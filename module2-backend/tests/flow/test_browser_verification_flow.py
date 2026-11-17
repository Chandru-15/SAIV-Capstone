"""Browser proof through FastAPI and an isolated PostgreSQL database."""

import hashlib
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CheckInAttempt, Device, User
from app.models.base import utcnow
from app.models.browser_key_challenge import BrowserKeyChallenge
from app.services.browser_verification import CHALLENGE_HEADER, SIGNATURE_HEADER, base64url


class SigningKey:
    def __init__(self):
        self.private = ec.generate_private_key(ec.SECP256R1())
        spki = self.private.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.public = base64url(spki)
        self.id = base64url(hashlib.sha256(spki).digest())

    def sign(self, message):
        r, s = decode_dss_signature(self.private.sign(message.encode(), ec.ECDSA(hashes.SHA256())))
        return base64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def register(client, actor, **fields):
    response = client.post("/devices/", json={
        "device_fingerprint": str(uuid4()), "platform": "web", **fields,
    }, headers=actor.headers)
    assert response.status_code == 201, response.text
    return response.json()


def enrollment_payload(client, actor, device, key, *, recovery=None, password="password123"):
    response = client.post("/devices/browser-key/challenge", json={
        "device_id": device["id"], "public_key": key.public, "recovery_device_id": recovery,
    }, headers=actor.headers)
    assert response.status_code == 201, response.text
    challenge = response.json()
    return {"challenge_id": challenge["challenge_id"], "public_key": key.public,
            "signature": key.sign(challenge["message"]), "password": password}


def finish(client, actor, payload):
    return client.post("/devices/browser-key/enroll", json=payload, headers=actor.headers)


def bind(client, actor, device, *, recovery=None):
    key = SigningKey()
    response = finish(client, actor, enrollment_payload(client, actor, device, key, recovery=recovery))
    assert response.status_code == 200, response.text
    assert response.json()["browser_key_id"] == key.id
    assert "public_key" not in response.json()
    return key


def challenge_for(client, actor, session, device):
    response = client.post("/checkins/browser-challenge", json={
        "session_id": session["id"], "device_fingerprint": device["device_fingerprint"],
    }, headers=actor.headers)
    assert response.status_code == 201, response.text
    return response.json()


def signed_request(actor, challenge, key, body):
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    digest = base64url(hashlib.sha256(raw).digest())
    return raw, {**actor.headers, "Content-Type": "application/json",
                 CHALLENGE_HEADER: challenge["challenge_id"],
                 SIGNATURE_HEADER: key.sign(f'{challenge["message"]}\n{digest}')}


@pytest.fixture
def pilot(db, enrolled_student):
    db.get(User, enrolled_student.id).browser_verification_required = True
    db.commit()
    return enrolled_student


def test_pilot_rejects_missing_proof_and_unsigned_legacy_registration(client, pilot, make_session, db):
    session = make_session()
    me = client.get("/auth/me", headers=pilot.headers)
    assert me.json()["browser_verification_required"] is True
    client.patch("/auth/me", json={"browser_verification_required": False}, headers=pilot.headers)
    device = register(client, pilot, public_key=SigningKey().public)
    assert device["browser_key_id"] is None
    assert db.get(Device, device["id"]).public_key == ""
    for fingerprint in (None, device["device_fingerprint"], str(uuid4())):
        response = client.post("/checkins/", json={
            "session_id": session["id"], "device_fingerprint": fingerprint,
            "browser_verification_required": False, "browser_key_verified": True,
        }, headers=pilot.headers)
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "browser_proof_required"
    for header in (CHALLENGE_HEADER, SIGNATURE_HEADER):
        response = client.post("/checkins/", json={"session_id": session["id"]},
                               headers={**pilot.headers, header: "x"})
        assert response.status_code == 403
    duplicate = client.post("/checkins/", json={
        "session_id": session["id"], "device_fingerprint": device["device_fingerprint"],
    }, headers=[*pilot.headers.items(), (CHALLENGE_HEADER, "x"), (CHALLENGE_HEADER, "y"), (SIGNATURE_HEADER, "z")])
    assert duplicate.status_code == 403
    assert client.post("/checkins", json={"session_id": session["id"]}, headers=pilot.headers).status_code == 403
    assert db.scalar(select(func.count()).select_from(CheckInAttempt)) == 0


def test_checkin_rechecks_pilot_policy_after_authentication(client, enrolled_student, make_session, engine):
    from app.services.browser_verification import BrowserProofError
    from app.services.checkin_service import CheckInService

    session = make_session()
    with Session(engine) as authenticated, Session(engine) as operator:
        actor = authenticated.get(User, enrolled_student.id)
        assert not actor.browser_verification_required
        operator.get(User, actor.id).browser_verification_required = True
        operator.commit()
        with pytest.raises(BrowserProofError):
            CheckInService(authenticated).submit(actor, session["id"])


def test_checkin_validation_does_not_reflect_raw_signed_evidence(client, pilot, make_session):
    device = register(client, pilot)
    key = bind(client, pilot, device)
    session = make_session()
    raw, headers = signed_request(pilot, challenge_for(client, pilot, session, device), key, {
        "session_id": session["id"], "device_fingerprint": device["device_fingerprint"],
        "challenge_frames": ["sensitive-frame"] * 31, "qr_code_secret": ["sensitive-code"],
    })
    response = client.post("/checkins/", content=raw, headers=headers)
    assert response.status_code == 422
    assert "sensitive" not in response.text
    assert {tuple(issue["loc"]) for issue in response.json()["detail"]} == {
        ("body", "challenge_frames"), ("body", "qr_code_secret"),
    }


def test_valid_proof_preserves_liveness_and_risk_and_records_each_attempt(
    client, pilot, make_session, passing_submission, db,
):
    device = register(client, pilot)
    key = bind(client, pilot, device)
    session = make_session(require_liveness_check=True)
    location = {"latitude": 1.2966, "longitude": 103.7764, "location_accuracy_meters": 5}
    payload = {"session_id": session["id"], "device_fingerprint": device["device_fingerprint"], **location}
    raw, headers = signed_request(pilot, challenge_for(client, pilot, session, device), key, payload)
    flagged = client.post("/checkins/", content=raw, headers=headers)
    assert flagged.status_code == 201, flagged.text
    assert flagged.json()["status"] == "flagged"
    body = passing_submission(pilot, session["id"], retry=True, device_fingerprint=device["device_fingerprint"])
    raw, headers = signed_request(pilot, challenge_for(client, pilot, session, device), key, body)
    approved = client.post("/checkins/", content=raw, headers=headers)
    assert approved.status_code == 201, approved.text
    assert approved.json()["id"] == flagged.json()["id"]
    assert approved.json()["status"] == "approved"
    assert approved.json()["browser_key_verified"] is True
    assert approved.json()["liveness_passed"] is True
    assert "attestation_failed" not in (approved.json()["risk_factors"] or "")
    attempts = db.scalars(select(CheckInAttempt).order_by(CheckInAttempt.attempt_number)).all()
    assert [(a.attempt_number, a.browser_key_verified) for a in attempts] == [(1, True), (2, True)]


def test_web_crypto_enrollment_and_signed_http_checkin(client, pilot, make_session):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the Web Crypto HTTP test")
    script = """
    (async () => {
      const { subtle } = require('node:crypto').webcrypto;
      const readline = require('node:readline');
      const encode = bytes => Buffer.from(bytes).toString('base64url');
      const key = await subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign', 'verify']);
      console.log(encode(await subtle.exportKey('spki', key.publicKey)));
      for await (const message of readline.createInterface({ input: process.stdin })) {
        const signature = await subtle.sign({ name: 'ECDSA', hash: 'SHA-256' },
          key.privateKey, new TextEncoder().encode(JSON.parse(message)));
        console.log(encode(signature));
      }
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """
    device = register(client, pilot)
    session = make_session(require_liveness_check=False, require_face_match=False)
    with subprocess.Popen([node, "-e", script], text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE) as signer:
        try:
            public = signer.stdout.readline().strip()
            assert len(public) == 122

            def sign(message):
                signer.stdin.write(json.dumps(message) + "\n")
                signer.stdin.flush()
                return signer.stdout.readline().strip()

            issue = client.post("/devices/browser-key/challenge", json={
                "device_id": device["id"], "public_key": public,
            }, headers=pilot.headers)
            assert issue.status_code == 201, issue.text
            enrollment = issue.json()
            accepted = finish(client, pilot, {
                "public_key": public, "challenge_id": enrollment["challenge_id"],
                "signature": sign(enrollment["message"]), "password": "password123",
            })
            assert accepted.status_code == 200, accepted.text
            challenge = challenge_for(client, pilot, session, device)
            raw = json.dumps({"session_id": session["id"], "device_fingerprint": device["device_fingerprint"],
                              "latitude": 1.2966, "longitude": 103.7764}, separators=(",", ":")).encode()
            digest = base64url(hashlib.sha256(raw).digest())
            result = client.post("/checkins/", content=raw, headers={
                **pilot.headers, "Content-Type": "application/json", CHALLENGE_HEADER: challenge["challenge_id"],
                SIGNATURE_HEADER: sign(f'{challenge["message"]}\n{digest}'),
            })
            assert result.status_code == 201, result.text
            assert result.json()["browser_key_verified"] is True
        finally:
            signer.stdin.close()
            try:
                signer.wait(timeout=5)
            except subprocess.TimeoutExpired:
                signer.kill()


def test_body_session_account_device_and_key_are_bound(client, pilot, make_session, make_actor, admin, course, db):
    device = register(client, pilot)
    key = bind(client, pilot, device)
    other = register(client, pilot)
    bind(client, pilot, other)
    session = make_session()
    second_session = make_session()
    challenge = challenge_for(client, pilot, session, device)
    payload = {"session_id": session["id"], "device_fingerprint": device["device_fingerprint"],
               "latitude": 1.2966, "qr_code_secret": "test-✓"}
    raw, headers = signed_request(pilot, challenge, key, payload)
    changed = raw.replace(b"1.2966", b"1.2967")
    assert client.post("/checkins/", content=changed, headers=headers).status_code == 403
    for delta in ({"session_id": second_session["id"]}, {"device_fingerprint": other["device_fingerprint"]},
                  {"device_fingerprint": None}):
        body, proof = signed_request(pilot, challenge, key, {**payload, **delta})
        assert client.post("/checkins/", content=body, headers=proof).status_code == 403
    body, proof = signed_request(pilot, challenge, SigningKey(), payload)
    assert client.post("/checkins/", content=body, headers=proof).status_code == 403
    # Another authenticated account cannot enroll against this device ID.
    outsider = make_actor("student")
    foreign = client.post("/devices/browser-key/challenge",
                          json={"device_id": device["id"], "public_key": key.public},
                          headers=outsider.headers)
    assert foreign.status_code == 403
    added = client.post("/admin/enrollments/", json={"student_id": outsider.id, "course_id": course["id"]},
                        headers=admin.headers)
    assert added.status_code == 201, added.text
    db.get(User, outsider.id).browser_verification_required = True
    db.commit()
    outsider_device = register(client, outsider)
    outsider_key = bind(client, outsider, outsider_device)
    outsider_body, outsider_proof = signed_request(outsider, challenge, outsider_key, {
        **payload, "device_fingerprint": outsider_device["device_fingerprint"],
    })
    assert client.post("/checkins/", content=outsider_body, headers=outsider_proof).status_code == 403
    # Invalid requests did not burn the original owner's challenge.
    assert client.post("/checkins/", content=raw, headers=headers).status_code == 201


def test_replay_and_concurrent_use_can_record_only_one_attempt(client, pilot, make_session, db):
    device = register(client, pilot)
    key = bind(client, pilot, device)
    session = make_session(require_liveness_check=True)
    raw, headers = signed_request(pilot, challenge_for(client, pilot, session, device), key, {
        "session_id": session["id"], "device_fingerprint": device["device_fingerprint"], "retry": True,
    })

    def submit():
        return client.post("/checkins/", content=raw, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(lambda _: submit(), range(2)))
    assert sorted(item.status_code for item in responses) == [201, 403], [item.text for item in responses]
    assert submit().status_code == 403
    assert db.scalar(select(func.count()).select_from(CheckInAttempt)) == 1


def test_challenge_claim_is_atomic_in_independent_processes(client, pilot, make_session, engine):
    device = register(client, pilot)
    bind(client, pilot, device)
    issued = challenge_for(client, pilot, make_session(), device)
    script = """
import os
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.repositories.browser_key_challenge_repository import BrowserKeyChallengeRepository

engine = create_engine(os.environ['TEST_DATABASE_URL'])
with Session(engine) as db, db.begin():
    repo = BrowserKeyChallengeRepository(db)
    challenge = repo.find_by_id(sys.argv[1])
    print('ready', flush=True)
    input()
    print(int(repo.consume(challenge)), flush=True)
"""
    processes = []
    try:
        for _ in range(2):
            processes.append(subprocess.Popen([sys.executable, "-c", script, issued["challenge_id"]],
                                              text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE))
        assert [worker.stdout.readline().strip() for worker in processes] == ["ready", "ready"]
        for worker in processes:
            worker.stdin.write("\n")
            worker.stdin.flush()
        results = [worker.communicate(timeout=15)[0].strip() for worker in processes]
        assert sorted(results) == ["0", "1"]
        assert all(worker.returncode == 0 for worker in processes)
    finally:
        for worker in processes:
            if worker.poll() is None:
                worker.kill()
            worker.communicate()


def test_retry_waits_for_staff_review_and_cannot_overwrite_it(client, pilot, make_session, admin, engine, monkeypatch):
    from app.repositories.checkin_repository import CheckInRepository

    device = register(client, pilot)
    key = bind(client, pilot, device)
    session = make_session(require_liveness_check=True)
    body = {"session_id": session["id"], "device_fingerprint": device["device_fingerprint"]}
    raw, headers = signed_request(pilot, challenge_for(client, pilot, session, device), key, body)
    first = client.post("/checkins/", content=raw, headers=headers)
    assert first.status_code == 201 and first.json()["status"] == "flagged"
    raw, headers = signed_request(pilot, challenge_for(client, pilot, session, device), key, {**body, "retry": True})
    waiting = Event()
    find = CheckInRepository.find_by_session_and_student

    def signal_retry(repo, *args, **kwargs):
        if kwargs.get("lock"):
            waiting.set()
        return find(repo, *args, **kwargs)

    monkeypatch.setattr(CheckInRepository, "find_by_session_and_student", signal_retry)
    with ThreadPoolExecutor(max_workers=1) as workers, Session(engine) as reviewer:
        repo = CheckInRepository(reviewer)
        row = repo.lock_for_update(first.json()["id"])
        repo.reject(row, reviewer_id=admin.id, notes="Reviewed by staff")
        retry = workers.submit(client.post, "/checkins/", content=raw, headers=headers)
        assert waiting.wait(10)
        reviewer.commit()
        refused = retry.result(timeout=10)
        assert refused.status_code == 400, refused.text
        assert "decided by staff" in refused.text
        assert len(repo.find_attempts(row.id)) == 1


def test_expiry_and_downstream_failure_do_not_allow_replay(client, pilot, make_session, db):
    device = register(client, pilot)
    key = bind(client, pilot, device)
    session = make_session(require_liveness_check=True)
    expired = challenge_for(client, pilot, session, device)
    db.get(BrowserKeyChallenge, expired["challenge_id"]).expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    body = {"session_id": session["id"], "device_fingerprint": device["device_fingerprint"],
            "challenge_id": "invalid-liveness-challenge", "challenge_frames": ["frame"]}
    raw, headers = signed_request(pilot, expired, key, body)
    assert client.post("/checkins/", content=raw, headers=headers).status_code == 403
    raw, headers = signed_request(pilot, challenge_for(client, pilot, session, device), key, body)
    assert client.post("/checkins/", content=raw, headers=headers).status_code == 400
    assert client.post("/checkins/", content=raw, headers=headers).status_code == 403
    assert db.scalar(select(func.count()).select_from(CheckInAttempt)) == 0


def test_password_confirmation_enrollment_replay_and_rate_limit(client, pilot):
    device = register(client, pilot)
    key = SigningKey()
    for _ in range(5):
        payload = enrollment_payload(client, pilot, device, key, password="incorrect-password")
        failed = finish(client, pilot, payload)
        assert failed.status_code == 403 and "Password confirmation failed" in failed.text
    replay = finish(client, pilot, {**payload, "password": "password123"})
    assert replay.status_code == 403 and "already used" in replay.text
    limited = finish(client, pilot, enrollment_payload(client, pilot, device, key))
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "900"


def test_invalid_enrollment_does_not_echo_password_and_requires_student(client, pilot, instructor):
    response = finish(client, pilot, {"password": "must-not-echo-this"})
    assert response.status_code == 422
    assert "must-not-echo-this" not in response.text
    device = register(client, instructor)
    response = client.post("/devices/browser-key/challenge", json={
        "device_id": device["id"], "public_key": SigningKey().public,
    }, headers=instructor.headers)
    assert response.status_code == 403


def test_ordinary_registration_cannot_replace_key_or_reactivate_revoked_browser(client, pilot, db):
    device = register(client, pilot)
    key = bind(client, pilot, device)
    fields = {"device_fingerprint": device["device_fingerprint"], "public_key": SigningKey().public}
    reread = register(client, pilot, **fields)
    assert reread["browser_key_id"] == key.id
    assert db.get(Device, device["id"]).public_key == key.public
    assert client.delete(f'/devices/{device["id"]}', headers=pilot.headers).status_code == 200
    assert client.post("/devices/", json=fields, headers=pilot.headers).status_code == 403
    recovery = client.post("/devices/browser-key/challenge", json={
        "device_id": register(client, pilot)["id"], "public_key": SigningKey().public,
        "recovery_device_id": device["id"],
    }, headers=pilot.headers)
    assert recovery.status_code == 403


def test_expired_key_refuses_proof_and_remains_recoverable(client, pilot, make_session, db):
    device = register(client, pilot)
    key = bind(client, pilot, device)
    session = make_session()
    issued = challenge_for(client, pilot, session, device)
    db.get(Device, device["id"]).public_key_expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    status = client.get("/devices/my-devices", headers=pilot.headers).json()[0]
    assert status["browser_key_id"] == key.id and status["browser_key_usable"] is False
    raw, headers = signed_request(pilot, issued, key, {
        "session_id": session["id"], "device_fingerprint": device["device_fingerprint"],
    })
    assert client.post("/checkins/", content=raw, headers=headers).status_code == 403
    assert client.post("/checkins/browser-challenge", json={
        "session_id": session["id"], "device_fingerprint": device["device_fingerprint"],
    }, headers=pilot.headers).status_code == 403
    bind(client, pilot, device, recovery=device["id"])
    assert client.get("/devices/my-devices", headers=pilot.headers).json()[0]["browser_key_usable"] is True


def test_recovery_rotates_in_place_or_revokes_old_device_after_storage_loss(client, pilot, make_session):
    device = register(client, pilot)
    first_key = bind(client, pilot, device)
    session = make_session()
    old = challenge_for(client, pilot, session, device)
    replacement_key = bind(client, pilot, device, recovery=device["id"])
    assert replacement_key.id != first_key.id
    raw, headers = signed_request(pilot, old, first_key, {
        "session_id": session["id"], "device_fingerprint": device["device_fingerprint"],
    })
    assert client.post("/checkins/", content=raw, headers=headers).status_code == 403
    new_device = register(client, pilot)
    new_key = bind(client, pilot, new_device, recovery=device["id"])
    devices = client.get("/devices/my-devices", headers=pilot.headers).json()
    assert {d["id"]: d["is_active"] for d in devices} == {device["id"]: False, new_device["id"]: True}
    raw, headers = signed_request(pilot, challenge_for(client, pilot, session, new_device), new_key, {
        "session_id": session["id"], "device_fingerprint": new_device["device_fingerprint"],
    })
    assert client.post("/checkins/", content=raw, headers=headers).status_code == 201


def test_logout_invalidates_outstanding_proof_and_legacy_still_works(
    client, pilot, make_session, db,
):
    device = register(client, pilot)
    key = bind(client, pilot, device)
    session = make_session()
    raw, headers = signed_request(pilot, challenge_for(client, pilot, session, device), key, {
        "session_id": session["id"], "device_fingerprint": device["device_fingerprint"],
    })
    assert client.post("/auth/logout", headers=pilot.headers).status_code in (200, 204)
    assert client.post("/checkins/", content=raw, headers=headers).status_code == 401
    auth = client.post("/auth/login", json={"email": pilot.email, "password": "password123"}).json()
    headers["Authorization"] = f'Bearer {auth["access_token"]}'
    assert client.post("/checkins/", content=raw, headers=headers).status_code == 403
    db.get(User, pilot.id).browser_verification_required = False
    db.commit()
    legacy = client.post("/checkins/", json={"session_id": session["id"]}, headers={
        "Authorization": headers["Authorization"],
    })
    assert legacy.status_code == 201
    assert legacy.json()["browser_key_verified"] is False
    disabled = client.post("/devices/browser-key/challenge", json={
        "device_id": device["id"], "public_key": SigningKey().public,
    }, headers={"Authorization": headers["Authorization"]})
    assert disabled.status_code == 403
