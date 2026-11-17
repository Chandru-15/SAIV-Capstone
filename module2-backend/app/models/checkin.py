"""Check-in records: the evidence and the resulting risk decision."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, NOW_UTC, enum_column, pk_column, utcnow
from .enums import CheckInStatus, LivenessChallengeType

# How long after checking in a student may appeal a flagged or rejected
# check-in. Measured from checked_in_at (a column on this row, so computing it
# never needs an extra query). Override with APPEAL_WINDOW_DAYS.
APPEAL_WINDOW_DAYS = int(os.environ.get("APPEAL_WINDOW_DAYS", "7"))

if TYPE_CHECKING:
    from .attendance_session import AttendanceSession
    from .checkin_attempt import CheckInAttempt
    from .device import Device
    from .risk_signal import RiskSignal
    from .user import User


class CheckIn(Base):
    """One check-in attempt. At most one per student per session.

    PRIVACY: only ``face_embedding_hash`` (SHA-256 hex) is kept — never the
    image or the embedding. Geolocation columns stay NULL without consent.
    """

    __tablename__ = "checkins"

    id: Mapped[str] = pk_column()
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("devices.id", ondelete="SET NULL")
    )

    browser_key_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    status: Mapped[CheckInStatus] = mapped_column(
        enum_column(CheckInStatus, 20),
        nullable=False,
        default=CheckInStatus.PENDING,
        server_default="pending",
    )
    checked_in_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=NOW_UTC
    )
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Geolocation (requires User.geolocation_consent)
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    location_accuracy_meters: Mapped[Optional[float]] = mapped_column(Float)
    distance_from_venue_meters: Mapped[Optional[float]] = mapped_column(Float)

    # Liveness (requires User.camera_consent)
    liveness_passed: Mapped[Optional[bool]] = mapped_column(Boolean)
    liveness_score: Mapped[Optional[float]] = mapped_column(Float)
    liveness_challenge_type: Mapped[Optional[LivenessChallengeType]] = mapped_column(
        enum_column(LivenessChallengeType, 50)
    )

    # Face match
    face_match_passed: Mapped[Optional[bool]] = mapped_column(Boolean)
    face_match_score: Mapped[Optional[float]] = mapped_column(Float)
    face_embedding_hash: Mapped[Optional[str]] = mapped_column(String(64))

    # Risk decision
    risk_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0.0")
    )
    risk_factors: Mapped[Optional[str]] = mapped_column(Text)  # JSON array of strings
    qr_code_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # Manual review
    reviewed_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    review_notes: Mapped[Optional[str]] = mapped_column(Text)

    # Student appeal
    appeal_reason: Mapped[Optional[str]] = mapped_column(Text)
    appealed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    scheduled_deletion_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # -- relationships ------------------------------------------------------
    session: Mapped["AttendanceSession"] = relationship(back_populates="checkins")
    student: Mapped["User"] = relationship(
        back_populates="checkins", foreign_keys=[student_id]
    )
    reviewer: Mapped[Optional["User"]] = relationship(
        back_populates="reviewed_checkins", foreign_keys=[reviewed_by_id]
    )
    device: Mapped[Optional["Device"]] = relationship(back_populates="checkins")
    risk_signals: Mapped[List["RiskSignal"]] = relationship(
        back_populates="checkin", cascade="all, delete-orphan", passive_deletes=True
    )
    # One row per submission, including the first. This row is updated in
    # place on a retry, so the attempts are the only record of what each
    # earlier submission actually looked like.
    attempts: Mapped[List["CheckInAttempt"]] = relationship(
        back_populates="checkin", cascade="all, delete-orphan",
        passive_deletes=True, order_by="CheckInAttempt.attempt_number",
    )

    __table_args__ = (
        UniqueConstraint("session_id", "student_id", name="uq_checkins_session_student"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'flagged', 'rejected', 'appealed')",
            name="ck_checkins_status",
        ),
        CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90", name="ck_checkins_latitude"
        ),
        CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180",
            name="ck_checkins_longitude",
        ),
        CheckConstraint(
            "liveness_score IS NULL OR liveness_score BETWEEN 0 AND 1",
            name="ck_checkins_liveness_score",
        ),
        CheckConstraint(
            "face_match_score IS NULL OR face_match_score BETWEEN 0 AND 1",
            name="ck_checkins_face_match_score",
        ),
        CheckConstraint("risk_score BETWEEN 0 AND 1", name="ck_checkins_risk_score"),
        CheckConstraint(
            "face_embedding_hash IS NULL OR char_length(face_embedding_hash) = 64",
            name="ck_checkins_face_hash_len",
        ),
        CheckConstraint(
            "liveness_challenge_type IS NULL "
            "OR liveness_challenge_type IN ('blink', 'head_turn', 'speak', "
            "'passive', 'colour_reflection')",
            name="ck_checkins_liveness_challenge_type",
        ),
        Index("ix_checkins_session_id", "session_id"),
        Index("ix_checkins_student_id", "student_id"),
        Index("ix_checkins_device_id", "device_id"),
        Index("ix_checkins_status", "status"),
        Index("ix_checkins_checked_in_at", "checked_in_at"),
        Index("ix_checkins_risk_score", "risk_score"),
        Index("ix_checkins_reviewed_by", "reviewed_by_id"),
        # Instructor review queue: flagged / appealed check-ins, newest first.
        Index(
            "ix_checkins_review_queue",
            "status",
            text("checked_in_at DESC"),
            postgresql_where=text("status IN ('flagged', 'appealed')"),
        ),
        Index(
            "ix_checkins_scheduled_deletion_at",
            "scheduled_deletion_at",
            postgresql_where=text("scheduled_deletion_at IS NOT NULL"),
        ),
    )

    @property
    def device_trust_score(self) -> Optional[str]:
        if self.device is None:
            return None
        value = self.device.trust_score
        return value.value if hasattr(value, "value") else str(value)

    @property
    def appeal_deadline(self) -> Optional[datetime]:
        """Last moment this check-in may be appealed."""
        if self.checked_in_at is None:
            return None
        return self.checked_in_at + timedelta(days=APPEAL_WINDOW_DAYS)

    @property
    def can_appeal(self) -> bool:
        """Flagged or rejected, never appealed before (one appeal per
        check-in; the decision on it is final), and still inside the window."""
        status = self.status.value if hasattr(self.status, "value") else str(self.status)
        if status not in (CheckInStatus.FLAGGED.value, CheckInStatus.REJECTED.value):
            return False
        if self.appealed_at is not None:
            return False
        deadline = self.appeal_deadline
        return deadline is None or utcnow() <= deadline

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CheckIn student={self.student_id} risk={self.risk_score} {self.status}>"
