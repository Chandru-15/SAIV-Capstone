"""SQLAlchemy models mirroring db/schema.sql.

Import models from this package rather than the submodules, so that every
mapper is registered before SQLAlchemy resolves relationships:

    from app.models import Base, User, CheckIn, UserRole
"""

from .assignment import CourseInstructor, SessionTA
from .attendance_session import AttendanceSession
from .audit_log import AuditLog
from .browser_key_challenge import BrowserKeyChallenge
from .base import Base, enum_column, new_uuid, utcnow
from .checkin import CheckIn
from .checkin_attempt import CheckInAttempt
from .course import Course
from .device import Device
from .enrollment import Enrollment
from .face_reenroll import FaceReEnrollRequest
from .grouping import GROUP_KINDS, CourseGroup, CourseSettings, GroupMember, SessionGroup
from .password_reset import PasswordResetCode
from .enums import (
    AuditAction,
    CheckInStatus,
    DevicePlatform,
    FaceReEnrollStatus,
    LivenessChallengeType,
    RiskSeverity,
    RiskSignalType,
    SessionStatus,
    SessionType,
    TrustScore,
    UserRole,
)
from .risk_signal import RiskSignal
from .user import User

__all__ = [
    # base
    "Base",
    "enum_column",
    "new_uuid",
    "utcnow",
    # models
    "AttendanceSession",
    "CourseInstructor",
    "SessionTA",
    "AuditLog",
    "BrowserKeyChallenge",
    "CheckIn",
    "CheckInAttempt",
    "Course",
    "Device",
    "Enrollment",
    "FaceReEnrollRequest",
    "CourseGroup",
    "CourseSettings",
    "GroupMember",
    "SessionGroup",
    "PasswordResetCode",
    "GROUP_KINDS",
    "RiskSignal",
    "User",
    # enums
    "AuditAction",
    "CheckInStatus",
    "DevicePlatform",
    "FaceReEnrollStatus",
    "LivenessChallengeType",
    "RiskSeverity",
    "RiskSignalType",
    "SessionStatus",
    "SessionType",
    "TrustScore",
    "UserRole",
]
