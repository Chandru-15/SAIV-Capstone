"""Enumerated values.

Every value here must appear in the matching CHECK constraint in
``db/schema.sql``. All members subclass ``str`` so they serialise straight to
JSON and compare equal to plain strings.
"""

from __future__ import annotations

import enum


class UserRole(str, enum.Enum):
    STUDENT = "student"
    INSTRUCTOR = "instructor"
    TA = "ta"
    ADMIN = "admin"


class SessionType(str, enum.Enum):
    LECTURE = "lecture"
    TUTORIAL = "tutorial"
    LAB = "lab"
    EXAM = "exam"


class SessionStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class CheckInStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    FLAGGED = "flagged"
    REJECTED = "rejected"
    APPEALED = "appealed"


class FaceReEnrollStatus(str, enum.Enum):
    """Where a face re-enrolment request stands.

    There is no "used" member: consumption is recorded by
    ``FaceReEnrollRequest.used_at``, so an approval that has been spent is
    still visibly an approval rather than becoming a fourth state.
    """

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class DevicePlatform(str, enum.Enum):
    IOS = "ios"
    ANDROID = "android"
    WEB = "web"
    DESKTOP = "desktop"


class TrustScore(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LivenessChallengeType(str, enum.Enum):
    BLINK = "blink"
    HEAD_TURN = "head_turn"
    SPEAK = "speak"
    PASSIVE = "passive"
    # Active illumination: the client screen is driven through a sequence of
    # saturated colours and the face must be seen to be lit by them. Unlike
    # the others this asks nothing of the subject, so it is the only active
    # type a student can satisfy while sitting still -- and the only one a
    # pre-recorded video cannot satisfy at all.
    COLOUR_REFLECTION = "colour_reflection"


class RiskSeverity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskSignalType(str, enum.Enum):
    # Geolocation
    GEO_OUT_OF_BOUNDS = "geo_out_of_bounds"
    IMPOSSIBLE_TRAVEL = "impossible_travel"
    GEO_ACCURACY_LOW = "geo_accuracy_low"
    GEO_IP_MISMATCH = "geo_ip_mismatch"
    # Network
    VPN_DETECTED = "vpn_detected"
    PROXY_DETECTED = "proxy_detected"
    TOR_DETECTED = "tor_detected"
    SUSPICIOUS_IP = "suspicious_ip"
    # Device
    DEVICE_UNKNOWN = "device_unknown"
    DEVICE_EMULATOR = "device_emulator"
    DEVICE_ROOTED = "device_rooted"
    ATTESTATION_FAILED = "attestation_failed"
    # Behavioural
    RAPID_SUCCESSION = "rapid_succession"
    UNUSUAL_TIME = "unusual_time"
    PATTERN_ANOMALY = "pattern_anomaly"
    # Liveness
    LIVENESS_FAILED = "liveness_failed"
    LIVENESS_LOW_CONFIDENCE = "liveness_low_confidence"
    DEEPFAKE_SUSPECTED = "deepfake_suspected"
    REPLAY_SUSPECTED = "replay_suspected"
    # Face match
    FACE_MATCH_FAILED = "face_match_failed"
    FACE_MATCH_LOW_CONFIDENCE = "face_match_low_confidence"


class AuditAction(str, enum.Enum):
    """Known audit actions.

    Unlike the others this is NOT enforced by a CHECK constraint, and
    ``AuditLog.action`` is a plain String column — the API spec ends its list
    with "etc.", so new actions must be loggable without a migration. Use these
    members for the known cases; any other string is accepted.
    """

    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    CHECKIN_ATTEMPTED = "checkin_attempted"
    CHECKIN_APPROVED = "checkin_approved"
    CHECKIN_FLAGGED = "checkin_flagged"
    CHECKIN_REJECTED = "checkin_rejected"
    CHECKIN_APPEALED = "checkin_appealed"
    CHECKIN_REVIEWED = "checkin_reviewed"
    SESSION_CREATED = "session_created"
    SESSION_UPDATED = "session_updated"
    SESSION_DELETED = "session_deleted"
    ENROLLMENT_ADDED = "enrollment_added"
    ENROLLMENT_REMOVED = "enrollment_removed"
    DEVICE_REGISTERED = "device_registered"
    FACE_ENROLLED = "face_enrolled"
    FACE_RE_ENROLLED = "face_re_enrolled"
    # Replacing a template is no longer something the account holder can do
    # alone: the student asks, an instructor or admin decides. All three are
    # recorded, so a template replaced just before a check-in can be traced
    # back to who allowed it.
    FACE_RE_ENROLL_REQUESTED = "face_re_enroll_requested"
    FACE_RE_ENROLL_APPROVED = "face_re_enroll_approved"
    FACE_RE_ENROLL_DENIED = "face_re_enroll_denied"
    DATA_EXPORTED = "data_exported"
    SECURITY_VIOLATION = "security_violation"
    INSTRUCTOR_ASSIGNED = "instructor_assigned"
    INSTRUCTOR_UNASSIGNED = "instructor_unassigned"
    TA_ASSIGNED = "ta_assigned"
    TA_UNASSIGNED = "ta_unassigned"
    # "Forgot password": a user asks, an admin issues a temporary password.
    PASSWORD_RESET_REQUESTED = "password_reset_requested"
    PASSWORD_RESET = "password_reset"
    PASSWORD_RESET_EMAILED = "password_reset_emailed"
    # Account lifecycle, logged separately from generic profile edits so the
    # audit trail says plainly what happened to which account.
    USER_ACTIVATED = "user_activated"
    USER_DEACTIVATED = "user_deactivated"
    USER_DELETED = "user_deleted"
    USER_RESTORED = "user_restored"