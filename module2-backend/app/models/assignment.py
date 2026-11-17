"""Staff assignments: which instructors teach a course, which TAs run a session.

Two join tables rather than columns, because both relationships are
many-to-many: a course can be co-taught, and a TA can assist several sessions
(and a session can have several TAs).

    course_instructors   (course_id, instructor_id)   set by an admin
    session_tas          (session_id, ta_id)          set by an admin or by an
                                                      instructor of the course

DDL lives in db/migrations/001_staff_assignments.sql. main.py also creates
these two tables at startup if they are missing, so an existing database
volume picks them up without a manual migration.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, NOW_UTC, pk_column, utcnow

if TYPE_CHECKING:
    from .attendance_session import AttendanceSession
    from .course import Course


class CourseInstructor(Base):
    __tablename__ = "course_instructors"

    id: Mapped[str] = pk_column()
    course_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    instructor_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    assigned_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=NOW_UTC
    )

    course: Mapped["Course"] = relationship(back_populates="instructor_links")

    __table_args__ = (
        UniqueConstraint("course_id", "instructor_id", name="uq_course_instructors"),
        Index("ix_course_instructors_instructor_id", "instructor_id"),
    )


class SessionTA(Base):
    __tablename__ = "session_tas"

    id: Mapped[str] = pk_column()
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    ta_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    assigned_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=NOW_UTC
    )

    session: Mapped["AttendanceSession"] = relationship(back_populates="ta_links")

    __table_args__ = (
        UniqueConstraint("session_id", "ta_id", name="uq_session_tas"),
        Index("ix_session_tas_ta_id", "ta_id"),
    )
