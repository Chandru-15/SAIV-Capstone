"""Repository for :class:`~app.models.enrollment.Enrollment`."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import func, select

from ..models import Enrollment
from ..models.base import utcnow
from .base import DEFAULT_LIMIT, BaseRepository, Page


class EnrollmentRepository(BaseRepository[Enrollment]):
    model = Enrollment

    def find_by_student_and_course(
        self, student_id: str, course_id: str
    ) -> Optional[Enrollment]:
        """Hits the unique index ``uq_enrollments_student_course``."""
        stmt = select(Enrollment).where(
            Enrollment.student_id == student_id,
            Enrollment.course_id == course_id,
        )
        return self.session.scalars(stmt).first()

    def is_enrolled(self, student_id: str, course_id: str) -> bool:
        """Authorisation check: may this student check in to this course?"""
        stmt = (
            select(func.count())
            .select_from(Enrollment)
            .where(
                Enrollment.student_id == student_id,
                Enrollment.course_id == course_id,
                Enrollment.is_active.is_(True),
            )
        )
        return bool(self.session.scalar(stmt))

    def find_by_course(
        self, course_id: str, active_only: bool = True,
        limit: int = DEFAULT_LIMIT, offset: int = 0,
    ) -> Page[Enrollment]:
        stmt = select(Enrollment).where(Enrollment.course_id == course_id)
        if active_only:
            stmt = stmt.where(Enrollment.is_active.is_(True))
        return self.paginate(stmt.order_by(Enrollment.enrolled_at), limit, offset)

    def find_by_student(self, student_id: str, active_only: bool = True) -> List[Enrollment]:
        stmt = select(Enrollment).where(Enrollment.student_id == student_id)
        if active_only:
            stmt = stmt.where(Enrollment.is_active.is_(True))
        return list(self.session.scalars(stmt.order_by(Enrollment.enrolled_at)))

    def count_by_course(self, course_id: str, active_only: bool = True) -> int:
        stmt = select(func.count()).select_from(Enrollment).where(
            Enrollment.course_id == course_id
        )
        if active_only:
            stmt = stmt.where(Enrollment.is_active.is_(True))
        return int(self.session.scalar(stmt) or 0)

    # -- domain operations --------------------------------------------------

    def enroll(self, student_id: str, course_id: str) -> Enrollment:
        """Idempotent enrolment.

        The unique constraint forbids a second row per (student, course), so a
        student who re-enrols after dropping reactivates the existing row
        instead of inserting. Doing this here keeps every caller from having to
        remember it.
        """
        existing = self.find_by_student_and_course(student_id, course_id)
        if existing is not None:
            if not existing.is_active:
                existing.is_active = True
                existing.dropped_at = None
                existing.enrolled_at = utcnow()
                self.session.flush()
            return existing
        return self.save(Enrollment(student_id=student_id, course_id=course_id))

    def drop(self, enrollment: Enrollment) -> Enrollment:
        """Soft drop: keeps the row so past check-ins stay explicable."""
        enrollment.is_active = False
        enrollment.dropped_at = utcnow()
        self.session.flush()
        return enrollment
