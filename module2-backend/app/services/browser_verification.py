"""Proof of possession of an application-enrolled browser key."""

import base64
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta, timezone
from functools import lru_cache

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Device, User, UserRole
from ..models.base import utcnow
from ..models.browser_key_challenge import BrowserKeyChallenge
from ..repositories import DeviceRepository
from ..repositories.browser_key_challenge_repository import (
    BrowserKeyChallengeRepository, ENROLLMENT_ATTEMPT_WINDOW,
)
from .audit_service import AuditService
from .auth_service import AuthService
from .base import BaseService, transactional
from .exceptions import ConflictError, PermissionDeniedError, RateLimitedError, ValidationError

CHALLENGE_HEADER = "X-SAIV-Browser-Challenge"
SIGNATURE_HEADER = "X-SAIV-Browser-Signature"
CHALLENGE_TTL = timedelta(seconds=60)
ENROLLMENT_TTL = timedelta(minutes=2)
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")


class BrowserProofError(PermissionDeniedError):
    code = "browser_proof_required"


@dataclass(frozen=True)
class BrowserProof:
    challenge_id: str
    signature: str
    body_digest: str


@lru_cache(maxsize=4)
def _claim_engine(url):
    # A busy lecture can occupy the request pool while the request sessions
    # hold device locks. Reserve a separate pool for the short durable claim
    # so those requests cannot wait on connections held by one another.
    return create_engine(url, pool_size=4, max_overflow=0, pool_pre_ping=True)


def base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode(value: str, size: int) -> bytes:
    if len(value) != (size * 8 + 5) // 6 or not _BASE64URL.fullmatch(value):
        raise ValueError("Invalid base64url length or alphabet")
    data = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if len(data) != size or base64url(data) != value:
        raise ValueError("Invalid base64url encoding")
    return data


def parse_public_key(value: str) -> tuple[ec.EllipticCurvePublicKey, str]:
    """Accept only canonical DER SPKI for a P-256 key, encoded as base64url."""
    try:
        raw = _decode(value, 91)
        key = serialization.load_der_public_key(raw)
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError("Invalid key type or curve")
        if key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) != raw:
            raise ValueError("Non-canonical public key")
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise ValidationError("A valid ECDSA P-256 public key is required") from exc
    return key, base64url(hashlib.sha256(raw).digest())


def verify_signature(key: ec.EllipticCurvePublicKey, signature: str, message: str) -> None:
    try:
        raw = _decode(signature, 64)
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        key.verify(der, message.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    except (ValueError, InvalidSignature) as exc:
        raise BrowserProofError("Browser signature could not be verified. Start a fresh attempt.") from exc


def _message(challenge: BrowserKeyChallenge, device: Device) -> str:
    return json.dumps([
        "saiv.browser-proof.v1", challenge.purpose, challenge.id, challenge.user_id,
        challenge.token_version, device.id, device.device_fingerprint, challenge.key_id,
        challenge.session_id, challenge.recovery_device_id, challenge.recovery_key_id,
        challenge.expires_at.replace(tzinfo=timezone.utc).isoformat(),
    ], separators=(",", ":"))


class BrowserVerificationService(BaseService):
    def __init__(self, session: Session) -> None:
        super().__init__(session)
        self.devices = DeviceRepository(session)
        self.challenges = BrowserKeyChallengeRepository(session)

    @staticmethod
    def _require_student(user: User) -> None:
        if user.role != UserRole.STUDENT or not user.is_active:
            raise PermissionDeniedError("Browser verification requires an active student account")

    @classmethod
    def _require_pilot(cls, user: User) -> None:
        cls._require_student(user)
        if not user.browser_verification_required:
            raise PermissionDeniedError("Browser verification is not enabled for this account")

    def _issue(self, user: User, device: Device, purpose: str, key_id: str, ttl: timedelta,
               *, session_id: str | None = None, recovery: Device | None = None) -> dict:
        self.challenges.discard_expired()
        challenge = BrowserKeyChallenge(
            id=secrets.token_urlsafe(32), purpose=purpose, user_id=user.id,
            token_version=user.token_version, device_id=device.id, key_id=key_id,
            session_id=session_id, recovery_device_id=recovery.id if recovery else None,
            recovery_key_id=recovery.browser_key_id if recovery else None,
            expires_at=utcnow() + ttl,
        )
        self.challenges.save(challenge)
        return {"challenge_id": challenge.id, "message": _message(challenge, device),
                "expires_at": challenge.expires_at.replace(tzinfo=timezone.utc)}

    def _load(self, user: User, challenge_id: str, purpose: str) -> BrowserKeyChallenge:
        try:
            _decode(challenge_id, 32)
        except ValueError as exc:
            raise BrowserProofError("Browser challenge is invalid, expired or already used.") from exc
        challenge = self.challenges.find_by_id(challenge_id)
        if (challenge is None or challenge.purpose != purpose or challenge.user_id != user.id
                or challenge.token_version != user.token_version or challenge.expires_at <= utcnow()
                or challenge.used_at is not None):
            raise BrowserProofError("Browser challenge is invalid, expired or already used.")
        return challenge

    def _consume(self, challenge: BrowserKeyChallenge) -> None:
        # Commit independently so an accepted proof cannot be replayed after
        # a password failure or a downstream attendance rollback.
        with Session(bind=_claim_engine(self.session.get_bind().url)) as claim_session, claim_session.begin():
            if not BrowserKeyChallengeRepository(claim_session).consume(challenge):
                raise BrowserProofError("Browser challenge is invalid, expired or already used.")

    @transactional
    def issue_enrollment(self, user: User, device_id: str, public_key: str,
                         recovery_device_id: str | None) -> dict:
        self._require_pilot(user)
        _, key_id = parse_public_key(public_key)
        device = self.devices.find_by_id(device_id)
        recovery = self.devices.find_by_id(recovery_device_id) if recovery_device_id else None
        self._check_enrollment_devices(user, device, recovery, recovery_device_id, key_id)
        return self._issue(user, device, "enroll", key_id, ENROLLMENT_TTL, recovery=recovery)

    @staticmethod
    def _check_enrollment_devices(user: User, device: Device | None, recovery: Device | None,
                                  recovery_id: str | None, key_id: str) -> None:
        if device is None or device.user_id != user.id or not device.is_active or device.revoked_at is not None:
            raise BrowserProofError("This browser registration is unavailable or revoked.")
        if recovery_id and (recovery is None or recovery.user_id != user.id
                            or not recovery.is_active or recovery.revoked_at is not None or not recovery.browser_key_id):
            raise BrowserProofError("The selected browser key is unavailable for recovery.")
        if device.browser_key_id and (recovery_id != device.id or device.browser_key_id == key_id):
            raise ConflictError("This browser already has a key. Recover it explicitly with a new key.")
        if recovery and recovery.browser_key_id == key_id:
            raise ConflictError("Recovery requires a different browser key.")

    @transactional
    def enroll(self, user: User, challenge_id: str, public_key: str,
               signature: str, password: str) -> Device:
        self._require_pilot(user)
        challenge = self._load(user, challenge_id, "enroll")
        key, key_id = parse_public_key(public_key)
        if key_id != challenge.key_id:
            raise BrowserProofError("The enrollment challenge does not match this key.")
        # Re-read the password and account version under the same lock that
        # guards credential changes for this account.
        # FOR NO KEY UPDATE serializes account changes while allowing
        # foreign-key checks against the account from other transactions.
        account = self.session.scalar(select(User).where(User.id == user.id)
                                      .with_for_update(key_share=True).execution_options(populate_existing=True))
        if account is None or account.token_version != challenge.token_version:
            raise BrowserProofError("Your account changed. Sign in again before enrolling.")
        self._require_pilot(account)
        ids = [challenge.device_id]
        if challenge.recovery_device_id:
            ids.append(challenge.recovery_device_id)
        devices = self.devices.lock_owned(user.id, *ids)
        device = devices.get(challenge.device_id)
        recovery = devices.get(challenge.recovery_device_id)
        self._check_enrollment_devices(user, device, recovery, challenge.recovery_device_id, key_id)
        if recovery and recovery.browser_key_id != challenge.recovery_key_id:
            raise ConflictError("The recovery key changed. Check your registrations and try again.")
        verify_signature(key, signature, _message(challenge, device))
        # Count password checks across workers even if Redis is unavailable.
        # The account lock serializes this check with all enrollment attempts.
        if self.challenges.enrollment_attempts(user.id) >= 5:
            raise RateLimitedError("Too many browser password confirmations. Try again later.",
                                   retry_after=int(ENROLLMENT_ATTEMPT_WINDOW.total_seconds()))
        self._consume(challenge)
        if not password or "\x00" in password or len(password) > 128 or not AuthService.verify_password(
            password, account.hashed_password
        ):
            raise BrowserProofError("Password confirmation failed. Check your password and try again.")

        if recovery is not None and recovery.id != device.id:
            self.devices.revoke(recovery, "replaced during browser key recovery")
        device.public_key = public_key
        device.browser_key_id = key_id
        device.public_key_created_at = utcnow()
        device.public_key_expires_at = None
        try:
            self.devices.touch_seen(device)
        except IntegrityError as exc:
            if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) != "uq_devices_browser_key_id":
                raise
            raise ConflictError("This public key is already registered. Use a new browser key.") from exc
        AuditService(self.session).record(
            "browser_key_recovered" if recovery else "browser_key_enrolled",
            user_id=user.id, device_id=device.id, resource_type="device", resource_id=device.id,
            details={"replaced_device_id": recovery.id} if recovery else None,
        )
        return device

    @transactional
    def issue_checkin(self, user: User, session_id: str, fingerprint: str) -> dict:
        self._require_pilot(user)
        device = self.devices.find_for_user(user.id, fingerprint)
        if device is None or not device.browser_key_usable:
            raise BrowserProofError("Enroll or recover a browser key before checking in.")
        return self._issue(user, device, "checkin", device.browser_key_id,
                           CHALLENGE_TTL, session_id=session_id)

    def verify_checkin(self, user: User, session_id: str, device: Device | None,
                       proof: BrowserProof | None) -> None:
        self._require_student(user)
        if device is None or not device.browser_key_usable or proof is None:
            raise BrowserProofError("This check-in requires proof from an enrolled browser key.")
        challenge = self._load(user, proof.challenge_id, "checkin")
        if (challenge.device_id != device.id or challenge.session_id != session_id
                or challenge.key_id != device.browser_key_id):
            raise BrowserProofError("The browser challenge does not match this check-in.")
        key, key_id = parse_public_key(device.public_key)
        if key_id != device.browser_key_id:
            raise BrowserProofError("The browser key changed. Reopen check-in and try again.")
        verify_signature(key, proof.signature, f"{_message(challenge, device)}\n{proof.body_digest}")

        # The outer transaction holds the device lock through the write.
        self._consume(challenge)
