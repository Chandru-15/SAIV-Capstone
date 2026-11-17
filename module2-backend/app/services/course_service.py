"""Course management."""

from __future__ import annotations

from typing import List, Optional

from ..models import Course, User, UserRole
from ..repositories import CourseRepository, EnrollmentRepository, Page
from .audit_service import AuditService
from .base import BaseService, transactional
from .exceptions import ConflictError, NotFoundError
from .user_service import UserService

# Creating and deleting a course is admin-only per API-SPECIFICATION.md
# (line 295: "Create a new course. Requires auth (admin only)"). Updating is
# wider - line 315 allows "admin or course instructor" - so the two use
# different tuples.
_COURSE_CREATORS = (UserRole.ADMIN,)
# Only admins edit a course. Instructors run its sessions, but its details,
# venue and check-in rules are set centrally.
_COURSE_EDITORS = (UserRole.ADMIN,)


class CourseService(BaseService):
    def __init__(self, session) -> None:
        super().__init__(session)
        self.courses = CourseRepository(session)
        self.enrollments = EnrollmentRepository(session)
        self.audit = AuditService(session)

    def get(self, course_id: str) -> Course:
        course = self.courses.find_by_id(course_id)
        if course is None:
            raise NotFoundError(f"No course with id {course_id}")
        return course

    def list(
        self, semester: Optional[str] = None, is_active: Optional[bool] = True,
        limit: int = 50, offset: int = 0,
    ) -> Page[Course]:
        return self.courses.search(semester=semester, is_active=is_active,
                                   limit=limit, offset=offset)

    def list_for_student(self, student_id: str) -> List[Course]:
        return self.courses.find_for_student(student_id)

    @transactional
    def create(self, actor: User, **fields) -> Course:
        UserService.require_role(actor, *_COURSE_CREATORS)
        has_tutorials = bool(fields.pop("has_tutorials", False))
        has_labs = bool(fields.pop("has_labs", False))
        code = str(fields.get("code", "")).strip().upper()
        if self.courses.exists_by_code(code):
            raise ConflictError(f"Course {code} already exists")
        course = self.courses.save(Course(**{**fields, "code": code}))
        self.audit.record(
            "course_created", user_id=actor.id, resource_type="course",
            resource_id=course.id, details={"code": code},
        )
        from .group_service import GroupService

        return GroupService(self.session).set_structure(actor, course.id, has_tutorials, has_labs)

    @transactional
    def update(self, actor: User, course_id: str, **changes) -> Course:
        UserService.require_role(actor, *_COURSE_EDITORS)
        course = self.get(course_id)
        for field, value in changes.items():
            if value is not None and hasattr(course, field):
                setattr(course, field, value)
        self.session.flush()
        self.audit.record(
            "course_updated", user_id=actor.id, resource_type="course",
            resource_id=course.id, details={"fields": sorted(changes)},
        )
        return course

    @transactional
    def delete(self, actor: User, course_id: str) -> None:
        """Remove a course with its sessions, enrolments, groups and assignments.

        Admin only, and only while nobody has checked in to any of its
        sessions: attendance records are evidence and must not disappear with
        the course. Once a course has history it stays.
        """
        from sqlalchemy import func, select

        from ..models import AttendanceSession, CheckIn

        UserService.require_role(actor, *_COURSE_CREATORS)
        course = self.get(course_id)
        records = self.session.scalar(
            select(func.count(CheckIn.id))
            .join(AttendanceSession, AttendanceSession.id == CheckIn.session_id)
            .where(AttendanceSession.course_id == course_id)
        ) or 0
        if records:
            raise ConflictError(
                f"{course.code} has {records} attendance record(s), so it can't be deleted. "
                "A course can only be deleted before anyone has checked in."
            )
        code = course.code
        self.session.delete(course)
        self.session.flush()
        self.audit.record(
            "course_deleted", user_id=actor.id, resource_type="course",
            resource_id=course_id, details={"code": code},
        )

    def enrolled_count(self, course_id: str) -> int:
        return self.enrollments.count_by_course(course_id)