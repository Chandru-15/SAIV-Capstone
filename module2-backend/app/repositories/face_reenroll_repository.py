"""Repository for :class:`~app.models.face_reenroll.FaceReEnrollRequest`."""

from __future__ import annotations

from typing import Optional, Sequence, Set

from sqlalchemy import select

from ..models import FaceReEnrollRequest, FaceReEnrollStatus
from .base import DEFAULT_LIMIT, BaseRepository, Page


class FaceReEnrollRepository(BaseRepository[FaceReEnrollRequest]):
    model = FaceReEnrollRequest

    def find_pending_for_user(self, user_id: str) -> Optional[FaceReEnrollRequest]:
        """The student's open request, if they have one.

        Used to refuse a second request rather than let a student queue up
        several and have a reviewer decide the same thing twice.
        """
        stmt = (
            select(FaceReEnrollRequest)
            .where(FaceReEnrollRequest.user_id == user_id,
                   FaceReEnrollRequest.status == FaceReEnrollStatus.PENDING)
            .order_by(FaceReEnrollRequest.created_at.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def find_spendable_for_user(self, user_id: str) -> Optional[FaceReEnrollRequest]:
        """An approval this student has not spent yet.

        The ``used_at IS NULL`` half is what makes an approval single-use, and
        it is checked here rather than in Python so two concurrent re-enrolments
        cannot both read the same unspent row as available.
        """
        stmt = (
            select(FaceReEnrollRequest)
            .where(FaceReEnrollRequest.user_id == user_id,
                   FaceReEnrollRequest.status == FaceReEnrollStatus.APPROVED,
                   FaceReEnrollRequest.used_at.is_(None))
            .order_by(FaceReEnrollRequest.reviewed_at.desc())
            .limit(1)
            .with_for_update()
        )
        return self.session.scalars(stmt).first()

    def find_for_user(self, user_id: str, limit: int = DEFAULT_LIMIT,
                      offset: int = 0) -> Page[FaceReEnrollRequest]:
        stmt = (
            select(FaceReEnrollRequest)
            .where(FaceReEnrollRequest.user_id == user_id)
            .order_by(FaceReEnrollRequest.created_at.desc())
        )
        return self.paginate(stmt, limit, offset)

    def find_queue(
        self, *, student_ids: Optional[Set[str]] = None,
        status: Optional[FaceReEnrollStatus] = None,
        limit: int = DEFAULT_LIMIT, offset: int = 0,
    ) -> Page[FaceReEnrollRequest]:
        """Requests a staff member may act on.

        ``student_ids`` None means "every student" (admin). An EMPTY set means
        this reviewer is responsible for nobody, which must return nothing --
        not everything -- so it is matched explicitly rather than skipped as a
        falsy filter.
        """
        stmt = select(FaceReEnrollRequest)
        if student_ids is not None:
            if not student_ids:
                return Page(items=[], total=0, limit=limit, offset=offset)
            stmt = stmt.where(FaceReEnrollRequest.user_id.in_(student_ids))
        if status is not None:
            stmt = stmt.where(FaceReEnrollRequest.status == status)
        # Oldest first: a review queue is worked front to back, unlike the
        # newest-first lists a student sees of their own history.
        return self.paginate(stmt.order_by(FaceReEnrollRequest.created_at.asc()),
                             limit, offset)
