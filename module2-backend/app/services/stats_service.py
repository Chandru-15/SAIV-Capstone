"""Aggregate statistics for the observability dashboard.

Method names carry a `_stats` suffix on purpose: BaseService assigns
`self.session` to the SQLAlchemy Session, which would shadow a method named
`session` and turn `self.session(id)` into an attempt to call the Session
object.

Every figure is computed in SQL. The alternative — loading rows and counting
in Python — would mean pulling the whole check-in table into memory for a
panel that a dashboard polls on a timer.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List

from sqlalchemy import func, select

from ..models import (
    AttendanceSession,
    CheckIn,
    CheckInStatus,
    Course,
    Enrollment,
    SessionStatus,
    User,
    UserRole,
)
from ..models.base import utcnow
from .base import BaseService
from .exceptions import NotFoundError


def _rate(numerator: int, denominator: int) -> float:
    """Attendance and approval rates. Zero rather than a division error when
    nothing has happened yet — a session with no enrolments has a rate of 0,
    not an undefined one."""
    return round(numerator / denominator, 4) if denominator else 0.0


class StatsService(BaseService):
    # -- institution-wide ---------------------------------------------------

    def overview(self) -> Dict[str, Any]:
        count = lambda stmt: int(self.session.scalar(stmt) or 0)
        total_sessions = count(select(func.count()).select_from(AttendanceSession))
        active_sessions = count(select(func.count()).select_from(AttendanceSession)
                                .where(AttendanceSession.status == SessionStatus.ACTIVE))
        total_courses = count(select(func.count()).select_from(Course))
        total_students = count(select(func.count()).select_from(User)
                               .where(User.role == UserRole.STUDENT))

        # "Today" is midnight UTC, matching the storage convention. A
        # local-midnight boundary would shift the figure for every viewer.
        midnight = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_checkins = count(select(func.count()).select_from(CheckIn)
                               .where(CheckIn.checked_in_at >= midnight))
        flagged_pending = count(select(func.count()).select_from(CheckIn)
                                .where(CheckIn.status.in_((CheckInStatus.FLAGGED,
                                                           CheckInStatus.APPEALED))))
        total_checkins = count(select(func.count()).select_from(CheckIn))
        approved = count(select(func.count()).select_from(CheckIn)
                         .where(CheckIn.status == CheckInStatus.APPROVED))
        return {
            "total_sessions": total_sessions,
            "active_sessions": active_sessions,
            "total_courses": total_courses,
            "total_students": total_students,
            "total_checkins": total_checkins,
            "today_checkins": today_checkins,
            "flagged_pending": flagged_pending,
            "approval_rate": _rate(approved, total_checkins),
        }

    # -- per session --------------------------------------------------------

    def session_stats(self, session_id: str) -> Dict[str, Any]:
        found = self.session.get(AttendanceSession, session_id)
        if found is None:
            raise NotFoundError(f"No session with id {session_id}")

        by_status = dict(self.session.execute(
            select(CheckIn.status, func.count())
            .where(CheckIn.session_id == session_id)
            .group_by(CheckIn.status)).all())
        counts = {(k.value if hasattr(k, "value") else str(k)): v
                  for k, v in by_status.items()}

        # A tutorial / lab session's roster is its group, not the whole course.
        from .staff_scope import session_roster_ids

        enrolled = len(session_roster_ids(self.session, found))
        checked_in = sum(counts.values())
        approved = counts.get("approved", 0)
        average_risk = self.session.scalar(
            select(func.avg(CheckIn.risk_score)).where(CheckIn.session_id == session_id))
        return {
            "session_id": session_id,
            "session_name": found.name,
            "course_id": found.course_id,
            "status": found.status.value,
            "total_enrolled": enrolled,
            "checked_in_count": checked_in,
            "approved_count": approved,
            "flagged_count": counts.get("flagged", 0) + counts.get("appealed", 0),
            "rejected_count": counts.get("rejected", 0),
            "by_status": counts,
            # Attendance counts APPROVED only: a flagged check-in has not been
            # accepted yet, and counting it would overstate attendance in
            # exactly the cases a reviewer is meant to look at.
            "attendance_rate": _rate(approved, enrolled),
            "average_risk_score": round(float(average_risk), 4) if average_risk is not None else 0.0,
        }

    # -- per course ---------------------------------------------------------

    def course_stats(self, course_id: str) -> Dict[str, Any]:
        found = self.session.get(Course, course_id)
        if found is None:
            raise NotFoundError(f"No course with id {course_id}")

        enrolled = int(self.session.scalar(
            select(func.count()).select_from(Enrollment)
            .where(Enrollment.course_id == course_id,
                   Enrollment.is_active.is_(True))) or 0)
        session_ids = list(self.session.scalars(
            select(AttendanceSession.id).where(AttendanceSession.course_id == course_id)))

        approved = flagged = 0
        if session_ids:
            approved = int(self.session.scalar(
                select(func.count()).select_from(CheckIn)
                .where(CheckIn.session_id.in_(session_ids),
                       CheckIn.status == CheckInStatus.APPROVED)) or 0)
            flagged = int(self.session.scalar(
                select(func.count()).select_from(CheckIn)
                .where(CheckIn.session_id.in_(session_ids),
                       CheckIn.status.in_((CheckInStatus.FLAGGED,
                                           CheckInStatus.APPEALED)))) or 0)
        # Mean attendance across sessions = approvals / (sessions x enrolled).
        opportunities = len(session_ids) * enrolled
        return {
            "course_id": course_id,
            "course_code": found.code,
            "course_name": found.name,
            "semester": found.semester,
            "total_enrolled": enrolled,
            "total_sessions": len(session_ids),
            "total_approved_checkins": approved,
            "flagged_checkins": flagged,
            "average_attendance_rate": _rate(approved, opportunities),
        }

    # -- per student --------------------------------------------------------

    def student_stats(self, student_id: str, recent: int = 5) -> Dict[str, Any]:
        found = self.session.get(User, student_id)
        if found is None:
            raise NotFoundError(f"No user with id {student_id}")

        course_ids = list(self.session.scalars(
            select(Enrollment.course_id).where(Enrollment.student_id == student_id,
                                               Enrollment.is_active.is_(True))))
        total_sessions = 0
        if course_ids:
            total_sessions = int(self.session.scalar(
                select(func.count()).select_from(AttendanceSession)
                .where(AttendanceSession.course_id.in_(course_ids))) or 0)
        attended = int(self.session.scalar(
            select(func.count()).select_from(CheckIn)
            .where(CheckIn.student_id == student_id,
                   CheckIn.status == CheckInStatus.APPROVED)) or 0)

        rows = self.session.execute(
            select(CheckIn.session_id, AttendanceSession.name, CheckIn.status,
                   CheckIn.checked_in_at, CheckIn.risk_score)
            .join(AttendanceSession, AttendanceSession.id == CheckIn.session_id)
            .where(CheckIn.student_id == student_id)
            .order_by(CheckIn.checked_in_at.desc()).limit(recent)).all()
        return {
            "student_id": student_id,
            "student_name": found.full_name,
            "total_enrolled_courses": len(course_ids),
            "total_sessions": total_sessions,
            "attended_sessions": attended,
            "attendance_rate": _rate(attended, total_sessions),
            "recent_sessions": [
                {"session_id": sid, "session_name": name,
                 "status": status.value if hasattr(status, "value") else str(status),
                 "checked_in_at": when.isoformat(), "risk_score": risk}
                for sid, name, status, when, risk in rows
            ],
        }

    # -- export -------------------------------------------------------------

    def session_attendance(self, session_id: str) -> Dict[str, Any]:
        """Per-student attendance for one session, including absentees.

        Absentees matter more than attendees for an attendance export, and they
        have no check-in row — so this starts from the enrolment list and left-
        joins, rather than listing check-ins.
        """
        stats = self.session_stats(session_id)
        found = self.session.get(AttendanceSession, session_id)

        from .staff_scope import session_roster_ids

        roster = session_roster_ids(self.session, found)
        rows = self.session.execute(
            select(User.id, User.full_name, User.email, CheckIn.status,
                   CheckIn.checked_in_at, CheckIn.risk_score)
            .outerjoin(CheckIn, (CheckIn.student_id == User.id)
                       & (CheckIn.session_id == session_id))
            .where(User.id.in_(roster) if roster else User.id.is_(None))
            .order_by(User.full_name)).all()
        records: List[Dict[str, Any]] = [
            {"student_id": sid, "student_name": name, "student_email": email,
             "status": (status.value if hasattr(status, "value") else status) or "absent",
             "checked_in_at": when.isoformat() if when else None,
             "risk_score": risk}
            for sid, name, email, status, when, risk in rows
        ]
        return {
            "session_id": session_id,
            "session_name": found.name,
            "course_id": found.course_id,
            "exported_at": utcnow().isoformat(),
            "summary": {
                "total_enrolled": stats["total_enrolled"],
                "checked_in_count": stats["checked_in_count"],
                "approved_count": stats["approved_count"],
                "flagged_count": stats["flagged_count"],
                "absent_count": sum(1 for r in records if r["status"] == "absent"),
                "attendance_rate": stats["attendance_rate"],
            },
            "records": records,
        }
