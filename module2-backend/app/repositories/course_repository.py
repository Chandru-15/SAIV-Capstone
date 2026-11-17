"""Repository for :class:`~app.models.course.Course`."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select

from ..models import Course
from .base import DEFAULT_LIMIT, BaseRepository, Page


class CourseRepository(BaseRepository[Course]):
    model = Course

    def find_by_code(self, code: str) -> Optional[Course]:
        stmt = select(Course).where(func.upper(Course.code) == code.upper())
        return self.session.scalars(stmt).first()

    def exists_by_code(self, code: str) -> bool:
        stmt = select(func.count()).select_from(Course).where(
            func.upper(Course.code) == code.upper()
        )
        return bool(self.session.scalar(stmt))

    def search(
        self,
        semester: Optional[str] = None,
        is_active: Optional[bool] = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Page[Course]:
        """Backs ``GET /courses``."""
        stmt = select(Course)
        if semester is not None:
            stmt = stmt.where(Course.semester == semester)
        if is_active is not None:
            stmt = stmt.where(Course.is_active.is_(is_active))
        return self.paginate(stmt.order_by(Course.code), limit, offset)

    def find_for_student(self, student_id: str, active_only: bool = True) -> list[Course]:
        """Courses the student is enrolled in — the join lives here so routers
        never have to know about the ``enrollments`` table."""
        from ..models import Enrollment

        stmt = (
            select(Course)
            .join(Enrollment, Enrollment.course_id == Course.id)
            .where(Enrollment.student_id == student_id)
            .order_by(Course.code)
        )
        if active_only:
            stmt = stmt.where(Enrollment.is_active.is_(True))
        return list(self.session.scalars(stmt))
