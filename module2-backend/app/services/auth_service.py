"""Registration, authentication and JWT issuance."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from ..models import AuditAction, User, UserRole
from ..models.base import utcnow
from ..repositories import UserRepository
from .audit_service import AuditService
from .base import BaseService, transactional
from .exceptions import (
    AuthenticationError,
    BusinessRuleError,
    ConflictError,
    PermissionDeniedError,
    ValidationError,
)

# Cost factor 10 is the documented minimum; 12 is recommended for production.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=10)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 7
MIN_PASSWORD_LENGTH = 8


class AuthService(BaseService):
    def __init__(self, session, secret_key: Optional[str] = None) -> None:
        super().__init__(session)
        self.users = UserRepository(session)
        self.audit = AuditService(session)
        # Injected for tests; falls back to the environment in production.
        # Deliberately no hardcoded default — an unset SECRET_KEY must fail
        # loudly rather than silently sign tokens anyone can forge.
        self._secret_key = secret_key or os.environ.get("SECRET_KEY")

    @property
    def secret_key(self) -> str:
        if not self._secret_key:
            raise RuntimeError(
                "SECRET_KEY is not set; refusing to issue or verify tokens"
            )
        return self._secret_key

    # -- passwords ----------------------------------------------------------

    @staticmethod
    def hash_password(password: str) -> str:
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(password: str, hashed: str) -> bool:
        return pwd_context.verify(password, hashed)

    @staticmethod
    def validate_password(password: str) -> None:
        if len(password or "") < MIN_PASSWORD_LENGTH:
            raise ValidationError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
            )

    # -- registration -------------------------------------------------------

    @transactional
    def register(
        self,
        email: str,
        full_name: str,
        password: str,
        role: UserRole = UserRole.STUDENT,
        *,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> User:
        self.validate_password(password)
        email = email.strip().lower()
        if self.users.exists_by_email(email):
            # 400, not 409. API-SPECIFICATION.md documents "Email already
            # registered" under 400 Bad Request for this endpoint, and the public
            # test asserts 400.
            raise BusinessRuleError(f"A user with email {email} already exists")

        user = self.users.save(
            User(
                email=email,
                full_name=full_name.strip(),
                hashed_password=self.hash_password(password),
                role=role,
            )
        )
        self.audit.record(
            AuditAction.USER_CREATED,
            user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
            details={"role": role.value},
        )
        return user

    # -- authentication -----------------------------------------------------

    @transactional
    def authenticate(
        self,
        email: str,
        password: str,
        *,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> User:
        """Verify credentials and record the attempt either way.

        The failure path is deliberately indistinguishable between "no such
        user" and "wrong password", so the endpoint cannot be used to enumerate
        registered addresses. Note the password is still verified against a
        dummy hash when the user is missing, to keep the timing comparable.
        """
        user = self.users.find_by_email(email)
        if user is None:
            # Constant-ish work so a missing user is not measurably faster.
            pwd_context.dummy_verify()
            self._record_failed_login(email, ip_address, user_agent, "no_such_user")
            raise AuthenticationError("Incorrect email or password")

        if not self.verify_password(password, user.hashed_password):
            self._record_failed_login(email, ip_address, user_agent, "bad_password",
                                      user_id=user.id)
            raise AuthenticationError("Incorrect email or password")

        if not user.is_active:
            self._record_failed_login(email, ip_address, user_agent, "inactive",
                                      user_id=user.id)
            raise PermissionDeniedError("This account has been deactivated")

        self.users.touch_last_login(user)
        self.audit.record(
            AuditAction.LOGIN_SUCCESS,
            user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return user

    def _record_failed_login(
        self, email: str, ip: Optional[str], ua: Optional[str],
        reason: str, user_id: Optional[str] = None,
    ) -> None:
        # Recorded outside this transaction: authenticate() raises immediately
        # after, and the resulting rollback would otherwise erase the very
        # entries brute-force detection depends on.
        self.audit.record_independently(
            AuditAction.LOGIN_FAILED,
            user_id=user_id,
            resource_type="user",
            ip_address=ip,
            user_agent=ua,
            details={"email": email, "reason": reason},
            success=False,
        )

    @transactional
    def logout(self, user: User, *, ip_address: Optional[str] = None) -> None:
        """Sign the user out everywhere.

        Deliberately global rather than per-token. The access token presented
        here is one of several a user may hold -- phone, laptop, an old tab --
        and only this one is even visible to us. Retiring just it would leave
        the others working while telling the user they had logged out, which
        is worse than not offering logout at all: someone logging out on a
        shared machine because they are worried is exactly who must not be
        left with a live session elsewhere.
        """
        user.token_version = int(user.token_version or 0) + 1
        user.updated_at = utcnow()
        self.session.flush()
        self.audit.record(
            AuditAction.LOGOUT, user_id=user.id, resource_type="user",
            resource_id=user.id, ip_address=ip_address,
            details={"token_version": user.token_version},
        )

    # -- tokens -------------------------------------------------------------

    def create_access_token(self, user: User) -> str:
        return self._encode(user, timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES), "access")

    def create_refresh_token(self, user: User) -> str:
        return self._encode(user, timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS), "refresh")

    def issue_tokens(self, user: User) -> Dict[str, Any]:
        """The ``/auth/login`` response payload, minus the serialised user."""
        return {
            "access_token": self.create_access_token(user),
            "refresh_token": self.create_refresh_token(user),
            "token_type": "bearer",
        }

    def _encode(self, user: User, ttl: timedelta, token_type: str) -> str:
        # Timezone-AWARE UTC here, deliberately, unlike everywhere else in the
        # codebase. JWT `iat`/`exp` are Unix epoch seconds, and
        # `naive.timestamp()` interprets the value as LOCAL time — on a machine
        # at UTC+8 that backdates every token by 8 hours, so a 1-hour token is
        # born 7 hours expired. Only aware datetimes convert correctly.
        now = datetime.now(timezone.utc)
        claims = {
            "sub": user.id,
            "email": user.email,
            "role": user.role.value if hasattr(user.role, "value") else str(user.role),
            "type": token_type,
            # The user's logout counter at the moment of issue. Checked on
            # every authenticated request, so incrementing it server-side
            # retires every token already in circulation for this user.
            "ver": int(user.token_version or 0),
            "iat": int(now.timestamp()),
            "exp": int((now + ttl).timestamp()),
        }
        return jwt.encode(claims, self.secret_key, algorithm=ALGORITHM)

    def decode_token(self, token: str, expected_type: str = "access") -> Dict[str, Any]:
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[ALGORITHM])
        except JWTError as exc:
            raise AuthenticationError("Invalid or expired token") from exc
        if payload.get("type") != expected_type:
            # Without this, a refresh token would be accepted as a bearer token
            # and quietly extend session lifetime to seven days.
            raise AuthenticationError(f"Expected a {expected_type} token")
        return payload

    @staticmethod
    def _assert_token_current(payload: Dict[str, Any], user: User) -> None:
        """Reject a token minted before the user's most recent logout.

        A JWT is self-contained: the server does not store it and cannot take
        it back, so logging out can only mean "stop honouring what is already
        out there". Comparing the token's `ver` claim against the user's
        current counter does exactly that, for every token on every device, at
        the cost of one integer and no session table.

        A token with no `ver` at all predates this check and is refused rather
        than trusted — so deploying this signs everyone out once, by design.
        """
        if payload.get("ver") != int(user.token_version or 0):
            raise AuthenticationError(
                "This session has been signed out. Please sign in again.")

    def current_user(self, token: str) -> User:
        payload = self.decode_token(token, expected_type="access")
        user = self.users.find_by_id(payload.get("sub", ""))
        if user is None:
            raise AuthenticationError("Token refers to a user that no longer exists")
        if not user.is_active:
            raise PermissionDeniedError("This account has been deactivated")
        self._assert_token_current(payload, user)
        return user

    def refresh(self, refresh_token: str) -> Dict[str, Any]:
        payload = self.decode_token(refresh_token, expected_type="refresh")
        user = self.users.find_by_id(payload.get("sub", ""))
        if user is None or not user.is_active:
            raise AuthenticationError("Cannot refresh this token")
        # Checked here too, and it matters more than on the access token: a
        # refresh token lives for days, so without this a logged-out client
        # could mint itself a fresh access token and carry on indefinitely.
        self._assert_token_current(payload, user)
        return self.issue_tokens(user)

    # -- consent ------------------------------------------------------------

    @transactional
    def update_consent(
        self, user: User, *, camera: Optional[bool] = None,
        geolocation: Optional[bool] = None,
    ) -> User:
        """Backs ``PATCH /auth/me``.

        Withdrawing camera consent also clears the enrolled face hash: keeping
        it would mean retaining biometric-derived data after consent was
        revoked.
        """
        if camera is not None:
            user.camera_consent = camera
            if camera is False:
                user.face_embedding_hash = None
                user.face_enrolled = False
        if geolocation is not None:
            user.geolocation_consent = geolocation
        user.updated_at = utcnow()
        self.session.flush()
        self.audit.record(
            AuditAction.USER_UPDATED, user_id=user.id, resource_type="user",
            resource_id=user.id,
            details={"camera_consent": user.camera_consent,
                     "geolocation_consent": user.geolocation_consent},
        )
        return user
