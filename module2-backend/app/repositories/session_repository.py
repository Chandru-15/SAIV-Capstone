"""Repository for :class:`~app.models.attendance_session.AttendanceSession`."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import select

from ..models import AttendanceSession, SessionStatus
from ..models.base import utcnow
from .base import DEFAULT_LIMIT, BaseRepository, Page


class AttendanceSessionRepository(BaseRepository[AttendanceSession]):
    model = AttendanceSession

    def search(
        self,
        course_id: Optional[str] = None,
        status: Optional[SessionStatus] = None,
        instructor_id: Optional[str] = None,
        starts_after: Optional[datetime] = None,
        starts_before: Optional[datetime] = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Page[AttendanceSession]:
        """Backs ``GET /sessions``."""
        stmt = select(AttendanceSession)
        if course_id is not None:
            stmt = stmt.where(AttendanceSession.course_id == course_id)
        if status is not None:
            stmt = stmt.where(AttendanceSession.status == status)
        if instructor_id is not None:
            stmt = stmt.where(AttendanceSession.instructor_id == instructor_id)
        if starts_after is not None:
            stmt = stmt.where(AttendanceSession.scheduled_start >= starts_after)
        if starts_before is not None:
            stmt = stmt.where(AttendanceSession.scheduled_start <= starts_before)
        return self.paginate(
            stmt.order_by(AttendanceSession.scheduled_start.desc()), limit, offset
        )

    def find_by_course(self, course_id: str) -> List[AttendanceSession]:
        stmt = (
            select(AttendanceSession)
            .where(AttendanceSession.course_id == course_id)
            .order_by(AttendanceSession.scheduled_start)
        )
        return list(self.session.scalars(stmt))

    def find_open_for_checkin(self, now: Optional[datetime] = None) -> List[AttendanceSession]:
        """Sessions currently accepting check-ins.

        Uses ``ix_sessions_checkin_window``. A session is open only when it is
        ACTIVE *and* inside its window — status alone is not enough, since an
        instructor may leave a session active after the window closes.
        """
        moment = now or utcnow()
        stmt = select(AttendanceSession).where(
            AttendanceSession.status == SessionStatus.ACTIVE,
            AttendanceSession.checkin_opens_at <= moment,
            AttendanceSession.checkin_closes_at >= moment,
        )
        return list(self.session.scalars(stmt))

    def is_open_for_checkin(
        self, session: AttendanceSession, now: Optional[datetime] = None
    ) -> bool:
        moment = now or utcnow()
        return (
            session.status == SessionStatus.ACTIVE
            and session.checkin_opens_at <= moment <= session.checkin_closes_at
        )

    def find_by_status(self, status: SessionStatus) -> List[AttendanceSession]:
        stmt = (
            select(AttendanceSession)
            .where(AttendanceSession.status == status)
            .order_by(AttendanceSession.scheduled_start)
        )
        return list(self.session.scalars(stmt))

    def set_status(
        self, session: AttendanceSession, status: SessionStatus
    ) -> AttendanceSession:
        """Backs ``PATCH /admin/sessions/{id}/status``, stamping the actual
        start/end times as the session transitions."""
        now = utcnow()
        if status == SessionStatus.ACTIVE and session.actual_start is None:
            session.actual_start = now
        if status in (SessionStatus.CLOSED, SessionStatus.CANCELLED) and session.actual_end is None:
            session.actual_end = now
        session.status = status
        self.session.flush()
        return session
