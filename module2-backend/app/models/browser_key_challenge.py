"""Short-lived, single-use browser key challenges."""

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class BrowserKeyChallenge(Base):
    __tablename__ = "browser_key_challenges"

    id: Mapped[str] = mapped_column(String(43), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(10), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_version: Mapped[int] = mapped_column(Integer, nullable=False)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    key_id: Mapped[str] = mapped_column(String(43), nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    recovery_device_id: Mapped[Optional[str]] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    recovery_key_id: Mapped[Optional[str]] = mapped_column(String(43))
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    __table_args__ = (
        CheckConstraint("purpose IN ('enroll', 'checkin')"),
        CheckConstraint(
            "(purpose = 'checkin' AND session_id IS NOT NULL AND recovery_device_id IS NULL "
            "AND recovery_key_id IS NULL AND used_at IS NULL) OR "
            "(purpose = 'enroll' AND session_id IS NULL "
            "AND (recovery_device_id IS NULL) = (recovery_key_id IS NULL))",
            name="ck_browser_key_challenges_scope",
        ),
        Index("ix_browser_key_challenges_expires_at", "expires_at"),
        Index("ix_browser_key_challenges_enrollment_attempts", "user_id", "used_at",
              postgresql_where=text("used_at IS NOT NULL")),
    )
