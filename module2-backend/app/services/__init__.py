"""Service layer — business rules and the transaction boundary.

The dependency direction is strictly one-way:

    routers  ->  services  ->  repositories  ->  models  ->  database

Services never import FastAPI; routers never build a ``select()``. A service
method decorated with :func:`~app.services.base.transactional` commits when it
returns and rolls back if it raises, and services calling each other share one
transaction rather than committing piecemeal.

    from app.services import AuthService, CheckInService

    auth = AuthService(session)
    user = auth.authenticate(email, password)      # commits
    checkin = CheckInService(session).submit(user, session_id)   # commits
"""

from .audit_service import AuditService
from .auth_service import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ALGORITHM,
    MIN_PASSWORD_LENGTH,
    REFRESH_TOKEN_EXPIRE_DAYS,
    AuthService,
)
from .base import BaseService, transactional
from .checkin_service import RETENTION_DAYS, CheckInService
from .course_service import CourseService
from .device_service import DeviceService
from .enrollment_service import EnrollmentService
from .exceptions import (
    AuthenticationError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ServiceError,
    ServiceUnavailableError,
    ValidationError,
)
from .risk_service import CheckInContext, RiskAssessment, RiskService
from .session_service import SessionService
from .user_service import UserService

__all__ = [
    # infrastructure
    "BaseService",
    "transactional",
    # exceptions
    "ServiceError",
    "ValidationError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "ConflictError",
    "BusinessRuleError",
    "ServiceUnavailableError",
    # services
    "AuditService",
    "AuthService",
    "CheckInService",
    "CourseService",
    "DeviceService",
    "EnrollmentService",
    "RiskService",
    "SessionService",
    "UserService",
    # risk types
    "CheckInContext",
    "RiskAssessment",
    # constants
    "ALGORITHM",
    "ACCESS_TOKEN_EXPIRE_MINUTES",
    "REFRESH_TOKEN_EXPIRE_DAYS",
    "MIN_PASSWORD_LENGTH",
    "RETENTION_DAYS",
]
