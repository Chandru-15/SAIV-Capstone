"""Domain exceptions raised by the service layer.

Services never import FastAPI or raise ``HTTPException`` — that would weld the
business rules to the transport. They raise these instead, and a single
exception handler in the router layer maps ``status_code`` onto the response.
This is the same separation as a Spring ``@Service`` throwing a domain
exception that ``@ControllerAdvice`` translates.
"""

from __future__ import annotations

from typing import Any, Optional


class ServiceError(Exception):
    """Base class for every expected business failure."""

    status_code: int = 400
    code: str = "service_error"

    def __init__(self, message: str, *, details: Optional[Any] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class ValidationError(ServiceError):
    """Input is well-formed but violates a business rule (HTTP 422)."""

    status_code = 422
    code = "validation_error"


class AuthenticationError(ServiceError):
    """Bad credentials, or an invalid / expired token (HTTP 401)."""

    status_code = 401
    code = "authentication_error"


class PermissionDeniedError(ServiceError):
    """Authenticated, but not allowed to do this (HTTP 403)."""

    status_code = 403
    code = "permission_denied"


class NotFoundError(ServiceError):
    """No such entity (HTTP 404)."""

    status_code = 404
    code = "not_found"


class ConflictError(ServiceError):
    """Collides with existing state — duplicate email, double check-in (409)."""

    status_code = 409
    code = "conflict"


class BusinessRuleError(ServiceError):
    """The request is refused by a domain rule (HTTP 400).

    Used for the errors the API spec documents as 400: session not active,
    check-in window closed, illegal status transition.
    """

    status_code = 400
    code = "business_rule_violation"


class RateLimitedError(ServiceError):
    """Attempted again too soon (HTTP 429).

    ``retry_after`` is whole seconds, for the Retry-After header: a client
    should be told how long to wait rather than left to poll.
    """

    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: int,
                 details: Optional[Any] = None) -> None:
        super().__init__(message, details=details)
        self.retry_after = max(1, int(retry_after))


class ServiceUnavailableError(ServiceError):
    """A dependency this request needed is down (HTTP 503).

    Distinct from a 500: the backend is healthy, something it calls is not, and
    the client may reasonably retry. The API spec requires 503 when the face
    recognition service is unavailable.
    """

    status_code = 503
    code = "service_unavailable"
