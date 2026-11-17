"""Append-only audit trail."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, NOW_UTC, pk_column, utcnow

if TYPE_CHECKING:
    from .user import User


class AuditLog(Base):
    """An immutable record of a sensitive action.

    There is deliberately no ``updated_at``: rows are only ever INSERTed. The
    backend must never UPDATE or DELETE here — see the note at the bottom of
    db/schema.sql for enforcing that with a REVOKE in production.

    ``user_id`` is ON DELETE SET NULL so the trail survives user deletion in an
    anonymised form. ``device_id`` is deliberately NOT a foreign key, for the
    same reason.

    ``action`` is a plain String, not an enum column: the API spec's action list
    ends with "etc.", so new actions must be loggable without a migration. See
    :class:`~app.models.enums.AuditAction` for the known values.
    """

    __tablename__ = "audit_logs"

    id: Mapped[str] = pk_column()
    user_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(String(50))
    resource_id: Mapped[Optional[str]] = mapped_column(String(36))
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))  # IPv4 or IPv6
    user_agent: Mapped[Optional[str]] = mapped_column(String(500))
    device_id: Mapped[Optional[str]] = mapped_column(String(36))
    details: Mapped[Optional[str]] = mapped_column(Text)  # JSON
    success: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=NOW_UTC
    )

    # -- relationships ------------------------------------------------------
    user: Mapped[Optional["User"]] = relationship(back_populates="audit_logs")

    __table_args__ = (
        Index("ix_audit_logs_user_id", "user_id"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_timestamp", "timestamp"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
        Index("ix_audit_logs_ip_address", "ip_address"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AuditLog {self.action} user={self.user_id} ok={self.success}>"
