"""User and authentication DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

import html

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer, field_validator

from ..models import UserRole


class UserResponse(BaseModel):
    """Never exposes hashed_password or the face embedding hash."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    camera_consent: bool
    geolocation_consent: bool
    face_enrolled: bool
    browser_verification_required: bool = False
    created_at: datetime
    last_login_at: Optional[datetime] = None
    # Set by DELETE /users/{id} (soft delete): when the account is erased.
    # Lets the dashboard tell a deleted account from a merely deactivated one.
    scheduled_deletion_at: Optional[datetime] = None

    @field_validator("browser_verification_required", mode="before")
    @classmethod
    def _default_browser_verification(cls, value):
        # An unflushed ORM user has not received its database default yet.
        return False if value is None else value

    @field_serializer("full_name")
    def _escape_full_name(self, value: str) -> str:
        """HTML-escape the one free-text, user-controlled field.

        SECURITY-REQUIREMENTS.md: "HTML escape all user-provided content in
        responses". Escaping on the way out rather than on the way in keeps the
        stored value faithful to what the user typed, while no response can
        carry an executable tag. Everything else on this model is either
        validated (email), an enum (role) or a boolean.
        """
        return html.escape(value, quote=False)


class RegisterRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    # Length is re-checked in AuthService: the rule belongs to the domain, and
    # the service must be safe to call from a test or a script too.
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.STUDENT


class ForgotPasswordRequest(BaseModel):
    """Body of ``POST /auth/forgot-password``."""

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Body of ``POST /auth/reset-password``: the emailed code plus a new password."""

    email: EmailStr
    code: str = Field(min_length=4, max_length=12)
    new_password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class ConsentUpdateRequest(BaseModel):
    camera_consent: Optional[bool] = None
    geolocation_consent: Optional[bool] = None


class BulkUserSpec(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None
    role: UserRole = UserRole.STUDENT


class BulkCreateUsersRequest(BaseModel):
    users: List[BulkUserSpec]
    default_password: str = Field(min_length=8, max_length=128)


class BulkCreateUsersResponse(BaseModel):
    requested: int
    created: int
    users: List[UserResponse]


class UserProfileUpdateRequest(BaseModel):
    """Self-service profile update for PUT/PATCH /users/me.

    Deliberately narrow: a user may change their display name and their
    consent flags. Role and is_active are admin-only and live on the admin
    endpoints, so a student cannot promote themselves here.
    """

    full_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    camera_consent: Optional[bool] = None
    geolocation_consent: Optional[bool] = None
