"""One immutable snapshot per check-in submission.

``checkins`` holds exactly one row per student per session — that invariant is
the attendance outcome, and ``uq_checkins_session_student`` enforces it. A
retry therefore UPDATES that row, which means the superseded attempt's
evidence (where the student was, what module 3 scored, which factors fired)
was overwritten and gone.

This table is the history ``checkins`` cannot keep. Every submission appends a
row, including the first, so the attempt trail is complete rather than
"everything except the attempt that counted". Rows are never updated: a
reviewer looking at a flagged check-in needs to see what was true at each
attempt, not a reconstruction.

PRIVACY: this duplicates the location and biometric SCORES of each attempt,
which is precisely why it carries its own ``scheduled_deletion_at`` rather
than living in the append-only audit log. It never stores an image, an
embedding, or anything that is not already a column on ``checkins``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, NOW_UTC, enum_column, pk_column
from .enums import CheckInStatus

if TYPE_CHECKING:
    from .checkin import CheckIn

# Kept in sync with ck_checkin_attempts_status in db/schema.sql.
_STATUS_VALUES = ", ".join(f"'{member.value}'" for member in CheckInStatus)


class CheckInAttempt(Base):
    """What one submission looked like, preserved after a retry replaces it."""

    __tablename__ = "checkin_attempts"

    id: Mapped[str] = pk_column()
    checkin_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("checkins.id", ondelete="CASCADE"), nullable=False
    )

    # 1 for the first submission, then 2, 3, ... Unique per check-in, so a
    # double-write cannot silently create two "attempt 2" rows.
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # The decision THIS attempt reached, not the check-in's current status.
    status: Mapped[CheckInStatus] = mapped_column(
        enum_column(CheckInStatus, 20), nullable=False
    )
    risk_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0.0")
    )
    risk_factors: Mapped[Optional[str]] = mapped_column(Text)  # JSON array of strings

    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    location_accuracy_meters: Mapped[Optional[float]] = mapped_column(Float)
    distance_from_venue_meters: Mapped[Optional[float]] = mapped_column(Float)

    liveness_passed: Mapped[Optional[bool]] = mapped_column(Boolean)
    liveness_score: Mapped[Optional[float]] = mapped_column(Float)
    face_match_passed: Mapped[Optional[bool]] = mapped_column(Boolean)
    face_match_score: Mapped[Optional[float]] = mapped_column(Float)

    # Which device submitted THIS attempt. Kept per attempt because switching
    # device between attempts is itself worth seeing.
    device_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("devices.id", ondelete="SET NULL")
    )

    browser_key_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    attempted_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=NOW_UTC
    )
    scheduled_deletion_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    checkin: Mapped["CheckIn"] = relationship(back_populates="attempts")

    __table_args__ = (
        UniqueConstraint("checkin_id", "attempt_number",
                         name="uq_checkin_attempts_checkin_number"),
        CheckConstraint(f"status IN ({_STATUS_VALUES})",
                        name="ck_checkin_attempts_status"),
        CheckConstraint("attempt_number >= 1",
                        name="ck_checkin_attempts_attempt_number"),
        CheckConstraint("latitude IS NULL OR latitude BETWEEN -90 AND 90",
                        name="ck_checkin_attempts_latitude"),
        CheckConstraint("longitude IS NULL OR longitude BETWEEN -180 AND 180",
                        name="ck_checkin_attempts_longitude"),
        CheckConstraint("liveness_score IS NULL OR liveness_score BETWEEN 0 AND 1",
                        name="ck_checkin_attempts_liveness_score"),
        CheckConstraint("face_match_score IS NULL OR face_match_score BETWEEN 0 AND 1",
                        name="ck_checkin_attempts_face_match_score"),
        Index("ix_checkin_attempts_checkin_id", "checkin_id"),
        Index("ix_checkin_attempts_attempted_at", "attempted_at"),
    )
