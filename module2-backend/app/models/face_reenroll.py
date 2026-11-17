"""Staff-approved face re-enrolment requests.

    face_reenroll_requests  (user_id, reason, status, reviewed_by_id, used_at)

Replacing an enrolled template used to need only the student's own password,
which the student always knows. That proved the account holder was present; it
never proved anyone else agreed. A student now asks, and an instructor or admin
decides. The approval is spent the moment a replacement succeeds, so one
decision buys exactly one new template.

No photo is stored here. A request is a permission, not a submission: the
student captures the new face after approval and the image goes straight to
module 3 as it always has. A pending row holding a base64 photo would be
biometric data at rest that ``unenroll`` could never erase.

main.py creates this table at startup if it is missing, like the password
reset and group tables, so an existing database volume needs no migration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, NOW_UTC, enum_column, pk_column, utcnow
from .enums import FaceReEnrollStatus


class FaceReEnrollRequest(Base):
    __tablename__ = "face_reenroll_requests"

    id: Mapped[str] = pk_column()
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # The student's own words, shown to whoever reviews it. Never acted on.
    reason: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[FaceReEnrollStatus] = mapped_column(
        enum_column(FaceReEnrollStatus, 16), nullable=False,
        default=FaceReEnrollStatus.PENDING,
        server_default=FaceReEnrollStatus.PENDING.value,
    )
    # SET NULL rather than CASCADE: who decided belongs to the record, but
    # removing a staff account must not erase the student's history with it.
    reviewed_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    review_notes: Mapped[Optional[str]] = mapped_column(Text)
    # Stamped when an approval is spent. An approved row with used_at still
    # NULL is the only thing that lets PUT /face through, so this column is
    # what makes an approval single-use. There is deliberately no expiry.
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=NOW_UTC
    )

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    reviewer: Mapped[Optional["User"]] = relationship(foreign_keys=[reviewed_by_id])

    __table_args__ = (
        # enum_column leaves the CHECK to schema.sql, which does not own this
        # table -- create_all is the only thing that builds it, so the
        # constraint has to be declared here or it would never exist.
        CheckConstraint(
            "status IN ('pending', 'approved', 'denied')",
            name="ck_face_reenroll_requests_status",
        ),
        Index("ix_face_reenroll_requests_user_id", "user_id"),
        Index("ix_face_reenroll_requests_status", "status"),
    )

    @property
    def is_spendable(self) -> bool:
        """Approved and not yet spent: the one state PUT /face accepts."""
        return self.status == FaceReEnrollStatus.APPROVED and self.used_at is None
