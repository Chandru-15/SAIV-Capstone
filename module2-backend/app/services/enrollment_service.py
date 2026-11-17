"""Enrollment management."""

from __future__ import annotations

from typing import Dict, Iterable, List

from ..models import AuditAction, Enrollment, User, UserRole
from ..repositories import (
    CourseRepository,
    EnrollmentRepository,
    Page,
    UserRepository,
)
from .audit_service import AuditService
from .base import BaseService, transactional
from .exceptions import BusinessRuleError, NotFoundError, ValidationError
from .staff_scope import can_manage_course, require_course_visible
from .user_service import UserService

_ENROLLMENT_MANAGERS = (UserRole.ADMIN, UserRole.INSTRUCTOR)


def _require_can_enrol(db, actor: User, course_id: str) -> None:
    """Admins enrol into any course; an instructor only into one they teach
    (or any course nobody has been assigned to yet)."""
    UserService.require_role(actor, *_ENROLLMENT_MANAGERS)
    if actor.role == UserRole.INSTRUCTOR and not can_manage_course(db, actor, course_id):
        from .exceptions import PermissionDeniedError

        raise PermissionDeniedError("You are not assigned to teach this course")


class EnrollmentService(BaseService):
    def __init__(self, session) -> None:
        super().__init__(session)
        self.enrollments = EnrollmentRepository(session)
        self.users = UserRepository(session)
        self.courses = CourseRepository(session)
        self.audit = AuditService(session)

    @transactional
    def enroll(self, actor: User, student_id: str, course_id: str) -> Enrollment:
        _require_can_enrol(self.session, actor, course_id)
        student = self.users.find_by_id(student_id)
        if student is None:
            raise NotFoundError(f"No user with id {student_id}")
        if student.role != UserRole.STUDENT:
            raise ValidationError("Only students can be enrolled in a course")
        if self.courses.find_by_id(course_id) is None:
            raise NotFoundError(f"No course with id {course_id}")

        enrollment = self.enrollments.enroll(student_id, course_id)
        self.audit.record(
            AuditAction.ENROLLMENT_ADDED, user_id=actor.id,
            resource_type="enrollment", resource_id=enrollment.id,
            details={"student_id": student_id, "course_id": course_id},
        )
        return enrollment

    @transactional
    def enroll_by_emails(
        self, actor: User, course_id: str, emails: Iterable[str]
    ) -> List[Dict[str, str]]:
        """Bulk enrolment, reporting per-address outcomes.

        Returns the ``{"email": ..., "status": ...}`` shape the API spec uses:
        ``enrolled``, ``already_enrolled`` or ``not_found``. One unknown
        address does not fail the batch.
        """
        _require_can_enrol(self.session, actor, course_id)
        if self.courses.find_by_id(course_id) is None:
            raise NotFoundError(f"No course with id {course_id}")

        wanted = [e.strip().lower() for e in emails]
        found = {u.email.lower(): u for u in self.users.find_by_emails(wanted)}

        results: List[Dict[str, str]] = []
        for email in wanted:
            student = found.get(email)
            if student is None or student.role != UserRole.STUDENT:
                results.append({"email": email, "status": "not_found"})
                continue
            already = self.enrollments.is_enrolled(student.id, course_id)
            self.enrollments.enroll(student.id, course_id)
            results.append(
                {"email": email, "status": "already_enrolled" if already else "enrolled"}
            )

        self.audit.record(
            AuditAction.ENROLLMENT_ADDED, user_id=actor.id,
            resource_type="course", resource_id=course_id,
            details={"requested": len(wanted),
                     "enrolled": sum(1 for r in results if r["status"] == "enrolled")},
        )
        return results

    def drop(self, actor: User, student_id: str, course_id: str) -> Enrollment:
        """Always refused: once enrolled, a student stays on the course.

        Their attendance record belongs to the course, so removing them would
        quietly rewrite its history. Kept as a method (rather than deleted) so
        any future caller gets a clear 400 instead of an AttributeError.
        """
        _require_can_enrol(self.session, actor, course_id)
        if self.enrollments.find_by_student_and_course(student_id, course_id) is None:
            raise NotFoundError("That student is not enrolled in this course")
        raise BusinessRuleError("Students cannot be removed from a course once they are enrolled")

    def my_enrollments(self, student: User) -> List[Dict[str, object]]:
        """A student's enrolments flattened with course details."""
        from sqlalchemy import select

        from ..models import Course

        rows = self.session.execute(
            select(Enrollment.id, Enrollment.course_id, Course.code, Course.name,
                   Course.semester, Enrollment.is_active, Enrollment.enrolled_at)
            .join(Course, Course.id == Enrollment.course_id)
            .where(Enrollment.student_id == student.id)
            .order_by(Course.code)).all()
        return [
            {"enrollment_id": eid, "course_id": cid, "course_code": code,
             "course_name": name, "semester": semester, "is_active": active,
             "enrolled_at": when.isoformat()}
            for eid, cid, code, name, semester, active, when in rows
        ]

    def course_roster(self, course_id: str) -> Dict[str, object]:
        """Every enrolled student on one course, with their details."""
        from sqlalchemy import select

        from ..models import Course, User as UserModel

        course = self.courses.find_by_id(course_id)
        if course is None:
            raise NotFoundError(f"No course with id {course_id}")
        rows = self.session.execute(
            select(UserModel.id, UserModel.full_name, UserModel.email,
                   Enrollment.is_active, Enrollment.enrolled_at)
            .join(Enrollment, Enrollment.student_id == UserModel.id)
            .where(Enrollment.course_id == course_id)
            .order_by(UserModel.full_name)).all()
        students = [
            {"student_id": sid, "full_name": name, "email": email,
             "is_active": active, "enrolled_at": when.isoformat()}
            for sid, name, email, active, when in rows
        ]
        return {
            "course_id": course_id,
            "course_code": course.code,
            "total_enrolled": sum(1 for s in students if s["is_active"]),
            "students": students,
        }

    def roster(self, actor: User, course_id: str, limit: int = 50, offset: int = 0) -> Page[Enrollment]:
        UserService.require_role(actor, UserRole.ADMIN, UserRole.INSTRUCTOR, UserRole.TA)
        require_course_visible(self.session, actor, course_id)
        return self.enrollments.find_by_course(course_id, limit=limit, offset=offset)