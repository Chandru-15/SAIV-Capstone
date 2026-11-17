"""Course structure and tutorial / lab groups.

    course_settings   (course_id)            does the course have tutorials, labs?
    course_groups     (course_id, kind, name) e.g. SC3099 tutorial T01, lab L02
    group_members     (group_id, student_id)  one tutorial and one lab per student
                                              per course (uq on course+kind+student)
    session_groups    (session_id, group_id)  a tutorial / lab session belongs to
                                              one group; lectures have no row

New tables rather than new columns, so the schema.sql-owned tables are not
altered. DDL: db/migrations/002_course_groups.sql (also created at startup).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, NOW_UTC, pk_column, utcnow

if TYPE_CHECKING:
    from .attendance_session import AttendanceSession
    from .course import Course

GROUP_KINDS = ("tutorial", "lab")


class CourseSettings(Base):
    __tablename__ = "course_settings"

    course_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True
    )
    has_tutorials: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                                server_default="false")
    has_labs: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                           server_default="false")

    course: Mapped["Course"] = relationship(back_populates="settings")


class CourseGroup(Base):
    __tablename__ = "course_groups"

    id: Mapped[str] = pk_column()
    course_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    capacity: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=NOW_UTC
    )

    members: Mapped[List["GroupMember"]] = relationship(
        back_populates="group", cascade="all, delete-orphan", passive_deletes=True,
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("course_id", "kind", "name", name="uq_course_groups_name"),
        CheckConstraint("kind IN ('tutorial', 'lab')", name="ck_course_groups_kind"),
        CheckConstraint("capacity IS NULL OR capacity > 0", name="ck_course_groups_capacity"),
        Index("ix_course_groups_course_id", "course_id"),
    )


class GroupMember(Base):
    __tablename__ = "group_members"

    id: Mapped[str] = pk_column()
    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("course_groups.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised from the group so the database itself guarantees one
    # tutorial and one lab per student per course.
    course_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)

    group: Mapped["CourseGroup"] = relationship(back_populates="members")

    __table_args__ = (
        UniqueConstraint("course_id", "kind", "student_id", name="uq_group_members_one_per_kind"),
        Index("ix_group_members_group_id", "group_id"),
        Index("ix_group_members_student_id", "student_id"),
    )


class SessionGroup(Base):
    __tablename__ = "session_groups"

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True
    )
    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("course_groups.id", ondelete="CASCADE"), nullable=False
    )

    session: Mapped["AttendanceSession"] = relationship(back_populates="group_link")
    group: Mapped["CourseGroup"] = relationship(lazy="selectin")

    __table_args__ = (Index("ix_session_groups_group_id", "group_id"),)
