"""Repository layer — the data-access seam, modelled on Spring Data JPA.

Routers and services depend on these classes, never on ``select()`` or the
ORM session directly. Every repository takes a SQLAlchemy ``Session`` in its
constructor, the way a Spring repository is injected with an ``EntityManager``:

    from sqlalchemy.orm import Session
    from app.repositories import UserRepository

    users = UserRepository(session)
    user = users.find_by_email("student@example.com")

None of these methods commit. Owning the transaction is the caller's job.
"""

from .audit_log_repository import AuditLogIsAppendOnlyError, AuditLogRepository
from .base import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    BaseRepository,
    EntityNotFoundError,
    Page,
    clamp_limit,
)
from .checkin_repository import REVIEWABLE_STATUSES, CheckInRepository
from .course_repository import CourseRepository
from .device_repository import DeviceRepository
from .enrollment_repository import EnrollmentRepository
from .face_reenroll_repository import FaceReEnrollRepository
from .risk_signal_repository import RiskSignalRepository
from .session_repository import AttendanceSessionRepository
from .user_repository import UserRepository

__all__ = [
    # infrastructure
    "BaseRepository",
    "Page",
    "EntityNotFoundError",
    "AuditLogIsAppendOnlyError",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "clamp_limit",
    "REVIEWABLE_STATUSES",
    # repositories
    "AttendanceSessionRepository",
    "AuditLogRepository",
    "CheckInRepository",
    "CourseRepository",
    "DeviceRepository",
    "EnrollmentRepository",
    "FaceReEnrollRepository",
    "RiskSignalRepository",
    "UserRepository",
]
