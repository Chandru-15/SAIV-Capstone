"""Attendance sessions.

The class is called ``AttendanceSession``, not ``Session``, to avoid colliding
with :class:`sqlalchemy.orm.Session` — which you will import in the same
modules constantly. The table is still ``sessions``.
"""

from __future__ import annotations

from datetime import datetime
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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at_column, enum_column, pk_column, updated_at_column
from .enums import SessionStatus, SessionType

if TYPE_CHECKING:
    from .assignment import SessionTA
    from .checkin import CheckIn
    from .grouping import SessionGroup
    from .course import Course
    from .user import User


class AttendanceSession(Base):
    """A single lecture / tutorial / lab / exam that students check in to.

    ``venue_*``, ``geofence_radius_meters`` and ``risk_threshold`` are NULLable
    overrides: when NULL, fall back to the parent course's value.
    """

    __tablename__ = "sessions"

    id: Mapped[str] = pk_column()
    course_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    instructor_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    session_type: Mapped[SessionType] = mapped_column(
        enum_column(SessionType, 50),
        nullable=False,
        default=SessionType.LECTURE,
        server_default="lecture",
    )
    description: Mapped[Optional[str]] = mapped_column(Text)

    scheduled_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    checkin_opens_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    checkin_closes_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    status: Mapped[SessionStatus] = mapped_column(
        enum_column(SessionStatus, 20),
        nullable=False,
        default=SessionStatus.SCHEDULED,
        server_default="scheduled",
    )
    actual_start: Mapped[Optional[datetime]] = mapped_column(DateTime)
    actual_end: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Per-session venue override
    venue_latitude: Mapped[Optional[float]] = mapped_column(Float)
    venue_longitude: Mapped[Optional[float]] = mapped_column(Float)
    venue_name: Mapped[Optional[str]] = mapped_column(String(255))
    geofence_radius_meters: Mapped[Optional[float]] = mapped_column(Float)

    # Per-session verification override
    require_liveness_check: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    require_face_match: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    risk_threshold: Mapped[Optional[float]] = mapped_column(Float)

    # Rotating QR check-in code
    qr_code_secret: Mapped[Optional[str]] = mapped_column(String(64))
    qr_code_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    # -- relationships ------------------------------------------------------
    course: Mapped["Course"] = relationship(back_populates="sessions")
    instructor: Mapped[Optional["User"]] = relationship(
        back_populates="taught_sessions", foreign_keys=[instructor_id]
    )
    checkins: Mapped[List["CheckIn"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )
    ta_links: Mapped[List["SessionTA"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True,
        lazy="selectin",
    )

    # Tutorial / lab sessions belong to one group; lectures have none.
    group_link: Mapped[Optional["SessionGroup"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True,
        lazy="selectin", uselist=False,
    )

    @property
    def group_id(self) -> Optional[str]:
        return self.group_link.group_id if self.group_link else None

    @property
    def group_name(self) -> Optional[str]:
        return self.group_link.group.name if self.group_link else None

    @property
    def group_size(self) -> Optional[int]:
        return len(self.group_link.group.members) if self.group_link else None

    @property
    def ta_ids(self) -> List[str]:
        """TAs assigned to run this session."""
        return [link.ta_id for link in self.ta_links]

    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled', 'active', 'closed', 'cancelled')",
            name="ck_sessions_status",
        ),
        CheckConstraint(
            "session_type IN ('lecture', 'tutorial', 'lab', 'exam')",
            name="ck_sessions_type",
        ),
        CheckConstraint("scheduled_end > scheduled_start", name="ck_sessions_schedule_order"),
        CheckConstraint(
            "checkin_closes_at > checkin_opens_at", name="ck_sessions_checkin_window_order"
        ),
        CheckConstraint(
            "venue_latitude IS NULL OR venue_latitude BETWEEN -90 AND 90",
            name="ck_sessions_latitude",
        ),
        CheckConstraint(
            "venue_longitude IS NULL OR venue_longitude BETWEEN -180 AND 180",
            name="ck_sessions_longitude",
        ),
        CheckConstraint(
            "geofence_radius_meters IS NULL OR geofence_radius_meters > 0",
            name="ck_sessions_geofence_radius",
        ),
        CheckConstraint(
            "risk_threshold IS NULL OR risk_threshold BETWEEN 0 AND 1",
            name="ck_sessions_risk_threshold",
        ),
        Index("ix_sessions_course_id", "course_id"),
        Index("ix_sessions_instructor_id", "instructor_id"),
        Index("ix_sessions_status", "status"),
        Index("ix_sessions_scheduled_start", "scheduled_start"),
        Index("ix_sessions_checkin_window", "checkin_opens_at", "checkin_closes_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AttendanceSession {self.name} ({self.status})>"
