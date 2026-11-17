"""Repository for :class:`~app.models.checkin.CheckIn`."""

from __future__ import annotations

from datetime import datetime
from typing import Collection, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..models import CheckIn, CheckInAttempt, CheckInStatus
from ..models.base import utcnow
from .base import DEFAULT_LIMIT, BaseRepository, Page

# Statuses an instructor is expected to act on. Matches the partial index
# ix_checkins_review_queue.
REVIEWABLE_STATUSES = (CheckInStatus.FLAGGED, CheckInStatus.APPEALED)


class CheckInRepository(BaseRepository[CheckIn]):
    model = CheckIn

    def find_by_session_and_student(
        self, session_id: str, student_id: str, *, lock: bool = False,
    ) -> Optional[CheckIn]:
        """Enforced as unique by ``uq_checkins_session_student``. Call this
        before inserting: one check-in per student per session."""
        stmt = select(CheckIn).where(
            CheckIn.session_id == session_id,
            CheckIn.student_id == student_id,
        )
        if lock:
            stmt = stmt.with_for_update(key_share=True).execution_options(populate_existing=True)
        return self.session.scalars(stmt).first()

    def lock_for_update(self, checkin_id: str) -> Optional[CheckIn]:
        return self.session.get(CheckIn, checkin_id, with_for_update={"key_share": True}, populate_existing=True)

    # -- attempt history ----------------------------------------------------
    # checkins holds one row per student per session, so a retry overwrites
    # it. These two methods are the append-only trail of what each individual
    # submission looked like before it was replaced.

    def record_attempt(self, attempt: CheckInAttempt) -> CheckInAttempt:
        """Append one submission's snapshot.

        Flushed rather than committed: the caller is inside ``@transactional``,
        so the attempt lands in the same transaction as the check-in it
        describes. An attempt row for a check-in that was rolled back would be
        a record of something that never happened.
        """
        self.session.add(attempt)
        self.session.flush()
        return attempt

    def find_attempts(self, checkin_id: str) -> List[CheckInAttempt]:
        """Every submission for this check-in, oldest first."""
        stmt = (
            select(CheckInAttempt)
            .where(CheckInAttempt.checkin_id == checkin_id)
            .order_by(CheckInAttempt.attempt_number)
        )
        return list(self.session.scalars(stmt))

    def find_last_positioned_attempt(
        self, student_id: str, before: datetime
    ) -> Optional[CheckInAttempt]:
        """This student's most recent ESTABLISHED position.

        The baseline for the impossible-travel speed test, which asks whether
        the student could have got from there to here in the elapsed time.

        Queried over ``checkin_attempts`` rather than ``checkins`` on purpose.
        Every submission is snapshotted there, including superseded retries, so
        one query covers both comparisons that matter: an earlier attempt at
        the session being submitted now (seconds ago) and a check-in at some
        other session (hours ago). The ``checkins`` row would only show the
        attempt that survived a retry.

        ONLY APPROVED ATTEMPTS COUNT, and this is the load-bearing filter.

        A rejected attempt's location claim is one the engine already refused
        to believe; using it to accuse the next claim would be reasoning from
        evidence we discarded. Concretely: a student whose first fix came back
        stale from the OS -- a cached position from home -- is rejected for
        being beyond the geofence, retries honestly from the lecture theatre
        seconds later, and that retry would read as a 40 km teleport. The
        module already settled this case in RiskService._retries: a stale GPS
        fix is the ordinary reason for a second go, so an attempt is judged on
        its own merits exactly as a first attempt would be.

        Flagged attempts are excluded for the same reason -- a flag means a
        human has yet to decide whether to believe it.

        WHAT THIS GIVES UP, stated plainly: a spoofer whose first attempt is
        rejected can move their fake position and retry without tripping the
        speed test. That pattern is indistinguishable from the honest stale-fix
        case above using position data alone, and the cost of guessing wrong is
        an attendance mark withheld from someone who attended. Both positions
        are still on the attempt trail for a reviewer, and ``rapid_succession``
        still records that it took more than one go.

        ``before`` is inclusive: two positions bearing the same instant are a
        contradiction worth seeing, not a tie to discard. The current attempt
        has no row yet when this runs, so it cannot match itself.

        Attempts with no latitude or longitude -- a student without location
        consent, or a fix that never arrived -- are skipped rather than treated
        as a position of (0, 0) in the Gulf of Guinea.
        """
        stmt = (
            select(CheckInAttempt)
            .join(CheckIn, CheckIn.id == CheckInAttempt.checkin_id)
            .where(
                CheckIn.student_id == student_id,
                CheckInAttempt.status == CheckInStatus.APPROVED,
                CheckInAttempt.attempted_at <= before,
                CheckInAttempt.latitude.is_not(None),
                CheckInAttempt.longitude.is_not(None),
            )
            .order_by(CheckInAttempt.attempted_at.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def has_checked_in(self, session_id: str, student_id: str) -> bool:
        stmt = (
            select(func.count())
            .select_from(CheckIn)
            .where(CheckIn.session_id == session_id, CheckIn.student_id == student_id)
        )
        return bool(self.session.scalar(stmt))

    def find_with_signals(self, checkin_id: str) -> Optional[CheckIn]:
        """``GET /checkins/{id}`` returns the risk signals inline; eager-load
        them so the response does not fire a second query per row."""
        stmt = (
            select(CheckIn)
            .where(CheckIn.id == checkin_id)
            .options(selectinload(CheckIn.risk_signals), selectinload(CheckIn.device))
        )
        return self.session.scalars(stmt).first()

    def search(
        self,
        session_id: Optional[str] = None,
        student_id: Optional[str] = None,
        course_id: Optional[str] = None,
        status: Optional[CheckInStatus] = None,
        min_risk_score: Optional[float] = None,
        checked_in_after: Optional[datetime] = None,
        checked_in_before: Optional[datetime] = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        session_ids: Optional[Collection[str]] = None,
    ) -> Page[CheckIn]:
        """Backs ``GET /checkins`` with its documented filters.

        ``session_ids`` restricts the result to a caller's own sessions (None
        means no restriction); see services/staff_scope.py.
        """
        stmt = select(CheckIn)
        if session_ids is not None:
            stmt = stmt.where(CheckIn.session_id.in_(list(session_ids)))
        if session_id is not None:
            stmt = stmt.where(CheckIn.session_id == session_id)
        if student_id is not None:
            stmt = stmt.where(CheckIn.student_id == student_id)
        if course_id is not None:
            from ..models import AttendanceSession

            stmt = stmt.join(
                AttendanceSession, AttendanceSession.id == CheckIn.session_id
            ).where(AttendanceSession.course_id == course_id)
        if status is not None:
            stmt = stmt.where(CheckIn.status == status)
        if min_risk_score is not None:
            stmt = stmt.where(CheckIn.risk_score >= min_risk_score)
        if checked_in_after is not None:
            stmt = stmt.where(CheckIn.checked_in_at >= checked_in_after)
        if checked_in_before is not None:
            stmt = stmt.where(CheckIn.checked_in_at <= checked_in_before)
        return self.paginate(stmt.order_by(CheckIn.checked_in_at.desc()), limit, offset)

    def find_review_queue(
        self, course_id: Optional[str] = None,
        limit: int = DEFAULT_LIMIT, offset: int = 0,
        session_ids: Optional[Collection[str]] = None,
    ) -> Page[CheckIn]:
        """Flagged and appealed check-ins, newest first.

        NOTE: at 20k rows the planner serves this from ``ix_checkins_status``
        plus a top-N sort, not from ``ix_checkins_review_queue`` — that index is
        keyed ``(status, checked_in_at DESC)``, so scanning two status values
        returns rows grouped by status rather than globally ordered, and the
        sort survives. Re-keying it to ``(checked_in_at DESC) WHERE status IN
        (...)`` removes the sort entirely. See the note in db/schema.sql.
        """
        stmt = select(CheckIn).where(CheckIn.status.in_(REVIEWABLE_STATUSES))
        if session_ids is not None:
            stmt = stmt.where(CheckIn.session_id.in_(list(session_ids)))
        if course_id is not None:
            from ..models import AttendanceSession

            stmt = stmt.join(
                AttendanceSession, AttendanceSession.id == CheckIn.session_id
            ).where(AttendanceSession.course_id == course_id)
        return self.paginate(stmt.order_by(CheckIn.checked_in_at.desc()), limit, offset)

    def find_by_student(
        self, student_id: str, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> Page[CheckIn]:
        """Backs ``GET /checkins/me``."""
        stmt = select(CheckIn).where(CheckIn.student_id == student_id)
        return self.paginate(stmt.order_by(CheckIn.checked_in_at.desc()), limit, offset)

    def find_by_session(self, session_id: str) -> List[CheckIn]:
        stmt = (
            select(CheckIn)
            .where(CheckIn.session_id == session_id)
            .order_by(CheckIn.checked_in_at)
        )
        return list(self.session.scalars(stmt))

    def count_by_status(self, session_id: str) -> Dict[str, int]:
        """The ``by_status`` block in the session statistics response."""
        stmt = (
            select(CheckIn.status, func.count())
            .where(CheckIn.session_id == session_id)
            .group_by(CheckIn.status)
        )
        return {
            (status.value if hasattr(status, "value") else str(status)): count
            for status, count in self.session.execute(stmt)
        }

    # -- domain operations --------------------------------------------------

    def approve(self, checkin: CheckIn, reviewer_id: Optional[str] = None,
                notes: Optional[str] = None) -> CheckIn:
        return self._review(checkin, CheckInStatus.APPROVED, reviewer_id, notes)

    def reject(self, checkin: CheckIn, reviewer_id: Optional[str] = None,
               notes: Optional[str] = None) -> CheckIn:
        return self._review(checkin, CheckInStatus.REJECTED, reviewer_id, notes)

    def appeal(self, checkin: CheckIn, reason: str) -> CheckIn:
        checkin.status = CheckInStatus.APPEALED
        checkin.appeal_reason = reason
        checkin.appealed_at = utcnow()
        self.session.flush()
        return checkin

    def _review(
        self, checkin: CheckIn, status: CheckInStatus,
        reviewer_id: Optional[str], notes: Optional[str],
    ) -> CheckIn:
        now = utcnow()
        checkin.status = status
        checkin.reviewed_by_id = reviewer_id
        checkin.reviewed_at = now
        checkin.review_notes = notes
        if status == CheckInStatus.APPROVED:
            checkin.verified_at = now
        self.session.flush()
        return checkin

    def find_due_for_deletion(self, now: Optional[datetime] = None) -> List[CheckIn]:
        """Retention sweep; uses ``ix_checkins_scheduled_deletion_at``."""
        cutoff = now or utcnow()
        stmt = select(CheckIn).where(
            CheckIn.scheduled_deletion_at.is_not(None),
            CheckIn.scheduled_deletion_at <= cutoff,
        )
        return list(self.session.scalars(stmt))
