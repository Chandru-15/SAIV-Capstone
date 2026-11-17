"""Authentication endpoints — ``/auth``.

A controller does four things and nothing else: bind the HTTP request to a
DTO, resolve the caller, delegate to one service call, and shape the response.
There is no business logic, no ``select()``, and no ``commit()`` in this file.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import (
    client_ip,
    get_current_user,
    rate_limit_by_ip,
    user_agent,
)
from ..models import User
from ..schemas import (
    ConsentUpdateRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserResponse,
)
from ..services import AuthService
from ..services.rate_limit import LOGIN, REGISTRATION

router = APIRouter(prefix="/auth", tags=["Authentication"])


# 10 registrations per hour per IP, per the specification's rate-limit table.
# Checked before the body is validated, so a flood cannot buy bcrypt work.
@router.post("/register", response_model=UserResponse,
             status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(rate_limit_by_ip(REGISTRATION))])
def register(
    payload: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    return AuthService(db).register(
        email=payload.email,
        full_name=payload.full_name,
        password=payload.password,
        role=payload.role,
        ip_address=client_ip(request),
        user_agent=user_agent(request),
    )


# 60 sign-in attempts per hour per IP. Note this counts ATTEMPTS, successful
# or not: a credential-stuffing run is made of failures, so counting only
# failures would leave the budget untouched by the thing it exists to slow.
@router.post("/login", response_model=TokenResponse,
             dependencies=[Depends(rate_limit_by_ip(LOGIN))])
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    auth = AuthService(db)
    user = auth.authenticate(
        payload.email, payload.password,
        ip_address=client_ip(request), user_agent=user_agent(request),
    )
    return {**auth.issue_tokens(user), "user": user}


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Start a password reset: email a one-time code to the account.

    The request also appears on the admin's reset list, as a fallback for
    users who cannot reach their email. The reply is identical whether or not
    the email has an account, so the endpoint cannot be used to discover which
    emails are registered.
    """
    from ..services.user_service import UserService

    UserService(db).request_password_reset(
        payload.email, ip_address=client_ip(request), user_agent=user_agent(request))
    return {"message": "If that email has an account, we have sent it a 6-digit code. "
                       "It expires in 10 minutes."}


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Finish a password reset with the emailed code and a new password."""
    from ..services.user_service import UserService

    UserService(db).confirm_password_reset(payload.email, payload.code, payload.new_password)
    return {"message": "Your password has been changed. You can now log in."}


@router.post("/refresh", response_model=RefreshResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> dict:
    return AuthService(db).refresh(payload.refresh_token)


@router.post("/logout", response_model=MessageResponse)
def logout(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    AuthService(db).logout(current_user, ip_address=client_ip(request))
    return {"message": "Logged out"}


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.patch("/me", response_model=UserResponse)
def update_me(
    payload: ConsentUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> User:
    return AuthService(db).update_consent(
        current_user,
        camera=payload.camera_consent,
        geolocation=payload.geolocation_consent,
    )