"""User accounts: students, instructors, TAs and admins."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at_column, enum_column, pk_column, updated_at_column
from .enums import UserRole

if TYPE_CHECKING:
    from .attendance_session import AttendanceSession
    from .audit_log import AuditLog
    from .checkin import CheckIn
    from .device import Device
    from .enrollment import Enrollment


class User(Base):
    """A person with an account.

    PRIVACY: ``face_embedding_hash`` is a SHA-256 hex digest of the enrolled
    embedding. Never store raw images or embeddings on this model.
    """

    __tablename__ = "users"

    id: Mapped[str] = pk_column()
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(enum_column(UserRole, 20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                            server_default="true")

    # Consent must be granted before camera / location data may be used.
    camera_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                                 server_default="false")
    geolocation_consent: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                                      default=False,
                                                      server_default="false")

    # Bumped by AuthService.logout. A token is accepted only while its `ver`
    # claim still matches, so one logout invalidates every token this user
    # holds, on every device, without any server-side session store.
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    browser_verification_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    face_embedding_hash: Mapped[Optional[str]] = mapped_column(String(64))
    face_enrolled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                                server_default="false")

    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    scheduled_deletion_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # -- relationships ------------------------------------------------------
    enrollments: Mapped[List["Enrollment"]] = relationship(
        back_populates="student", cascade="all, delete-orphan", passive_deletes=True
    )
    devices: Mapped[List["Device"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    checkins: Mapped[List["CheckIn"]] = relationship(
        back_populates="student",
        foreign_keys="CheckIn.student_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    reviewed_checkins: Mapped[List["CheckIn"]] = relationship(
        back_populates="reviewer", foreign_keys="CheckIn.reviewed_by_id"
    )
    taught_sessions: Mapped[List["AttendanceSession"]] = relationship(
        back_populates="instructor", foreign_keys="AttendanceSession.instructor_id"
    )
    audit_logs: Mapped[List["AuditLog"]] = relationship(back_populates="user")

    __table_args__ = (
        CheckConstraint(
            "role IN ('student', 'instructor', 'ta', 'admin')",
            name="ck_users_role",
        ),
        CheckConstraint(
            "face_embedding_hash IS NULL OR char_length(face_embedding_hash) = 64",
            name="ck_users_face_hash_len",
        ),
        Index("ix_users_email", "email"),
        Index("ix_users_role", "role"),
        Index("ix_users_is_active", "is_active"),
        Index(
            "ix_users_scheduled_deletion_at",
            "scheduled_deletion_at",
            postgresql_where=text("scheduled_deletion_at IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.email} ({self.role})>"
