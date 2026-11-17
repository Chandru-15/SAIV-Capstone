"""
SAIV Backend API - Module 2

Layering, outermost first:

    controllers  ->  services  ->  repositories  ->  models  ->  db/schema.sql
      HTTP           business        queries         ORM          DDL
                     rules +
                     transactions

Each layer may only call the one beneath it. A controller never writes a
``select()``; a service never imports FastAPI; a repository never commits.

See docs/API-SPECIFICATION.md for endpoint documentation.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .controllers import api_router
from .dependencies import rate_limit_api
from .metrics import install_metrics
from .services.exceptions import ServiceError

logger = logging.getLogger(__name__)

# The placeholder docker-compose.yml falls back to, so a clean clone boots
# without a .env. It is in the repository, so it is public.
_PLACEHOLDER_SECRET_KEYS = frozenset({
    "your-secret-key-change-in-production",
    "change-me",
    "secret",
})
# HS256 signs with the key itself, so a short key is the whole attack surface.
# 32 characters is the floor; .env.example suggests token_urlsafe(48), which
# gives 64.
_MIN_SECRET_KEY_LENGTH = 32


def _warn_about_weak_secret_key(secret_key: str) -> None:
    """Say so loudly, but still boot.

    A weak signing key is not a missing one. Anyone who knows it can mint a
    token for any user, including an admin, so this needs saying where somebody
    will see it -- but refusing to start would mean a fresh clone of this
    repository does not come up at all, because the compose default IS the
    placeholder below. That trade is deliberate: convenience for a first run,
    noise in the log for as long as it lasts.
    """
    if secret_key in _PLACEHOLDER_SECRET_KEYS:
        logger.warning(
            "SECRET_KEY is the placeholder from docker-compose.yml, which is "
            "published in this repository. Anyone can forge a token for any "
            "user, including an admin. Set a real one in .env before this "
            "deployment holds anything that matters: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"')
    elif len(secret_key) < _MIN_SECRET_KEY_LENGTH:
        logger.warning(
            "SECRET_KEY is %d characters; %d is the minimum worth having. "
            "HS256 signs with the key itself, so a short key is guessable and "
            "every token is forgeable. Replace it with: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"',
            len(secret_key), _MIN_SECRET_KEY_LENGTH)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Fail at boot, not at the first login.

    Without SECRET_KEY every token operation raises, and the client sees an
    opaque 500 on /auth/login with nothing explaining why. A missing signing
    key is a deployment fault, so surface it where the message is unmissable.
    docker-compose already provides it.
    """
    secret_key = os.environ.get("SECRET_KEY")
    if not secret_key:
        raise RuntimeError(
            "SECRET_KEY is not set. The API cannot sign or verify JWTs without it. "
            "Set it in the environment (docker-compose.yml already does)."
        )
    _warn_about_weak_secret_key(secret_key)
    _ensure_assignment_tables()
    closer = asyncio.create_task(_auto_close_loop()) if AUTO_CLOSE_SECONDS > 0 else None
    yield
    if closer is not None:
        closer.cancel()


# Opening a session is always a deliberate act by its instructor, TA or an
# admin. Closing is too, but a session somebody forgot to close must not stay
# "active" forever: once both its scheduled end and its check-in window have
# passed, it is closed automatically (and the audit trail says so).
AUTO_CLOSE_SECONDS = int(os.environ.get("SESSION_AUTO_CLOSE_SECONDS", "60"))


async def _auto_close_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(close_finished_sessions)
        except Exception:
            logger.exception("auto-close pass failed")
        await asyncio.sleep(AUTO_CLOSE_SECONDS)


def close_finished_sessions() -> int:
    from sqlalchemy import select

    from .database import SessionLocal
    from .models import AttendanceSession, SessionStatus
    from .models.base import utcnow
    from .services.audit_service import AuditService

    now = utcnow()
    with SessionLocal() as db:
        finished = list(db.scalars(select(AttendanceSession).where(
            AttendanceSession.status == SessionStatus.ACTIVE,
            AttendanceSession.scheduled_end < now,
            AttendanceSession.checkin_closes_at < now,
        )))
        audit = AuditService(db)
        for session in finished:
            session.status = SessionStatus.CLOSED
            session.actual_end = session.actual_end or now
            audit.record("session_auto_closed", resource_type="session", resource_id=session.id,
                         details={"scheduled_end": session.scheduled_end})
        db.commit()
    if finished:
        logger.info("auto-closed %d finished session(s)", len(finished))
    return len(finished)


def _ensure_assignment_tables() -> None:
    """Create the staff-assignment, group, password-reset-code and check-in
    attempt tables on a database that predates them.

    Idempotent (CREATE ... IF NOT EXISTS semantics via checkfirst) and limited
    to an explicit list, so it can never alter the schema.sql-owned tables.

    Note the limit: ``create_all`` adds missing TABLES, never missing COLUMNS.
    ``users.token_version`` therefore cannot be repaired here — it is added by
    the idempotent ALTER in db/schema.sql, which the compose ``migrate``
    service re-runs on every startup.
    """
    import time

    from .database import engine
    from .models import (
        CheckInAttempt,
        CourseGroup,
        CourseInstructor,
        CourseSettings,
        FaceReEnrollRequest,
        GroupMember,
        PasswordResetCode,
        SessionGroup,
        SessionTA,
    )

    tables = [CourseInstructor.__table__, SessionTA.__table__, CourseSettings.__table__,
              CourseGroup.__table__, GroupMember.__table__, SessionGroup.__table__,
              PasswordResetCode.__table__,
              FaceReEnrollRequest.__table__,
              # checkin_attempts is also declared in db/schema.sql; listed here
              # so a database created before it exists gains the table at boot
              # rather than failing every check-in until schema.sql is re-run.
              CheckInAttempt.__table__]
    for attempt in range(1, 6):
        try:
            CourseInstructor.metadata.create_all(engine, tables=tables, checkfirst=True)
            return
        except Exception as exc:  # database still starting up
            logger.warning("assignment tables not created (attempt %d/5): %s",
                           attempt, type(exc).__name__)
            time.sleep(2)
    logger.error("Could not create staff assignment tables; run "
                 "db/migrations/001_staff_assignments.sql by hand.")


app = FastAPI(
    title="SAIV Backend API",
    description="Secure Attendance & Identity Verification System",
    version="1.0.0",
    lifespan=lifespan,
)

# Which browser origins may read our responses. An allowlist from the
# environment rather than ["*"]: a wildcard is invalid per the CORS spec once
# allow_credentials is set, and Starlette compensates by echoing whatever
# Origin asked, which lets any site on the internet make credentialed calls.
# Tunnel and staging origins belong in ALLOWED_ORIGINS, not in the default.
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registered after the CORS middleware, which means it sits OUTSIDE it and so
# also counts preflight responses. See app/metrics.py for the one rule that
# matters here: label with the route template, never the request path.
install_metrics(app)


# =============================================================================
# Exception handling
# =============================================================================
# The equivalent of Spring's @ControllerAdvice. Services raise domain errors
# that know nothing about HTTP; this is the single place they become responses,
# so no controller needs a try/except and no service needs HTTPException.


@app.exception_handler(ServiceError)
async def handle_service_error(request: Request, exc: ServiceError) -> JSONResponse:
    headers = {}
    if exc.status_code == 401:
        # Required by RFC 9110 for a 401; clients rely on it to know how to
        # authenticate rather than guessing.
        headers["WWW-Authenticate"] = "Bearer"
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        # RFC 9110 again: a 429 should say how long to wait, so a client
        # backs off on instruction instead of hammering the endpoint.
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            # `detail` is the key the API specification documents for errors
            # (e.g. {"detail": "Invalid input data"}) and the one FastAPI's own
            # HTTPException emits, so clients and the public tests both look
            # for it. `code`, `message` and `details` are kept alongside it:
            # `code` is machine-readable, which a bare string is not.
            "detail": exc.message,
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
        },
        headers=headers,
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Anything unplanned becomes an opaque 500.

    The traceback goes to the log, never to the client: exception text can leak
    table names, file paths and query fragments.
    """
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred",
                 "code": "internal_error",
                 "message": "An unexpected error occurred", "details": None},
    )


@app.get("/health", tags=["Health"])
async def health_check():
    """Basic health check endpoint."""
    return {"status": "healthy"}


# =============================================================================
# Routers
# =============================================================================
# Everything is served under /api/v1, per the base URL in
# docs/API-SPECIFICATION.md ("http://localhost:8000/api/v1"). The public test
# suite calls /api/v1/auth/register and friends, so this prefix is part of the
# contract, not a style choice.
#
# /health deliberately stays at the root: it is an infrastructure probe, not an
# API resource, and docker-compose healthchecks plus the public performance and
# dashboard tests all hit /health unversioned.
API_PREFIX = "/api/v1"

# /api/v1 + /auth /users /courses /sessions /checkins /audit /admin
#
# The router-wide dependency is the specification's blanket budget: 1000 API
# requests per hour per user. Attached here rather than to each router so a
# new endpoint is covered by default -- the failure mode of a per-router list
# is the endpoint somebody forgets. /health and /metrics sit outside this
# prefix and are deliberately uncounted: a probe that gets rate-limited looks
# exactly like an outage.
app.include_router(api_router, prefix=API_PREFIX,
                   dependencies=[Depends(rate_limit_api)])