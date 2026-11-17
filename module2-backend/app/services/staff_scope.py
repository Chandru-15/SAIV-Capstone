"""Which courses and sessions a staff member is responsible for.

The single answer to "may this instructor / TA see or act on this?". Every
check-in list, review and export goes through here, so the rule from the
Module 4 brief holds on the server, not just in the dashboard UI:

    TA          sessions they are assigned to
    Instructor  courses they are assigned to teach, plus any session they
                created themselves
    Admin       everything

A hidden button is not a secure endpoint: these checks are what actually stop
an instructor reading another course's attendance by calling the API directly.
"""

from __future__ import annotations

from typing import List, Optional, Set

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import (
    AttendanceSession,
    CourseInstructor,
    Enrollment,
    GroupMember,
    SessionGroup,
    SessionTA,
    User,
    UserRole,
)
from .exceptions import PermissionDeniedError


def instructor_course_ids(db: Session, user_id: str) -> Set[str]:
    return set(db.scalars(
        select(CourseInstructor.course_id).where(CourseInstructor.instructor_id == user_id)))


def ta_session_ids(db: Session, user_id: str) -> Set[str]:
    return set(db.scalars(select(SessionTA.session_id).where(SessionTA.ta_id == user_id)))


def course_has_instructors(db: Session, course_id: str) -> bool:
    return db.scalar(
        select(CourseInstructor.id).where(CourseInstructor.course_id == course_id).limit(1)
    ) is not None


def teaches_course(db: Session, user: User, course_id: str) -> bool:
    return user.role == UserRole.INSTRUCTOR and course_id in instructor_course_ids(db, user.id)


def visible_session_ids(db: Session, user: User) -> Optional[Set[str]]:
    """Session ids this staff member may see. ``None`` means "all" (admin).

    Students get an empty set: their own records are reached through the
    ``/me`` endpoints, never through staff lists.
    """
    if user.role == UserRole.ADMIN:
        return None
    if user.role == UserRole.INSTRUCTOR:
        courses = instructor_course_ids(db, user.id)
        condition = AttendanceSession.instructor_id == user.id
        if courses:
            condition = or_(condition, AttendanceSession.course_id.in_(courses))
        return set(db.scalars(select(AttendanceSession.id).where(condition)))
    if user.role == UserRole.TA:
        return ta_session_ids(db, user.id)
    return set()


def can_see_session(db: Session, user: User, session: AttendanceSession) -> bool:
    if user.role == UserRole.ADMIN:
        return True
    if user.role == UserRole.INSTRUCTOR:
        return (session.instructor_id == user.id
                or session.course_id in instructor_course_ids(db, user.id))
    if user.role == UserRole.TA:
        return session.id in ta_session_ids(db, user.id)
    return False


def require_session_visible(db: Session, user: User, session: AttendanceSession) -> None:
    if not can_see_session(db, user, session):
        raise PermissionDeniedError("This session is not one of yours")


def can_manage_course(db: Session, user: User, course_id: str) -> bool:
    """Admin, or an instructor assigned to the course.

    A course nobody has been assigned to yet stays open to any instructor
    (the same rule session creation uses), so existing flows keep working
    until the admin assigns staff.
    """
    if user.role == UserRole.ADMIN:
        return True
    if user.role != UserRole.INSTRUCTOR:
        return False
    return teaches_course(db, user, course_id) or not course_has_instructors(db, course_id)


def can_view_course(db: Session, user: User, course_id: str) -> bool:
    """Admin; an instructor who teaches it (or any instructor while nobody is
    assigned) or created one of its sessions; a TA assigned to one of its
    sessions."""
    if user.role == UserRole.ADMIN:
        return True
    if user.role == UserRole.INSTRUCTOR:
        if can_manage_course(db, user, course_id):
            return True
        return db.scalar(select(AttendanceSession.id).where(
            AttendanceSession.course_id == course_id,
            AttendanceSession.instructor_id == user.id).limit(1)) is not None
    if user.role == UserRole.TA:
        return db.scalar(
            select(AttendanceSession.id)
            .join(SessionTA, SessionTA.session_id == AttendanceSession.id)
            .where(AttendanceSession.course_id == course_id, SessionTA.ta_id == user.id)
            .limit(1)) is not None
    return False


def require_course_visible(db: Session, user: User, course_id: str) -> None:
    if not can_view_course(db, user, course_id):
        raise PermissionDeniedError("This course is not one of yours")


# -- students ------------------------------------------------------------------
# A student sees a session only if they are enrolled in its course AND, for a
# tutorial or lab session, they are in that session's group. Sessions they
# cannot attend are not merely refused at check-in: they are not listed at all.


def student_course_ids(db: Session, student_id: str) -> Set[str]:
    return set(db.scalars(select(Enrollment.course_id).where(
        Enrollment.student_id == student_id, Enrollment.is_active.is_(True))))


def student_group_ids(db: Session, student_id: str) -> Set[str]:
    return set(db.scalars(select(GroupMember.group_id).where(
        GroupMember.student_id == student_id)))


def student_session_filter(db: Session, student_id: str):
    """A WHERE clause for AttendanceSession limited to what this student may see."""
    courses = student_course_ids(db, student_id)
    if not courses:
        return AttendanceSession.id.is_(None)
    groups = student_group_ids(db, student_id)
    grouped = select(SessionGroup.session_id)
    allowed_grouped = select(SessionGroup.session_id).where(
        SessionGroup.group_id.in_(groups)) if groups else None
    in_course = AttendanceSession.course_id.in_(courses)
    ungrouped = AttendanceSession.id.not_in(grouped)
    if allowed_grouped is None:
        return in_course & ungrouped
    return in_course & or_(ungrouped, AttendanceSession.id.in_(allowed_grouped))


def student_can_see_session(db: Session, student: User, session: AttendanceSession) -> bool:
    if session.course_id not in student_course_ids(db, student.id):
        return False
    link = session.group_link
    return link is None or link.group_id in student_group_ids(db, student.id)


def student_may_view_session(db: Session, student: User, session: AttendanceSession) -> bool:
    """Read access to one session's details (``GET /sessions/{id}``,
    ``/sessions/active``).

    Session details are readable by any signed-in user, as the API spec
    requires; the one exception is a tutorial / lab session belonging to a
    group the student is not in. Checking in is stricter and still needs
    enrolment (see student_can_see_session and the check-in service).
    """
    link = session.group_link
    return link is None or link.group_id in student_group_ids(db, student.id)


def session_roster_ids(db: Session, session: AttendanceSession) -> List[str]:
    """Who is expected at this session: its group, or the whole course."""
    if session.group_link is not None:
        return list(db.scalars(select(GroupMember.student_id).where(
            GroupMember.group_id == session.group_link.group_id)))
    return list(db.scalars(select(Enrollment.student_id).where(
        Enrollment.course_id == session.course_id, Enrollment.is_active.is_(True))))

# -- face re-enrolment review --------------------------------------------------


def visible_student_ids(db: Session, user: User) -> Optional[Set[str]]:
    """Students this staff member may decide for. ``None`` means "all" (admin).

    Mirrors :func:`visible_session_ids`, but over people rather than sessions:
    an instructor answers for the students actively enrolled in the courses
    they teach, and a TA answers for nobody. Approving a face re-enrolment
    replaces the biometric reference every later check-in is matched against,
    which is an instructor's decision in the same way resolving a flagged
    check-in is -- see ``CheckInService.review``.

    An instructor who teaches nothing yet gets an empty set, not everything.
    ``can_manage_course``'s "a course with no instructors stays open to any
    instructor" rule is deliberately NOT applied here: it exists so course
    setup is not blocked, and it should not quietly widen who may authorise a
    template replacement.
    """
    if user.role == UserRole.ADMIN:
        return None
    if user.role != UserRole.INSTRUCTOR:
        return set()
    courses = instructor_course_ids(db, user.id)
    if not courses:
        return set()
    return set(db.scalars(select(Enrollment.student_id).where(
        Enrollment.course_id.in_(courses), Enrollment.is_active.is_(True))))


def can_review_student(db: Session, user: User, student_id: str) -> bool:
    allowed = visible_student_ids(db, user)
    return allowed is None or student_id in allowed
