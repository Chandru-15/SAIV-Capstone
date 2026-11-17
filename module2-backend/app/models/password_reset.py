"""One-time codes for self-service password reset ("forgot password").

    password_reset_codes   (user_id, code_hash, expires_at, attempts, used_at)

The 6-digit code itself is never stored: only an HMAC of it, keyed with the
row id and the application's SECRET_KEY, so a database dump does not reveal
live codes. A code expires after a few minutes, works once, and is cancelled
after too many wrong guesses (see UserService.confirm_password_reset).

main.py creates this table at startup if it is missing, like the assignment
and group tables, so an existing database volume needs no manual migration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, NOW_UTC, pk_column, utcnow


class PasswordResetCode(Base):
    __tablename__ = "password_reset_codes"

    id: Mapped[str] = pk_column()
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=NOW_UTC
    )

    __table_args__ = (
        Index("ix_password_reset_codes_user_id", "user_id"),
    )