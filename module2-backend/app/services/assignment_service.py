"""Assigning instructors to courses and TAs to sessions.

Who may do what:

    instructor -> course   admin only. Deciding who teaches a course is an
                           institutional decision, not a self-service one.
    TA -> session          admin, or an instructor assigned to that session's
                           course. The instructor knows which TA is running
                           which tutorial; routing every swap through an admin
                           would make the admin a bottleneck for a decision
                           they have no information about.
"""

from __future__ import annotations

from typing import Dict, List

from sqlalchemy import select

from ..models import (
    AttendanceSession,
    AuditAction,
    Course,
    CourseInstructor,
    SessionTA,
    User,
    UserRole,
)
from .audit_service import AuditService
from .base import BaseService, transactional
from .exceptions import NotFoundError, PermissionDeniedError, ValidationError
from .staff_scope import can_manage_course
from .user_service import UserService


def _person(user: User) -> Dict[str, str]:
    return {"id": user.id, "email": user.email, "full_name": user.full_name,
            "role": user.role.value}


class AssignmentService(BaseService):
    def __init__(self, session) -> None:
        super().__init__(session)
        self.audit = AuditService(session)

    # -- lookups ------------------------------------------------------------

    def _course(self, course_id: str) -> Course:
        course = self.session.get(Course, course_id)
        if course is None:
            raise NotFoundError(f"No course with id {course_id}")
        return course

    def _session(self, session_id: str) -> AttendanceSession:
        found = self.session.get(AttendanceSession, session_id)
        if found is None:
            raise NotFoundError(f"No session with id {session_id}")
        return found

    def _user_with_role(self, user_id: str, role: UserRole) -> User:
        user = self.session.get(User, user_id)
        if user is None:
            raise NotFoundError(f"No user with id {user_id}")
        if user.role != role:
            raise ValidationError(f"{user.email} is not a {role.value}")
        if not user.is_active:
            raise ValidationError(f"{user.email} is deactivated")
        return user

    # -- instructors <-> courses -------------------------------------------

    def course_instructors(self, actor: User, course_id: str) -> List[Dict[str, str]]:
        UserService.require_role(actor, UserRole.ADMIN, UserRole.INSTRUCTOR, UserRole.TA)
        self._course(course_id)
        rows = self.session.scalars(
            select(User).join(CourseInstructor, CourseInstructor.instructor_id == User.id)
            .where(CourseInstructor.course_id == course_id).order_by(User.full_name))
        return [_person(u) for u in rows]

    @transactional
    def assign_instructor(self, actor: User, course_id: str, instructor_id: str) -> Dict[str, str]:
        UserService.require_role(actor, UserRole.ADMIN)
        self._course(course_id)
        instructor = self._user_with_role(instructor_id, UserRole.INSTRUCTOR)
        existing = self.session.scalar(select(CourseInstructor).where(
            CourseInstructor.course_id == course_id,
            CourseInstructor.instructor_id == instructor_id))
        if existing is None:
            self.session.add(CourseInstructor(course_id=course_id, instructor_id=instructor_id,
                                              assigned_by_id=actor.id))
            self.session.flush()
            self.audit.record(AuditAction.INSTRUCTOR_ASSIGNED, user_id=actor.id,
                              resource_type="course", resource_id=course_id,
                              details={"instructor_id": instructor_id})
        return _person(instructor)

    @transactional
    def unassign_instructor(self, actor: User, course_id: str, instructor_id: str) -> None:
        UserService.require_role(actor, UserRole.ADMIN)
        link = self.session.scalar(select(CourseInstructor).where(
            CourseInstructor.course_id == course_id,
            CourseInstructor.instructor_id == instructor_id))
        if link is None:
            raise NotFoundError("That instructor is not assigned to this course")
        self.session.delete(link)
        self.session.flush()
        self.audit.record(AuditAction.INSTRUCTOR_UNASSIGNED, user_id=actor.id,
                          resource_type="course", resource_id=course_id,
                          details={"instructor_id": instructor_id})

    # -- TAs <-> sessions ---------------------------------------------------

    def _require_ta_manager(self, actor: User, session: AttendanceSession) -> None:
        if actor.role == UserRole.ADMIN:
            return
        if actor.role == UserRole.INSTRUCTOR and (
                session.instructor_id == actor.id
                or can_manage_course(self.session, actor, session.course_id)):
            return
        raise PermissionDeniedError(
            "Only an admin or an instructor of this course can assign its TAs")

    def session_tas(self, actor: User, session_id: str) -> List[Dict[str, str]]:
        UserService.require_role(actor, UserRole.ADMIN, UserRole.INSTRUCTOR, UserRole.TA)
        self._session(session_id)
        rows = self.session.scalars(
            select(User).join(SessionTA, SessionTA.ta_id == User.id)
            .where(SessionTA.session_id == session_id).order_by(User.full_name))
        return [_person(u) for u in rows]

    @transactional
    def assign_ta(self, actor: User, session_id: str, ta_id: str) -> Dict[str, str]:
        session = self._session(session_id)
        self._require_ta_manager(actor, session)
        ta = self._user_with_role(ta_id, UserRole.TA)
        existing = self.session.scalar(select(SessionTA).where(
            SessionTA.session_id == session_id, SessionTA.ta_id == ta_id))
        if existing is None:
            self.session.add(SessionTA(session_id=session_id, ta_id=ta_id,
                                       assigned_by_id=actor.id))
            self.session.flush()
            self.audit.record(AuditAction.TA_ASSIGNED, user_id=actor.id,
                              resource_type="session", resource_id=session_id,
                              details={"ta_id": ta_id})
        return _person(ta)

    @transactional
    def unassign_ta(self, actor: User, session_id: str, ta_id: str) -> None:
        session = self._session(session_id)
        self._require_ta_manager(actor, session)
        link = self.session.scalar(select(SessionTA).where(
            SessionTA.session_id == session_id, SessionTA.ta_id == ta_id))
        if link is None:
            raise NotFoundError("That TA is not assigned to this session")
        self.session.delete(link)
        self.session.flush()
        self.audit.record(AuditAction.TA_UNASSIGNED, user_id=actor.id,
                          resource_type="session", resource_id=session_id,
                          details={"ta_id": ta_id})

    def staff_directory(self, actor: User, role: UserRole) -> List[Dict[str, str]]:
        """Active instructors or TAs, for the assignment pickers.

        GET /users/ is admin-only and returns every account. An instructor
        choosing a TA needs names and emails of TAs, and nothing else.
        """
        if role == UserRole.TA:
            UserService.require_role(actor, UserRole.ADMIN, UserRole.INSTRUCTOR)
        elif role == UserRole.INSTRUCTOR:
            UserService.require_role(actor, UserRole.ADMIN)
        else:
            raise ValidationError("Only instructor and ta directories are available")
        rows = self.session.scalars(
            select(User).where(User.role == role, User.is_active.is_(True))
            .order_by(User.full_name))
        return [_person(u) for u in rows]
