"""Attendance session lifecycle."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from ..models import AttendanceSession, AuditAction, SessionStatus, User, UserRole
from ..models.base import utcnow
from ..repositories import (
    AttendanceSessionRepository,
    CheckInRepository,
    CourseRepository,
    EnrollmentRepository,
    Page,
)
from .audit_service import AuditService
from .base import BaseService, transactional
from .exceptions import BusinessRuleError, NotFoundError, PermissionDeniedError, ValidationError
from .user_service import UserService
from .staff_scope import (
    can_see_session,
    student_may_view_session,
    student_session_filter,
    course_has_instructors,
    require_session_visible,
    teaches_course,
    visible_session_ids,
)

_SESSION_MANAGERS = (UserRole.ADMIN, UserRole.INSTRUCTOR)

# Only these moves are legal, per the API specification. Cancelling is allowed
# from anywhere; a cancelled or closed session is terminal.
_ALLOWED_TRANSITIONS: Dict[SessionStatus, tuple] = {
    SessionStatus.SCHEDULED: (SessionStatus.ACTIVE, SessionStatus.CANCELLED),
    SessionStatus.ACTIVE: (SessionStatus.CLOSED, SessionStatus.CANCELLED),
    SessionStatus.CLOSED: (SessionStatus.CANCELLED,),
    SessionStatus.CANCELLED: (),
}

QR_CODE_TTL_SECONDS = 30


class SessionService(BaseService):
    def __init__(self, session) -> None:
        super().__init__(session)
        self.sessions = AttendanceSessionRepository(session)
        self.courses = CourseRepository(session)
        self.checkins = CheckInRepository(session)
        self.enrollments = EnrollmentRepository(session)
        self.audit = AuditService(session)

    # -- queries ------------------------------------------------------------

    def get(self, session_id: str) -> AttendanceSession:
        found = self.sessions.find_by_id(session_id)
        if found is None:
            raise NotFoundError(f"No session with id {session_id}")
        return found

    def list(self, **filters) -> Page[AttendanceSession]:
        return self.sessions.search(**filters)

    def open_for_checkin(self, now: Optional[datetime] = None) -> List[AttendanceSession]:
        return self.sessions.find_open_for_checkin(now)

    def list_mine(self, actor: User, limit: int = 500) -> List[AttendanceSession]:
        """The sessions this staff member is responsible for.

        TA: assigned sessions. Instructor: sessions of the courses they teach
        plus any they created. Admin: everything. Newest first.
        """
        UserService.require_role(actor, UserRole.ADMIN, UserRole.INSTRUCTOR, UserRole.TA)
        from sqlalchemy import select

        stmt = select(AttendanceSession).order_by(AttendanceSession.scheduled_start.desc())
        ids = visible_session_ids(self.session, actor)
        if ids is not None:
            if not ids:
                return []
            stmt = stmt.where(AttendanceSession.id.in_(ids))
        return list(self.session.scalars(stmt.limit(limit)))

    # -- ownership ----------------------------------------------------------

    def require_owner_or_admin(self, actor: User, session: AttendanceSession) -> None:
        """Admins manage any session. Instructors manage sessions they created
        and every session of a course they are assigned to teach, so a
        co-instructor can cover for a colleague."""
        if actor.role == UserRole.ADMIN:
            return
        if actor.role == UserRole.INSTRUCTOR and (
                session.instructor_id == actor.id
                or teaches_course(self.session, actor, session.course_id)):
            return
        raise PermissionDeniedError("You do not manage this session")

    # -- lifecycle ----------------------------------------------------------

    @transactional
    def create(self, actor: User, course_id: str, **fields) -> AttendanceSession:
        UserService.require_role(actor, *_SESSION_MANAGERS)
        if self.courses.find_by_id(course_id) is None:
            raise NotFoundError(f"No course with id {course_id}")
        # Once an admin has assigned instructors to a course, only they may
        # schedule its sessions. A course with nobody assigned yet stays open
        # to any instructor, so nothing breaks before assignments are set up.
        if (actor.role == UserRole.INSTRUCTOR
                and course_has_instructors(self.session, course_id)
                and not teaches_course(self.session, actor, course_id)):
            raise PermissionDeniedError("You are not assigned to teach this course")

        group_id = fields.pop("group_id", None)
        fields.setdefault("instructor_id", actor.id)
        session = AttendanceSession(course_id=course_id, **fields)
        self._validate_window(session)
        session = self.sessions.save(session)
        self._set_group(session, group_id)
        self.audit.record(
            AuditAction.SESSION_CREATED, user_id=actor.id, resource_type="session",
            resource_id=session.id, details={"course_id": course_id},
        )
        return session

    @transactional
    def update(self, actor: User, session_id: str, **changes) -> AttendanceSession:
        session = self.get(session_id)
        self.require_owner_or_admin(actor, session)
        if "group_id" in changes:
            self._set_group(session, changes.pop("group_id"))
        for field, value in changes.items():
            if value is not None and hasattr(session, field):
                setattr(session, field, value)
        self._validate_window(session)
        self.session.flush()
        self.audit.record(
            AuditAction.SESSION_UPDATED, user_id=actor.id, resource_type="session",
            resource_id=session.id, details={"fields": sorted(changes)},
        )
        return session

    @transactional
    def change_status(
        self, actor: User, session_id: str, status: SessionStatus
    ) -> AttendanceSession:
        session = self.get(session_id)
        # A TA assigned to the session may open and close it - that is the
        # job of whoever is standing at the front of the tutorial room - but
        # may not cancel it, which is a scheduling decision.
        ta_running_it = (actor.role == UserRole.TA
                         and status in (SessionStatus.ACTIVE, SessionStatus.CLOSED)
                         and can_see_session(self.session, actor, session))
        if not ta_running_it:
            self.require_owner_or_admin(actor, session)
        if status != session.status and status not in _ALLOWED_TRANSITIONS[session.status]:
            raise BusinessRuleError(
                f"Cannot move a session from {session.status.value} to {status.value}"
            )
        self.sessions.set_status(session, status)
        self.audit.record(
            AuditAction.SESSION_UPDATED, user_id=actor.id, resource_type="session",
            resource_id=session.id, details={"status": status.value},
        )
        return session

    @transactional
    def delete(self, actor: User, session_id: str) -> None:
        """Only ``scheduled`` sessions may be deleted; anything with attendance
        history must be cancelled so the records survive."""
        session = self.get(session_id)
        self.require_owner_or_admin(actor, session)
        if session.status != SessionStatus.SCHEDULED:
            raise BusinessRuleError(
                "Only scheduled sessions can be deleted; cancel this one instead"
            )
        self.sessions.delete(session)
        self.audit.record(
            AuditAction.SESSION_DELETED, user_id=actor.id, resource_type="session",
            resource_id=session_id,
        )

    # -- QR codes -----------------------------------------------------------

    @transactional
    def rotate_qr_code(self, actor: User, session_id: str) -> AttendanceSession:
        """Issue a short-lived QR secret.

        The TTL is what makes the code hard to relay: a screenshot shared with
        someone off-site expires within seconds.
        """
        session = self.get(session_id)
        self.require_owner_or_admin(actor, session)
        session.qr_code_secret = secrets.token_urlsafe(32)[:64]
        session.qr_code_expires_at = utcnow() + timedelta(seconds=QR_CODE_TTL_SECONDS)
        self.session.flush()
        return session

    def verify_qr_code(self, session: AttendanceSession, secret: Optional[str]) -> bool:
        if not secret or not session.qr_code_secret:
            return False
        if session.qr_code_expires_at and session.qr_code_expires_at < utcnow():
            return False
        return secrets.compare_digest(session.qr_code_secret, secret)

    # -- statistics ---------------------------------------------------------

    def statistics(self, actor: User, session_id: str) -> Dict[str, object]:
        UserService.require_role(actor, UserRole.ADMIN, UserRole.INSTRUCTOR, UserRole.TA)
        session = self.get(session_id)
        require_session_visible(self.session, actor, session)
        by_status = self.checkins.count_by_status(session_id)
        from .staff_scope import session_roster_ids

        enrolled = len(session_roster_ids(self.session, session))
        present = by_status.get("approved", 0)
        return {
            "session_id": session_id,
            "status": session.status.value,
            "enrolled": enrolled,
            "by_status": by_status,
            "attendance_rate": round(present / enrolled, 4) if enrolled else 0.0,
        }

    def students(self, actor: User, session_id: str) -> List[Dict[str, object]]:
        """Everyone expected at this session, and whether they checked in.

        The session's group for a tutorial or lab, else the whole course; an
        absentee is listed with status "absent". Staff only, scoped like the
        session itself: an admin, the course's instructors, or a TA assigned to
        this session. Reading the list is not an export, so unlike
        GET /export/session it writes no audit entry.
        """
        UserService.require_role(actor, UserRole.ADMIN, UserRole.INSTRUCTOR, UserRole.TA)
        session = self.get(session_id)
        require_session_visible(self.session, actor, session)
        from .stats_service import StatsService

        return StatsService(self.session).session_attendance(session_id)["records"]

    # -- groups -------------------------------------------------------------

    def _set_group(self, session: AttendanceSession, group_id: Optional[str]) -> None:
        """Tie a tutorial / lab session to one group, or clear it (lecture).

        The group must belong to the session's course and match its type: a
        tutorial session takes a tutorial group, a lab session a lab group.
        """
        from ..models import CourseGroup, SessionGroup

        if not group_id:
            session.group_link = None
            self.session.flush()
            return
        group = self.session.get(CourseGroup, group_id)
        if group is None or group.course_id != session.course_id:
            raise ValidationError("That group does not belong to this course")
        stype = session.session_type.value if hasattr(session.session_type, "value") else str(session.session_type)
        if stype in ("tutorial", "lab") and group.kind != stype:
            raise ValidationError(f"A {stype} session needs a {stype} group")
        if stype not in ("tutorial", "lab"):
            raise ValidationError("Only tutorial and lab sessions are given a group")
        if session.group_link is None:
            session.group_link = SessionGroup(session_id=session.id, group_id=group.id)
        else:
            session.group_link.group_id = group.id
        self.session.flush()
        self.session.refresh(session, ["group_link"])

    # -- student view -----------------------------------------------------

    def list_for_student(self, student: User, **filters) -> Page[AttendanceSession]:
        """Only sessions this student may attend: their courses, and for a
        tutorial / lab, only their own group's sessions."""
        from sqlalchemy import select

        stmt = select(AttendanceSession).where(student_session_filter(self.session, student.id))
        for key, column in (("course_id", AttendanceSession.course_id),
                            ("status", AttendanceSession.status),
                            ("instructor_id", AttendanceSession.instructor_id)):
            if filters.get(key) is not None:
                stmt = stmt.where(column == filters[key])
        if filters.get("starts_after") is not None:
            stmt = stmt.where(AttendanceSession.scheduled_start >= filters["starts_after"])
        if filters.get("starts_before") is not None:
            stmt = stmt.where(AttendanceSession.scheduled_start <= filters["starts_before"])
        return self.sessions.paginate(stmt.order_by(AttendanceSession.scheduled_start.desc()),
                                      filters.get("limit", 50), filters.get("offset", 0))

    def get_for(self, actor: User, session_id: str) -> AttendanceSession:
        """A student asking for another group's tutorial / lab gets 404, not
        403: the session's existence is not theirs to know. Lectures stay
        readable by any signed-in user, as the API spec requires."""
        session = self.get(session_id)
        if actor.role == UserRole.STUDENT and not student_may_view_session(self.session, actor, session):
            raise NotFoundError(f"No session with id {session_id}")
        return session

    def open_for(self, actor: Optional[User], now: Optional[datetime] = None) -> List[AttendanceSession]:
        sessions = self.open_for_checkin(now)
        if actor is not None and actor.role == UserRole.STUDENT:
            sessions = [s for s in sessions if student_may_view_session(self.session, actor, s)]
        return sessions

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _validate_window(session: AttendanceSession) -> None:
        """Mirror the CHECK constraints so a bad window is a 422, not a 500.

        The database would reject these anyway; catching them here turns an
        IntegrityError into a message the client can act on.
        """
        if session.scheduled_end <= session.scheduled_start:
            raise ValidationError("scheduled_end must be after scheduled_start")
        if session.checkin_closes_at <= session.checkin_opens_at:
            raise ValidationError("checkin_closes_at must be after checkin_opens_at")