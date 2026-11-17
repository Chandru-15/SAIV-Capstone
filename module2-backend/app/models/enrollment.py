"""Student membership in a course."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, NOW_UTC, pk_column, utcnow

if TYPE_CHECKING:
    from .course import Course
    from .user import User


class Enrollment(Base):
    """One row per (student, course). Re-enrolling flips ``is_active`` back on
    rather than inserting a second row — the unique constraint forbids it."""

    __tablename__ = "enrollments"

    id: Mapped[str] = pk_column()
    student_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                            server_default="true")
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=NOW_UTC
    )
    dropped_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # -- relationships ------------------------------------------------------
    student: Mapped["User"] = relationship(back_populates="enrollments")
    course: Mapped["Course"] = relationship(back_populates="enrollments")

    __table_args__ = (
        UniqueConstraint("student_id", "course_id", name="uq_enrollments_student_course"),
        Index("ix_enrollments_student_id", "student_id"),
        Index("ix_enrollments_course_id", "course_id"),
        Index("ix_enrollments_is_active", "is_active"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Enrollment student={self.student_id} course={self.course_id}>"
