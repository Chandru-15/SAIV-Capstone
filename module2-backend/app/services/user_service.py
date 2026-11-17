"""User administration and RBAC helpers."""

from __future__ import annotations

from datetime import timedelta
from typing import Dict, Iterable, List, Optional

from ..models import AuditAction, User, UserRole
from ..models.base import utcnow
from ..repositories import Page, UserRepository
from .audit_service import AuditService
from .base import BaseService, transactional
from .exceptions import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError

# "Forgot password": how long a request stays on the admin's list, how many
# requests one IP may make an hour, and the one-time email code's limits.
RESET_REQUEST_DAYS = 7
RESET_REQUESTS_PER_IP_PER_HOUR = 10
RESET_CODE_TTL_MINUTES = 10
RESET_CODE_MAX_ATTEMPTS = 5
RESET_CODE_RESEND_SECONDS = 60
RESET_CODES_PER_ACCOUNT_PER_HOUR = 5

# PII retention window from the schema's privacy requirements.
RETENTION_DAYS = 30

# Who may act on whom. Instructors and TAs can read users; only admins write.
_ADMIN_ONLY = (UserRole.ADMIN,)
_STAFF = (UserRole.ADMIN, UserRole.INSTRUCTOR, UserRole.TA)


class UserService(BaseService):
    def __init__(self, session) -> None:
        super().__init__(session)
        self.users = UserRepository(session)
        self.audit = AuditService(session)

    # -- authorisation ------------------------------------------------------

    @staticmethod
    def require_role(actor: User, *allowed: UserRole) -> None:
        """The single place role checks are spelled out.

        Routers call this rather than comparing roles inline, so a new role
        never means auditing every endpoint by hand.
        """
        if actor.role not in allowed:
            raise PermissionDeniedError(
                f"Requires one of: {', '.join(r.value for r in allowed)}"
            )

    @staticmethod
    def require_self_or_staff(actor: User, target_user_id: str) -> None:
        """Students may only reach their own records; staff may reach anyone's."""
        if actor.id != target_user_id and actor.role not in _STAFF:
            raise PermissionDeniedError("You may only access your own records")

    # -- queries ------------------------------------------------------------

    def get(self, user_id: str) -> User:
        user = self.users.find_by_id(user_id)
        if user is None:
            raise NotFoundError(f"No user with id {user_id}")
        return user

    def list(
        self, actor: User, role: Optional[UserRole] = None,
        is_active: Optional[bool] = None, query: Optional[str] = None,
        limit: int = 50, offset: int = 0,
    ) -> Page[User]:
        # Admin only. API-SPECIFICATION.md line 139: "List all users with
        # pagination and filters. Requires auth (admin only)." An instructor
        # needing a roster uses GET /enrollments/course/{id}, which is scoped
        # to their course rather than the whole institution.
        self.require_role(actor, *_ADMIN_ONLY)
        return self.users.search(role=role, is_active=is_active, query=query,
                                 limit=limit, offset=offset)

    @transactional
    def rename(self, user: User, full_name: str) -> User:
        """Self-service display-name change."""
        user.full_name = full_name.strip()
        user.updated_at = utcnow()
        self.session.flush()
        self.audit.record(
            AuditAction.USER_UPDATED, user_id=user.id, resource_type="user",
            resource_id=user.id, details={"full_name": user.full_name},
        )
        return user

    # -- administration -----------------------------------------------------

    @transactional
    def set_active(self, actor: User, user_id: str, is_active: bool) -> User:
        self.require_role(actor, *_ADMIN_ONLY)
        user = self.get(user_id)
        if actor.id == user.id and not is_active:
            # An admin who deactivates themselves cannot undo it.
            raise PermissionDeniedError("You cannot deactivate your own account")
        self.users.set_active(user, is_active)
        details = {"email": user.email, "is_active": is_active}
        action = AuditAction.USER_ACTIVATED if is_active else AuditAction.USER_DEACTIVATED
        if is_active and user.scheduled_deletion_at is not None:
            # Reactivating an account cancels its pending deletion, so the
            # retention sweep never erases an account that is in use again.
            user.scheduled_deletion_at = None
            self.session.flush()
            details["deletion_cancelled"] = True
            action = AuditAction.USER_RESTORED
        # ``user_id`` is the admin who acted; ``resource_id`` and the email in
        # ``details`` identify the account it was done to.
        self.audit.record(
            action, user_id=actor.id, resource_type="user",
            resource_id=user.id, details=details,
        )
        return user

    @transactional
    def bulk_create(
        self, actor: User, specs: Iterable[dict], default_password: str,
    ) -> List[User]:
        """Backs ``POST /admin/users/bulk``.

        Existing emails are skipped rather than raising, so one duplicate in a
        batch of two hundred does not discard the other 199.
        """
        from .auth_service import AuthService

        self.require_role(actor, *_ADMIN_ONLY)
        auth = AuthService(self.session)
        auth.validate_password(default_password)

        specs = list(specs)
        wanted = [str(s["email"]).strip().lower() for s in specs]
        existing = {u.email.lower() for u in self.users.find_by_emails(wanted)}

        created: List[User] = []
        hashed = auth.hash_password(default_password)
        for spec in specs:
            email = str(spec["email"]).strip().lower()
            if email in existing:
                continue
            existing.add(email)  # guards duplicates *within* the batch too
            created.append(
                self.users.save(
                    User(
                        email=email,
                        full_name=spec.get("full_name") or email.split("@")[0],
                        hashed_password=hashed,
                        role=UserRole(spec.get("role", UserRole.STUDENT.value)),
                    )
                )
            )
        self.audit.record(
            AuditAction.USER_CREATED, user_id=actor.id, resource_type="user",
            details={"requested": len(specs), "created": len(created)},
        )
        return created

    # -- forgot password -------------------------------------------------------
    #
    # Self-service first: the user asks from the login page, receives a 6-digit
    # code by email (services/email_service.py, configured from SMTP_* env
    # vars), and sets a new password with it. Every request also appears on
    # the admin's "password reset requests" list, so a user without access to
    # their email can still be helped: the admin issues a one-off temporary
    # password instead (reset_password). Every step is audit-logged.

    def _locked_reset_user(self, *, email: Optional[str] = None, user_id: Optional[str] = None):
        """Serialize issuance/redemption on the existing account, not a new table.

        Locking the account also serializes against an admin password reset.
        Populate existing state after waiting so a concurrent request cannot use
        an ORM-cached password or code state from before the winning commit.
        """
        from sqlalchemy import func, select

        condition = User.id == user_id if user_id is not None else func.lower(User.email) == (email or "").strip().lower()
        return self.session.scalars(select(User).where(condition).with_for_update()
            .execution_options(populate_existing=True)).first()

    def request_password_reset(
        self, email: str, *, ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        """Issue and email a one-time code. Always succeeds silently.

        The response never reveals whether the email has an account, so the
        response is generic, and the email goes out on a background thread so
        SMTP latency does not affect it. Database paths are not constant-time.
        Floods from one IP address and rapid repeats for one account are dropped
        rather than acted on. The email is sent after the code is committed.
        """
        issued = self._issue_reset_code(email, ip_address=ip_address, user_agent=user_agent)
        if issued is None:
            return
        to_address, code = issued
        # Sent on a background thread: if the reply waited for the mail server,
        # a slower answer would reveal that the email has an account.
        self._email_code(to_address, code)

    @transactional
    def _issue_reset_code(
        self, email: str, *, ip_address: Optional[str], user_agent: Optional[str],
    ) -> Optional[tuple]:
        from sqlalchemy import func, select

        from ..models import AuditLog

        if ip_address:
            # Serialize the existing count/insert limiter across API workers.
            # PostgreSQL transaction locks need no new table or migration.
            import hashlib
            lock_key = int.from_bytes(hashlib.sha256(
                ("password-reset-request:" + ip_address).encode()).digest()[:8], "big", signed=True)
            self.session.execute(select(func.pg_advisory_xact_lock(lock_key)))
            now = utcnow()  # The rate-limit window must follow any lock wait.
            recent_from_ip = self.session.scalar(
                select(func.count()).select_from(AuditLog).where(
                    AuditLog.action == AuditAction.PASSWORD_RESET_REQUESTED.value,
                    AuditLog.ip_address == ip_address,
                    AuditLog.timestamp >= now - timedelta(hours=1)))
            if (recent_from_ip or 0) >= RESET_REQUESTS_PER_IP_PER_HOUR:
                return None
        user = self._locked_reset_user(email=email)
        if user is None or not user.is_active or user.scheduled_deletion_at is not None:
            # Logged (without a resource) so unknown emails still count towards
            # the per-IP limit; never shown on the admin's list.
            self.audit.record(
                AuditAction.PASSWORD_RESET_REQUESTED, resource_type="user",
                ip_address=ip_address, user_agent=user_agent, success=False,
                details={"email": (email or "").strip().lower()[:254],
                         "reason": "no_account" if user is None else "account_unavailable"},
            )
            return None

        # Start validity after the account lock, not before a potentially long wait.
        now = utcnow()
        if self._code_limit(user, now) is not None:
            return None
        code = self._new_code(user, now)
        self.audit.record(
            AuditAction.PASSWORD_RESET_REQUESTED, user_id=user.id, resource_type="user",
            resource_id=user.id, ip_address=ip_address, user_agent=user_agent,
            details={"email": user.email},
        )
        return user.email, code

    def _code_limit(self, user: User, now) -> Optional[str]:
        """Why a new code may not be issued yet, or None if it may."""
        from sqlalchemy import select

        from ..models import PasswordResetCode

        recent_codes = list(self.session.scalars(
            select(PasswordResetCode.created_at).where(
                PasswordResetCode.user_id == user.id,
                PasswordResetCode.created_at >= now - timedelta(hours=1))))
        if len(recent_codes) >= RESET_CODES_PER_ACCOUNT_PER_HOUR:
            return "too_many"
        if recent_codes and max(recent_codes) >= now - timedelta(seconds=RESET_CODE_RESEND_SECONDS):
            return "too_soon"
        return None

    def _new_code(self, user: User, now) -> str:
        """Cancel the account's unused codes and store a fresh one (hashed)."""
        import secrets

        from sqlalchemy import select

        from ..models import PasswordResetCode

        for old in self.session.scalars(select(PasswordResetCode).where(
                PasswordResetCode.user_id == user.id, PasswordResetCode.used_at.is_(None))):
            old.used_at = now
        code = f"{secrets.randbelow(1_000_000):06d}"
        row = PasswordResetCode(user_id=user.id, code_hash="-",
                                expires_at=now + timedelta(minutes=RESET_CODE_TTL_MINUTES))
        self.session.add(row)
        self.session.flush()
        row.code_hash = self._code_hash(row.id, code)
        self.session.flush()
        return code

    @staticmethod
    def _email_code(to_address: str, code: str, *, by_admin: bool = False) -> None:
        """Send the code on a background thread (see request_password_reset)."""
        import threading

        from .email_service import send_email

        intro = ("An administrator has started a password reset for your SAIV account.\n\n"
                 if by_admin else "")
        worker = threading.Thread(target=send_email, daemon=True, args=(
            to_address,
            "Your SAIV password reset code",
            f"{intro}Your SAIV password reset code is: {code}\n\n"
            f"It expires in {RESET_CODE_TTL_MINUTES} minutes and can be used once.\n"
            "On the SAIV login page choose \"Forgot password?\", enter your email, then "
            "this code and your new password.\n"
            "If you did not ask to reset your password, ignore this email; "
            "your password has not changed.\n",
        ))
        try:
            worker.start()
        except RuntimeError:
            # Keep the public reply generic even if a mail worker cannot start.
            import logging
            logging.getLogger(__name__).warning("Password reset email could not be queued")

    def send_reset_email(self, actor: User, user_id: str) -> Dict[str, str]:
        """Admin-initiated reset by email (preferred over a temporary password).

        Emails the account a one-time code; the user then chooses their own new
        password, so the admin never learns it. Refused when email is not set up
        (the admin then falls back to reset_password) or when a code went out
        moments ago.
        """
        from .email_service import email_configured

        self.require_role(actor, *_ADMIN_ONLY)
        if not email_configured():
            raise BusinessRuleError(
                "Email isn't set up on this server, so no code can be sent. "
                "Use a temporary password instead.")
        to_address, code = self._issue_code_for(actor, user_id)
        self._email_code(to_address, code, by_admin=True)
        return {"user_id": user_id, "email": to_address,
                "message": f"A reset code has been emailed to {to_address}. "
                           f"It expires in {RESET_CODE_TTL_MINUTES} minutes."}

    @transactional
    def _issue_code_for(self, actor: User, user_id: str) -> tuple:
        user = self._locked_reset_user(user_id=user_id)
        if user is None:
            raise NotFoundError("No user with that id")
        if not user.is_active or user.scheduled_deletion_at is not None:
            raise BusinessRuleError("This account is inactive or being deleted. Restore it first.")
        now = utcnow()
        limit = self._code_limit(user, now)
        if limit == "too_soon":
            raise BusinessRuleError("A reset code was sent less than a minute ago. "
                                    "Ask the user to check their inbox and spam folder.")
        if limit == "too_many":
            raise BusinessRuleError("Too many reset codes for this account in the last hour. "
                                    "Use a temporary password instead.")
        code = self._new_code(user, now)
        self.audit.record(
            AuditAction.PASSWORD_RESET_EMAILED, user_id=actor.id, resource_type="user",
            resource_id=user.id, details={"email": user.email, "code_emailed": True},
        )
        return user.email, code

    @staticmethod
    def _code_hash(row_id: str, code: str) -> str:
        """HMAC of the code, keyed with SECRET_KEY and salted with the row id."""
        import hashlib
        import hmac
        import os

        secret = os.environ.get("SECRET_KEY")
        if not secret:
            raise RuntimeError("SECRET_KEY is required for password reset verification")
        key = secret.encode()
        return hmac.new(key, f"{row_id}:{code}".encode(), hashlib.sha256).hexdigest()

    def confirm_password_reset(self, email: str, code: str, new_password: str) -> None:
        """Set a new password using an emailed code.

        One generic error for every failure (unknown email, wrong, expired,
        used or exhausted code), so the endpoint reveals nothing. A wrong guess
        is counted and committed before the error is raised.
        """
        from .auth_service import AuthService
        from .exceptions import ValidationError

        AuthService.validate_password(new_password)
        # bcrypt compares only the first 72 bytes. Reject ambiguous new reset
        # passwords instead of silently truncating them. Other auth policies
        # and existing password hashes are deliberately unchanged.
        if len(new_password.encode("utf-8")) > 72:
            raise ValidationError("Password must not exceed 72 UTF-8 bytes")
        if not self._apply_reset_code(email, code, new_password):
            raise ValidationError("That code is invalid or has expired. Request a new code.")

    @transactional
    def _apply_reset_code(self, email: str, code: str, new_password: str) -> bool:
        import hmac

        from sqlalchemy import select

        from ..models import PasswordResetCode
        from .auth_service import AuthService

        user = self._locked_reset_user(email=email)
        if user is None or not user.is_active or user.scheduled_deletion_at is not None:
            return False
        now = utcnow()  # A code may have expired while waiting for the account lock.
        row = self.session.scalars(
            select(PasswordResetCode).where(
                PasswordResetCode.user_id == user.id,
                PasswordResetCode.used_at.is_(None),
                PasswordResetCode.expires_at > now,
            ).order_by(PasswordResetCode.created_at.desc()).limit(1)).first()
        if row is None or row.attempts >= RESET_CODE_MAX_ATTEMPTS:
            return False
        supplied = "".join(ch for ch in str(code or "") if ch.isdigit())
        if not hmac.compare_digest(row.code_hash, self._code_hash(row.id, supplied)):
            row.attempts += 1
            if row.attempts >= RESET_CODE_MAX_ATTEMPTS:
                row.used_at = now
            self.audit.record(
                AuditAction.PASSWORD_RESET, user_id=user.id, resource_type="user",
                resource_id=user.id, success=False,
                details={"self_service": True, "reason": "wrong_code",
                         "attempts": row.attempts},
            )
            return False
        user.hashed_password = AuthService.hash_password(new_password)
        user.updated_at = now
        row.used_at = now
        self.session.flush()
        self.audit.record(
            AuditAction.PASSWORD_RESET, user_id=user.id, resource_type="user",
            resource_id=user.id, details={"email": user.email, "self_service": True},
        )
        return True

    def pending_reset_requests(self, actor: User) -> List[Dict[str, object]]:
        """Requests from the last RESET_REQUEST_DAYS that no admin has acted on
        yet (no password reset for that account since the request)."""
        from sqlalchemy import func, select

        from ..models import AuditLog

        self.require_role(actor, *_ADMIN_ONLY)
        since = utcnow() - timedelta(days=RESET_REQUEST_DAYS)
        latest_request = dict(self.session.execute(
            select(AuditLog.resource_id, func.max(AuditLog.timestamp))
            .where(AuditLog.action == AuditAction.PASSWORD_RESET_REQUESTED.value,
                   AuditLog.success.is_(True),
                   AuditLog.resource_id.is_not(None),
                   AuditLog.timestamp >= since)
            .group_by(AuditLog.resource_id)).all())
        if not latest_request:
            return []
        latest_reset = dict(self.session.execute(
            select(AuditLog.resource_id, func.max(AuditLog.timestamp))
            .where(AuditLog.action == AuditAction.PASSWORD_RESET.value,
                   AuditLog.success.is_(True),
                   AuditLog.resource_id.in_(list(latest_request)))
            .group_by(AuditLog.resource_id)).all())
        pending = []
        for user_id, requested_at in latest_request.items():
            reset_at = latest_reset.get(user_id)
            if reset_at is not None and reset_at >= requested_at:
                continue
            user = self.users.find_by_id(user_id)
            if user is None or user.scheduled_deletion_at is not None:
                continue
            pending.append({
                "user_id": user.id, "email": user.email, "full_name": user.full_name,
                "role": user.role.value if hasattr(user.role, "value") else str(user.role),
                "is_active": user.is_active, "requested_at": requested_at,
            })
        pending.sort(key=lambda row: row["requested_at"], reverse=True)
        return pending

    @transactional
    def reset_password(self, actor: User, user_id: str) -> str:
        """Set a random temporary password and return it (shown once).

        Also clears the account's pending "forgot password" request.
        """
        import secrets

        from .auth_service import AuthService

        self.require_role(actor, *_ADMIN_ONLY)
        user = self._locked_reset_user(user_id=user_id)
        if user is None:
            raise NotFoundError("No user with that id")
        temporary = secrets.token_urlsafe(9)
        user.hashed_password = AuthService.hash_password(temporary)
        user.updated_at = utcnow()
        from sqlalchemy import select
        from ..models import PasswordResetCode
        for code in self.session.scalars(select(PasswordResetCode).where(
                PasswordResetCode.user_id == user.id, PasswordResetCode.used_at.is_(None))):
            code.used_at = user.updated_at
        self.session.flush()
        self.audit.record(
            AuditAction.PASSWORD_RESET, user_id=actor.id, resource_type="user",
            resource_id=user.id, details={"email": user.email, "by_admin": True},
        )
        return temporary

    @transactional
    def schedule_deletion(self, actor: User, user_id: str) -> User:
        """Soft delete: marks the account for erasure after the retention
        window instead of removing it now, so an accidental deletion is
        recoverable and in-flight attendance records stay intact."""
        self.require_role(actor, *_ADMIN_ONLY)
        user = self.get(user_id)
        if actor.id == user.id:
            # Same reason as set_active: an admin cannot undo deleting themselves.
            raise PermissionDeniedError("You cannot delete your own account")
        user.is_active = False
        user.scheduled_deletion_at = utcnow() + timedelta(days=RETENTION_DAYS)
        self.session.flush()
        self.audit.record(
            AuditAction.USER_DELETED, user_id=actor.id, resource_type="user",
            resource_id=user.id,
            details={"email": user.email, "scheduled_deletion_at": user.scheduled_deletion_at},
        )
        return user

    @transactional
    def purge_due(self) -> int:
        """Hard-delete accounts past their retention deadline.

        Meant for a scheduled job. The database cascades remove check-ins,
        devices and enrollments; audit rows survive with ``user_id`` nulled.
        """
        due = self.users.find_due_for_deletion()
        for user in due:
            self.audit.record(
                AuditAction.USER_UPDATED, resource_type="user", resource_id=user.id,
                details={"purged": True, "retention_days": RETENTION_DAYS},
            )
            self.users.delete(user)
        return len(due)
