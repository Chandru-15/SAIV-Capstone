"""Admin endpoints — ``/admin``.

These exist because the automated test suite drives them; they are the
privileged counterparts of endpoints that are otherwise self-service.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from ..models import AttendanceSession, Enrollment, User
from ..schemas import (
    BulkCreateUsersRequest,
    BulkCreateUsersResponse,
    BulkEnrollRequest,
    BulkEnrollResponse,
    EnrollmentCreateRequest,
    EnrollmentResponse,
    SessionResponse,
    SessionStatusUpdateRequest,
    UserResponse,
)
from ..services import EnrollmentService, SessionService, UserService

router = APIRouter(prefix="/admin", tags=["Admin"])

# The spec and the public tests also address enrolment at /enrollments/.
# Same handlers, second address — no duplicated logic.
enrollment_router = APIRouter(prefix="/enrollments", tags=["Enrollments"])


@router.patch("/users/{user_id}/deactivate", response_model=UserResponse)
def deactivate_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> User:
    return UserService(db).set_active(current_user, user_id, False)


@router.patch("/users/{user_id}/activate", response_model=UserResponse)
def activate_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> User:
    return UserService(db).set_active(current_user, user_id, True)


@router.post("/users/bulk", response_model=BulkCreateUsersResponse,
             status_code=status.HTTP_201_CREATED)
def bulk_create_users(
    payload: BulkCreateUsersRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Addresses that already exist are skipped, not rejected, so one
    duplicate does not discard the rest of the batch."""
    created = UserService(db).bulk_create(
        current_user,
        [spec.model_dump() for spec in payload.users],
        payload.default_password,
    )
    return {
        "requested": len(payload.users),
        "created": len(created),
        "users": [UserResponse.model_validate(u) for u in created],
    }


@router.get("/password-reset-requests")
def password_reset_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list:
    """Pending "forgot password" requests, newest first (admin only)."""
    return UserService(db).pending_reset_requests(current_user)


@router.get("/email-status")
def email_status(current_user: User = Depends(get_current_user)) -> dict:
    """Whether outgoing email (SMTP) is configured, so the dashboard can offer
    "email a reset code" or fall back to a temporary password. Admin only."""
    from ..models import UserRole
    from ..services.email_service import email_configured

    UserService.require_role(current_user, UserRole.ADMIN)
    return {"enabled": email_configured()}


@router.post("/users/{user_id}/send-reset-email")
def send_reset_email(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Email the account a one-time code so the user sets their own new
    password (preferred over a temporary password). Admin only."""
    return UserService(db).send_reset_email(current_user, user_id)


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Fallback reset: a one-off temporary password, shown once to the admin
    and handed to the user in person. Use it when email is not set up or the
    user cannot reach their inbox; otherwise prefer send-reset-email.
    """
    temporary = UserService(db).reset_password(current_user, user_id)
    return {"user_id": user_id, "temporary_password": temporary}


@router.patch("/sessions/{session_id}/status", response_model=SessionResponse)
def update_session_status(
    session_id: str,
    payload: SessionStatusUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AttendanceSession:
    return SessionService(db).change_status(current_user, session_id, payload.status)


@router.post("/enrollments/", response_model=EnrollmentResponse,
             status_code=status.HTTP_201_CREATED)
def create_enrollment(
    payload: EnrollmentCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Enrollment:
    return EnrollmentService(db).enroll(current_user, payload.student_id, payload.course_id)


@router.post("/enrollments/bulk", response_model=BulkEnrollResponse)
def bulk_enroll(
    payload: BulkEnrollRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    results = EnrollmentService(db).enroll_by_emails(
        current_user, payload.course_id, payload.emails
    )
    return {"results": results}


# --- /enrollments/ (alias of /admin/enrollments/) ----------------------------


@enrollment_router.post("/", response_model=EnrollmentResponse,
                        status_code=status.HTTP_201_CREATED)
def create_enrollment_alias(
    payload: EnrollmentCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Enrollment:
    return EnrollmentService(db).enroll(current_user, payload.student_id, payload.course_id)


@enrollment_router.post("/bulk", response_model=BulkEnrollResponse)
def bulk_enroll_alias(
    payload: BulkEnrollRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    return {"results": EnrollmentService(db).enroll_by_emails(
        current_user, payload.course_id, payload.emails)}


@enrollment_router.get("/my-enrollments")
def my_enrollments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list:
    """The caller's own enrolments, joined to course details.

    A bare list of flattened rows rather than EnrollmentResponse objects: a
    client showing "my courses" needs the code and name, and would otherwise
    have to fetch each course separately.
    """
    from ..services.enrollment_service import EnrollmentService

    return EnrollmentService(db).my_enrollments(current_user)


@enrollment_router.get("/course/{course_id}")
def course_enrollments(
    course_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """The roster for one course. Staff only — it names every enrolled student.

    This is the scoped alternative to GET /users/, which is admin-only: an
    instructor needing their class list uses this rather than being granted
    sight of every account in the institution.
    """
    from ..models import UserRole
    from ..services.enrollment_service import EnrollmentService
    from ..services.user_service import UserService

    from ..services.staff_scope import require_course_visible

    UserService.require_role(current_user, UserRole.ADMIN, UserRole.INSTRUCTOR, UserRole.TA)
    require_course_visible(db, current_user, course_id)
    return EnrollmentService(db).course_roster(course_id)