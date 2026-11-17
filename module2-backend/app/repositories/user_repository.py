"""Repository for :class:`~app.models.user.User`."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import func, select

from ..models import User, UserRole
from .base import DEFAULT_LIMIT, BaseRepository, Page


class UserRepository(BaseRepository[User]):
    model = User

    # -- derived finders ----------------------------------------------------

    def find_by_email(self, email: str) -> Optional[User]:
        """Email is matched case-insensitively: addresses are not case
        sensitive in practice, and treating them as such lets the same person
        register twice."""
        stmt = select(User).where(func.lower(User.email) == email.lower())
        return self.session.scalars(stmt).first()

    def exists_by_email(self, email: str) -> bool:
        stmt = select(func.count()).select_from(User).where(
            func.lower(User.email) == email.lower()
        )
        return bool(self.session.scalar(stmt))

    def find_by_role(self, role: UserRole, limit: int = DEFAULT_LIMIT,
                     offset: int = 0) -> Page[User]:
        stmt = select(User).where(User.role == role).order_by(User.full_name)
        return self.paginate(stmt, limit, offset)

    def search(
        self,
        role: Optional[UserRole] = None,
        is_active: Optional[bool] = None,
        query: Optional[str] = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Page[User]:
        """Backs ``GET /users`` with its optional filters."""
        stmt = select(User)
        if role is not None:
            stmt = stmt.where(User.role == role)
        if is_active is not None:
            stmt = stmt.where(User.is_active.is_(is_active))
        if query:
            pattern = f"%{query.lower()}%"
            stmt = stmt.where(
                func.lower(User.full_name).like(pattern)
                | func.lower(User.email).like(pattern)
            )
        return self.paginate(stmt.order_by(User.full_name), limit, offset)

    def find_by_emails(self, emails: List[str]) -> List[User]:
        """Bulk lookup for ``POST /admin/users/bulk`` and bulk enrollment."""
        if not emails:
            return []
        lowered = [e.lower() for e in emails]
        stmt = select(User).where(func.lower(User.email).in_(lowered))
        return list(self.session.scalars(stmt))

    # -- domain operations --------------------------------------------------

    def touch_last_login(self, user: User, when: Optional[datetime] = None) -> User:
        from ..models.base import utcnow

        user.last_login_at = when or utcnow()
        self.session.flush()
        return user

    def set_active(self, user: User, is_active: bool) -> User:
        """Backs the admin activate / deactivate endpoints."""
        user.is_active = is_active
        self.session.flush()
        return user

    def find_due_for_deletion(self, now: Optional[datetime] = None) -> List[User]:
        """Users past their retention deadline.

        Uses the partial index ``ix_users_scheduled_deletion_at``.
        """
        from ..models.base import utcnow

        cutoff = now or utcnow()
        stmt = select(User).where(
            User.scheduled_deletion_at.is_not(None),
            User.scheduled_deletion_at <= cutoff,
        )
        return list(self.session.scalars(stmt))
