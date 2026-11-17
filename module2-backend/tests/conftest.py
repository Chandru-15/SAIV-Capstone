"""Shared fixtures.

Two tiers of test:

* ``tests/unit`` — pure logic. No database, no network. Always runnable.
* ``tests/flow`` — the real stack: HTTP -> controller -> service -> repository
  -> PostgreSQL. Skipped with a clear message if no database is reachable.

Isolation between flow tests is by TRUNCATE, not by wrapping each test in a
transaction that is rolled back. That costs a few milliseconds per test but
keeps real commit semantics — which matters here, because the code under test
has opinions about transactions: ``@transactional`` commits, and
``AuditService.record_independently`` deliberately commits on a *second*
connection. A savepoint-based harness would quietly invalidate both.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest

# Must be set before app.services is imported anywhere.
os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = REPO_ROOT / "db" / "schema.sql"

# Explicit disposable PostgreSQL database only:
#   TEST_DATABASE_URL=postgresql+psycopg2://me@localhost:5432/saiv_test
# Must match app.main.API_PREFIX.
API_PREFIX = "/api/v1"

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

TABLES = (
    "audit_logs", "risk_signals", "browser_key_challenges", "checkin_attempts", "checkins", "devices",
    # Tables added for staff assignments, tutorial / lab groups and password
    # reset codes. TRUNCATE ... CASCADE would reach them through their foreign
    # keys anyway; listing them makes the reset explicit.
    "password_reset_codes", "face_reenroll_requests",
    "session_groups", "group_members", "course_groups",
    "course_settings", "session_tas", "course_instructors",
    "sessions", "enrollments", "courses", "users",
)


def utcnow() -> datetime:
    """Naive UTC, matching the storage convention in db/schema.sql."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def engine():
    """Engine for the test database, with the schema applied.

    Skips the whole flow suite — rather than erroring 40 times — when no
    database is reachable.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    if not TEST_DATABASE_URL:
        pytest.skip("Set TEST_DATABASE_URL to an explicitly disposable PostgreSQL database.")
    url = make_url(TEST_DATABASE_URL)
    if url.get_backend_name() != "postgresql" or not re.search(
        r"(?:^|_)(?:tests?|disposable|audit)(?:_|$)", url.database or "", re.IGNORECASE
    ):
        pytest.fail("Refusing destructive flow tests. Use a dedicated PostgreSQL test database.", pytrace=False)
    eng = create_engine(url, pool_pre_ping=True)
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(
            f"Disposable test database is unavailable ({type(exc).__name__}). "
            "Check the PostgreSQL instance configured in TEST_DATABASE_URL.",
            allow_module_level=True,
        )

    # The schema file is idempotent, so applying it to an existing database is
    # safe and keeps the tests honest: they run against the real DDL, not
    # against Base.metadata.create_all().
    with eng.begin() as conn:
        conn.execute(text(SCHEMA_SQL.read_text()))

    # The newer tables are not in schema.sql: main.py creates them at startup
    # (_ensure_assignment_tables). Create them here too, on THIS engine, so the
    # suite also works when TEST_DATABASE_URL points somewhere other than the
    # app's own DATABASE_URL.
    from app.models import (
        CourseGroup,
        CourseInstructor,
        CourseSettings,
        FaceReEnrollRequest,
        GroupMember,
        PasswordResetCode,
        SessionGroup,
        SessionTA,
    )

    CourseInstructor.metadata.create_all(eng, checkfirst=True, tables=[
        CourseInstructor.__table__, SessionTA.__table__, CourseSettings.__table__,
        CourseGroup.__table__, GroupMember.__table__, SessionGroup.__table__,
        PasswordResetCode.__table__, FaceReEnrollRequest.__table__,
    ])
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def clean_tables(request) -> Iterator[None]:
    """Empty every table before each flow test.

    Autouse, but a no-op for unit tests, which never request ``engine``.
    """
    if "engine" not in request.fixturenames:
        yield
        return
    from sqlalchemy import text

    eng = request.getfixturevalue("engine")
    with eng.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def db(engine):
    """A session bound to the test database, for tests that call services
    directly rather than through HTTP."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


# Flow requests arrive the way browser traffic does in the real deployment:
# through a reverse proxy, which appends the caller's address to
# X-Forwarded-For. Two things are needed to model that, because
# dependencies.client_ip now believes the header only when the CONNECTION came
# from a proxy the operator named:
#
#   * Starlette's TestClient leaves scope["client"] unset, so there is no peer
#     address at all. _with_peer injects one.
#   * That peer is named as the one trusted proxy, by the autouse fixture
#     below. Without it the header would be (correctly) ignored and every
#     IP-keyed test would land in the same bucket.
#
# See API-NOTES.md section 8.
TEST_PROXY_IP = "10.250.0.9"


def _with_peer(asgi_app, host: str = TEST_PROXY_IP, port: int = 54321):
    """Wrap an ASGI app so requests appear to come from ``host``."""

    async def wrapper(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "client": (host, port)}
        await asgi_app(scope, receive, send)

    return wrapper


@pytest.fixture(autouse=True)
def trusted_proxy(monkeypatch):
    """Trust the injected peer, for every test.

    A test that wants to pin the UNTRUSTED path overrides this with its own
    monkeypatch.setenv/delenv, which wins because it runs later.
    """
    monkeypatch.setenv("TRUSTED_PROXY_IPS", TEST_PROXY_IP)


@pytest.fixture
def client(engine, request):
    """TestClient wired to the test database.

    The dependency override is the only seam: the app, routers, services and
    repositories under test are exactly the ones that run in production.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app.database import get_db
    from app.main import app

    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    # Patch FaceService onto the module 3 stub. Required, not optional: the
    # client-supplied biometric fields were removed, so liveness and face match
    # can only arrive from module 3, and an un-stubbed FaceService would try to
    # reach a real container.
    from app.services.face_service import FaceService

    stub = request.getfixturevalue("face_stub")
    original_init = FaceService.__init__

    def patched_init(self, session, client=None):
        original_init(self, session, client=client or stub["client"])

    FaceService.__init__ = patched_init
    app.dependency_overrides[get_db] = override_get_db
    # raise_server_exceptions=False so a 500 is asserted as a response rather
    # than blowing up the test — that is what a real client would see.
    #
    # base_url carries the /api/v1 prefix: httpx concatenates base_url's path
    # with the request path, so tests keep writing "/auth/login" and it lands
    # on "/api/v1/auth/login". One line here instead of 300 edited paths, and
    # the prefix is asserted separately in test_api_prefix.py.
    try:
        with TestClient(_with_peer(app), base_url=f"http://testserver{API_PREFIX}",
                        raise_server_exceptions=False) as test_client:
            yield test_client
    finally:
        FaceService.__init__ = original_init
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Actors
# ---------------------------------------------------------------------------

DEFAULT_PASSWORD = "password123"


class Actor:
    """A registered, logged-in user plus the header to act as them."""

    def __init__(self, client, user: dict, token: str) -> None:
        self._client = client
        self.user = user
        self.token = token
        self.id = user["id"]
        self.email = user["email"]
        self.role = user["role"]

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def grant_consent(self, camera: bool = True, geolocation: bool = True) -> "Actor":
        self._client.patch(
            "/auth/me",
            json={"camera_consent": camera, "geolocation_consent": geolocation},
            headers=self.headers,
        )
        return self


@pytest.fixture
def make_actor(client):
    """Register and log in a user of the given role."""

    counter = {"n": 0}

    def _make(role: str = "student", email: str | None = None) -> Actor:
        counter["n"] += 1
        email = email or f"{role}{counter['n']}@example.edu"
        reg = client.post(
            "/auth/register",
            json={"email": email, "full_name": f"{role.title()} {counter['n']}",
                  "password": DEFAULT_PASSWORD, "role": role},
        )
        assert reg.status_code == 201, reg.text
        login = client.post("/auth/login",
                            json={"email": email, "password": DEFAULT_PASSWORD})
        assert login.status_code == 200, login.text
        return Actor(client, login.json()["user"], login.json()["access_token"])

    return _make


@pytest.fixture
def admin(make_actor) -> Actor:
    return make_actor("admin")


@pytest.fixture
def instructor(make_actor) -> Actor:
    return make_actor("instructor")


@pytest.fixture
def student(make_actor) -> Actor:
    return make_actor("student").grant_consent()


# ---------------------------------------------------------------------------
# Domain fixtures
# ---------------------------------------------------------------------------

# NTU, roughly. Used as the venue for every test course.
VENUE_LAT, VENUE_LON = 1.2966, 103.7764


@pytest.fixture
def course(client, admin, instructor) -> dict:
    """Created by an ADMIN: API-SPECIFICATION.md line 295 makes course
    creation admin-only. `instructor` is still requested so the same
    instructor exists for session ownership in dependent fixtures."""
    response = client.post(
        "/courses/",
        json={
            "code": "CS6101", "name": "Deep Learning", "semester": "AY2024-25 Sem 1",
            "venue_latitude": VENUE_LAT, "venue_longitude": VENUE_LON,
            "geofence_radius_meters": 100.0, "risk_threshold": 0.5,
        },
        headers=admin.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def make_session(client, instructor, course):
    """Create a session whose check-in window contains the present moment.

    Anchored to the real clock deliberately: the check-in endpoint has no way
    to inject a time, so a fixed date would put every window in the past.
    """

    def _make(*, active: bool = True, open_window: bool = True, **overrides) -> dict:
        now = utcnow()
        offset = timedelta(0) if open_window else timedelta(days=2)
        body = {
            "course_id": course["id"],
            "name": "Lecture 5",
            "scheduled_start": (now + offset).isoformat(),
            "scheduled_end": (now + offset + timedelta(hours=2)).isoformat(),
            "checkin_opens_at": (now + offset - timedelta(minutes=15)).isoformat(),
            "checkin_closes_at": (now + offset + timedelta(minutes=15)).isoformat(),
        }
        body.update(overrides)
        response = client.post("/sessions/", json=body, headers=instructor.headers)
        assert response.status_code == 201, response.text
        session = response.json()
        if active:
            admin_like = instructor.headers
            patched = client.patch(f"/admin/sessions/{session['id']}/status",
                                   json={"status": "active"}, headers=admin_like)
            assert patched.status_code == 200, patched.text
            session = patched.json()
        return session

    return _make


@pytest.fixture
def enrolled_student(client, admin, student, course) -> Actor:
    response = client.post(
        "/admin/enrollments/",
        json={"student_id": student.id, "course_id": course["id"]},
        headers=admin.headers,
    )
    assert response.status_code == 201, response.text
    return student


@pytest.fixture
def at_venue() -> dict:
    """A clean submission over the PASSIVE liveness path (a single image).

    No longer enough to be approved on its own. `require_liveness_check`
    defaults to true and now binds the backend rather than only the frontend,
    so a passive verdict leaves the check-in `flagged` with a
    `replay_suspected` signal however good its score -- see API-NOTES.md
    section 6. Everything else about this payload is clean, which is what makes
    it useful: it isolates the liveness path from the other checks.

    For a payload that is approved, use `passing_submission` below.
    """
    # `image` rather than client-supplied scores: the backend derives liveness
    # from module 3 (the stub returns a passing 0.93), which is the only path
    # that exists now.
    return {
        "latitude": VENUE_LAT, "longitude": VENUE_LON,
        "location_accuracy_meters": 5.0,
        "image": FAKE_IMAGE,
    }


@pytest.fixture
def passing_submission(client):
    """A submission payload that passes every check, challenge included.

    Returns a callable so the challenge can be minted per student and session:
    it is bound to both, expires in 60 seconds and is single-use, so it cannot
    be a module-level constant.

    Used by the tests that need an APPROVED outcome to test something else
    entirely -- audit trails, appeal windows, retry bookkeeping, attendance
    statistics. They each used `at_venue` until the liveness requirement began
    binding the backend; going through a real challenge keeps them testing what
    they are named for instead of re-testing the liveness gate.
    """
    def _submission(actor, session_id: str, **overrides) -> dict:
        issued = client.post(
            f"/checkins/liveness-challenge?session_id={session_id}",
            headers=actor.headers)
        assert issued.status_code == 201, issued.text
        challenge = issued.json()
        # Adapt to whichever type the server chose, rather than pinning the
        # rotation here. colour_reflection wants EXACTLY one frame per colour
        # per step, not a minimum, because the server reads each frame's
        # colour off its position. Keeping this in the fixture is what lets
        # every test that only wants an approved check-in stay indifferent to
        # the challenge type -- and means they all exercise the colour path
        # some of the time, for free.
        frames = FAKE_FRAMES
        if challenge.get("colour_sequence"):
            frames = [FAKE_IMAGE] * (len(challenge["colour_sequence"])
                                     * challenge["frames_per_step"])
        payload = {
            "session_id": session_id,
            "challenge_id": challenge["challenge_id"],
            "challenge_frames": frames,
            "latitude": VENUE_LAT, "longitude": VENUE_LON,
            "location_accuracy_meters": 5.0,
        }
        payload.update(overrides)
        return payload

    return _submission


# ---------------------------------------------------------------------------
# Module 3 (face recognition) stub
# ---------------------------------------------------------------------------
# Flow tests must not depend on a running module 3: MediaPipe is slow, the
# container is heavy, and its verdicts are not what module 2's tests are
# about. A MockTransport-backed FaceClient gives deterministic scores while
# exercising module 2's real client, service and controller code.

import base64

FAKE_IMAGE = base64.b64encode(b"a face image, honestly, trust me").decode()
FAKE_HASH = "f" * 64


@pytest.fixture
def face_stub():
    """Configurable fake module 3.

    Mutate ``behaviour`` inside a test to simulate a poor liveness score, a
    rejected image, or a total outage.
    """
    import httpx

    from app.clients import FaceClient

    behaviour = {
        "enroll_status": 201,
        "enroll_body": {"enrollment_successful": True,
                        "face_template_hash": FAKE_HASH,
                        "quality_score": 0.88,
                        "details": {"face_detected": True}},
        "liveness_body": {"liveness_passed": True, "liveness_score": 0.93,
                          "liveness_threshold": 0.60,
                          "face_embedding_hash": FAKE_HASH,
                          "details": {"challenge_passed": True,
                                      "frames_with_face": 8}},
        "verify_body": {"match_passed": True, "match_score": 0.84,
                        "match_threshold": 0.70, "face_detected": True,
                        "current_template_hash": FAKE_HASH},
        "down": False,
        "calls": [],
        "liveness_requests": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        behaviour["calls"].append(request.url.path)
        if request.url.path == "/liveness/check" and request.content:
            # Record what module 2 actually sent, so tests can assert the
            # server's challenge_type was used and not the client's.
            behaviour["liveness_requests"].append(_json.loads(request.content))
        if behaviour["down"]:
            raise httpx.ConnectError("module 3 is down", request=request)
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "healthy"})
        if path == "/face/enroll":
            return httpx.Response(behaviour["enroll_status"],
                                  json=behaviour["enroll_body"])
        if path == "/liveness/check":
            return httpx.Response(200, json=behaviour["liveness_body"])
        if path in ("/face/verify", "/face/match"):
            return httpx.Response(200, json=behaviour["verify_body"])
        return httpx.Response(404, json={"detail": "no such endpoint"})

    behaviour["client"] = FaceClient(base_url="http://face-stub:8001",
                                     transport=httpx.MockTransport(handler))
    return behaviour


@pytest.fixture
def client_with_face(engine, face_stub):
    """TestClient whose FaceService talks to the module 3 stub."""
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app.database import get_db
    from app.main import app
    from app.services.face_service import FaceService

    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    # Patch the client FaceService builds by default, so both the face
    # controller and the check-in pipeline reach the stub.
    original_init = FaceService.__init__

    def patched_init(self, session, client=None):
        original_init(self, session, client=client or face_stub["client"])

    FaceService.__init__ = patched_init
    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(_with_peer(app), base_url=f"http://testserver{API_PREFIX}",
                        raise_server_exceptions=False) as test_client:
            yield test_client
    finally:
        FaceService.__init__ = original_init
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def isolated_challenge_store(monkeypatch):
    """Give every test a fresh in-memory challenge store.

    Without this, the module-level store is shared across tests and a
    challenge minted in one leaks into the next. Also keeps the suite from
    reaching for Redis.
    """
    from app.services import checkin_service
    from app.services.liveness_challenge import InMemoryChallengeStore

    store = InMemoryChallengeStore()
    monkeypatch.setattr(checkin_service, "_CHALLENGE_STORE", store)
    monkeypatch.setattr(checkin_service, "_shared_challenge_store", lambda: store)
    return store


@pytest.fixture(autouse=True)
def isolated_rate_limits(monkeypatch):
    """Give every test its own rate-limit counters.

    Required, not merely tidy. The budgets are per IP or per user and the
    TestClient presents ONE address for the whole suite, so a shared store
    would have the eleventh ``make_actor`` in the session hit the 10/hour
    registration limit and every test after it fail for a reason unrelated to
    what it was testing. Also keeps the suite from reaching for Redis.

    Returned so a test can assert on the counters, and so a test that WANTS to
    exhaust a budget can do so without affecting its neighbours.
    """
    from app.services import rate_limit

    store = rate_limit.InMemoryCounterStore()
    monkeypatch.setattr(rate_limit, "_STORE", store)
    return store


FAKE_FRAMES = [FAKE_IMAGE] * 8
