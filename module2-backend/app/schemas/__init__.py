"""Pydantic DTOs — the request/response contract.

The equivalent of Spring's DTO classes. Two rules hold throughout:

* ORM entities are never returned directly. ``UserResponse`` has no
  ``hashed_password``; ``CheckInResponse`` has no ``face_embedding_hash``.
  Adding a column to a model must not silently widen the API.
* Field constraints here are a fast first line, not the authority. Business
  rules live in the services, which are also reachable from tests and jobs.
"""

from .audit import AuditLogCreateRequest, AuditLogResponse
from .checkin import (
    CheckInAppealRequest,
    CheckInCreateRequest,
    CheckInDetailResponse,
    CheckInResponse,
    CheckInReviewRequest,
    RiskSignalResponse,
)
from .face import (
    FaceEnrollRequest,
    FaceReEnrollRequestCreate,
    FaceReEnrollRequestResponse,
    FaceReEnrollReviewRequest,
    FaceReEnrollSubmitRequest,
    LivenessChallengeResponse,
    LivenessFramesRequest,
    LivenessFramesResponse,
    FaceEnrollResponse,
    FaceReEnrollResponse,
    FaceServiceHealthResponse,
    FaceVerifyRequest,
    FaceVerifyResponse,
    LivenessCheckRequest,
    LivenessCheckResponse,
)
from .common import ErrorResponse, MessageResponse, PageResponse
from .course import (
    BulkEnrollRequest,
    DeviceRegisterRequest,
    DeviceResponse,
    BulkEnrollResponse,
    BulkEnrollResult,
    CourseCreateRequest,
    CourseResponse,
    CourseStructureRequest,
    GroupAllocationRequest,
    GroupMoveRequest,
    CourseUpdateRequest,
    PublicCourseResponse,
    EnrollmentCreateRequest,
    EnrollmentResponse,
)
from .session import (
    QRCodeResponse,
    SessionCreateRequest,
    SessionResponse,
    SessionStatisticsResponse,
    SessionStatusUpdateRequest,
    SessionUpdateRequest,
)
from .user import (
    BulkCreateUsersRequest,
    BulkCreateUsersResponse,
    BulkUserSpec,
    ConsentUpdateRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserProfileUpdateRequest,
    UserResponse,
)

__all__ = [
    "AuditLogCreateRequest", "AuditLogResponse",
    "CheckInAppealRequest", "CheckInCreateRequest", "CheckInDetailResponse",
    "CheckInResponse", "CheckInReviewRequest", "RiskSignalResponse",
    "ErrorResponse", "MessageResponse", "PageResponse",
    "FaceEnrollRequest", "FaceReEnrollSubmitRequest", "FaceEnrollResponse",
    "FaceReEnrollRequestCreate", "FaceReEnrollRequestResponse",
    "FaceReEnrollReviewRequest",
    "FaceReEnrollResponse",
    "FaceServiceHealthResponse",
    "FaceVerifyRequest", "FaceVerifyResponse",
    "LivenessCheckRequest", "LivenessCheckResponse",
    "LivenessChallengeResponse", "LivenessFramesRequest", "LivenessFramesResponse",
    "BulkEnrollRequest", "BulkEnrollResponse", "BulkEnrollResult",
    "CourseCreateRequest", "CourseResponse", "CourseUpdateRequest",
    "CourseStructureRequest", "GroupAllocationRequest", "GroupMoveRequest",
    "PublicCourseResponse",
    "EnrollmentCreateRequest", "EnrollmentResponse",
    "QRCodeResponse", "SessionCreateRequest", "SessionResponse",
    "SessionStatisticsResponse", "SessionStatusUpdateRequest", "SessionUpdateRequest",
    "BulkCreateUsersRequest", "BulkCreateUsersResponse", "BulkUserSpec",
    "ConsentUpdateRequest", "ForgotPasswordRequest", "LoginRequest", "RefreshRequest", "RefreshResponse",
    "RegisterRequest", "ResetPasswordRequest", "TokenResponse", "UserResponse", "UserProfileUpdateRequest",
    "DeviceRegisterRequest", "DeviceResponse",
]