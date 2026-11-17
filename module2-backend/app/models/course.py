"""Courses and their default verification policy."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, CheckConstraint, Float, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at_column, pk_column, updated_at_column

if TYPE_CHECKING:
    from .assignment import CourseInstructor
    from .attendance_session import AttendanceSession
    from .enrollment import Enrollment
    from .grouping import CourseSettings


class Course(Base):
    """A course. Its venue and policy columns are the defaults every session
    inherits unless the session overrides them."""

    __tablename__ = "courses"

    id: Mapped[str] = pk_column()
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    semester: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                            server_default="true")

    # Default venue / geofence
    venue_latitude: Mapped[Optional[float]] = mapped_column(Float)
    venue_longitude: Mapped[Optional[float]] = mapped_column(Float)
    venue_name: Mapped[Optional[str]] = mapped_column(String(255))
    geofence_radius_meters: Mapped[float] = mapped_column(
        Float, nullable=False, default=100.0, server_default=text("100.0")
    )

    # Default verification policy
    require_face_recognition: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    require_device_binding: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    risk_threshold: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5, server_default=text("0.5")
    )

    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    # -- relationships ------------------------------------------------------
    enrollments: Mapped[List["Enrollment"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
    )
    sessions: Mapped[List["AttendanceSession"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
    )
    # selectin: CourseResponse always serialises instructor_ids, so load the
    # links for a whole page of courses in one extra query, not one per course.
    instructor_links: Mapped[List["CourseInstructor"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True,
        lazy="selectin",
    )

    settings: Mapped[Optional["CourseSettings"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True,
        lazy="selectin", uselist=False,
    )

    @property
    def has_tutorials(self) -> bool:
        return bool(self.settings and self.settings.has_tutorials)

    @property
    def has_labs(self) -> bool:
        return bool(self.settings and self.settings.has_labs)

    @property
    def instructor_ids(self) -> List[str]:
        """Instructors assigned to teach this course (may be several)."""
        return [link.instructor_id for link in self.instructor_links]

    __table_args__ = (
        CheckConstraint(
            "venue_latitude IS NULL OR venue_latitude BETWEEN -90 AND 90",
            name="ck_courses_latitude",
        ),
        CheckConstraint(
            "venue_longitude IS NULL OR venue_longitude BETWEEN -180 AND 180",
            name="ck_courses_longitude",
        ),
        CheckConstraint("geofence_radius_meters > 0", name="ck_courses_geofence_radius"),
        CheckConstraint("risk_threshold BETWEEN 0 AND 1", name="ck_courses_risk_threshold"),
        Index("ix_courses_code", "code"),
        Index("ix_courses_semester", "semester"),
        Index("ix_courses_is_active", "is_active"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Course {self.code} {self.semester}>"
