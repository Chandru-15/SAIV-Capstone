"""Repository for :class:`~app.models.audit_log.AuditLog`.

This is the one repository that does NOT inherit the full CRUD surface in
spirit: the audit trail is append-only, so the mutating and deleting methods
are overridden to raise. ``db/schema.sql`` deliberately has no trigger
enforcing that (it would fire on the ON DELETE SET NULL that anonymises the
trail), which makes this class the place where the rule actually lives.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional, Union

from sqlalchemy import select

from ..models import AuditAction, AuditLog
from .base import DEFAULT_LIMIT, BaseRepository, Page


class AuditLogIsAppendOnlyError(RuntimeError):
    """Raised on any attempt to modify or delete an audit record."""


class AuditLogRepository(BaseRepository[AuditLog]):
    model = AuditLog

    # -- write (append only) ------------------------------------------------

    def record(
        self,
        action: Union[AuditAction, str],
        user_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        device_id: Optional[str] = None,
        details: Optional[str] = None,
        success: bool = True,
    ) -> AuditLog:
        """Append one entry. ``action`` accepts an :class:`AuditAction` or any
        string, since the schema deliberately does not constrain it."""
        entry = AuditLog(
            action=action.value if isinstance(action, AuditAction) else action,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            device_id=device_id,
            details=details,
            success=success,
        )
        return self.save(entry)

    # -- read ---------------------------------------------------------------

    def search(
        self,
        user_id: Optional[str] = None,
        action: Optional[Union[AuditAction, str]] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        success: Optional[bool] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Page[AuditLog]:
        """Backs ``GET /audit/logs``."""
        stmt = select(AuditLog)
        if user_id is not None:
            stmt = stmt.where(AuditLog.user_id == user_id)
        if action is not None:
            stmt = stmt.where(
                AuditLog.action == (action.value if isinstance(action, AuditAction) else action)
            )
        if resource_type is not None:
            stmt = stmt.where(AuditLog.resource_type == resource_type)
        if resource_id is not None:
            stmt = stmt.where(AuditLog.resource_id == resource_id)
        if ip_address is not None:
            stmt = stmt.where(AuditLog.ip_address == ip_address)
        if success is not None:
            stmt = stmt.where(AuditLog.success.is_(success))
        if since is not None:
            stmt = stmt.where(AuditLog.timestamp >= since)
        if until is not None:
            stmt = stmt.where(AuditLog.timestamp <= until)
        return self.paginate(stmt.order_by(AuditLog.timestamp.desc()), limit, offset)

    def find_for_resource(self, resource_type: str, resource_id: str) -> List[AuditLog]:
        """Everything that ever happened to one object — uses the composite
        index ``ix_audit_logs_resource``."""
        stmt = (
            select(AuditLog)
            .where(
                AuditLog.resource_type == resource_type,
                AuditLog.resource_id == resource_id,
            )
            .order_by(AuditLog.timestamp)
        )
        return list(self.session.scalars(stmt))

    def count_failures_since(
        self, since: datetime, ip_address: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> int:
        """Brute-force detection: failed actions in a window, by IP or user."""
        from sqlalchemy import func

        stmt = (
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.success.is_(False), AuditLog.timestamp >= since)
        )
        if ip_address is not None:
            stmt = stmt.where(AuditLog.ip_address == ip_address)
        if user_id is not None:
            stmt = stmt.where(AuditLog.user_id == user_id)
        return int(self.session.scalar(stmt) or 0)

    # -- forbidden ----------------------------------------------------------

    def delete(self, entity: AuditLog) -> None:  # type: ignore[override]
        raise AuditLogIsAppendOnlyError("audit_logs is append-only; entries cannot be deleted")

    def delete_by_id(self, entity_id: str) -> bool:  # type: ignore[override]
        raise AuditLogIsAppendOnlyError("audit_logs is append-only; entries cannot be deleted")

    def delete_all(self) -> int:  # type: ignore[override]
        raise AuditLogIsAppendOnlyError("audit_logs is append-only; entries cannot be deleted")

    def save(self, entity: AuditLog) -> AuditLog:
        """Insert only. Re-saving a persisted entry would be an UPDATE."""
        if entity in self.session and entity.id is not None:
            state: Any = getattr(entity, "_sa_instance_state", None)
            if state is not None and state.persistent:
                raise AuditLogIsAppendOnlyError(
                    "audit_logs is append-only; entries cannot be modified"
                )
        return super().save(entity)
