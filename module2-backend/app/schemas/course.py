"""Course and enrollment DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class CourseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    description: Optional[str] = None
    semester: str
    is_active: bool
    venue_latitude: Optional[float] = None
    venue_longitude: Optional[float] = None
    venue_name: Optional[str] = None
    geofence_radius_meters: float
    require_face_recognition: bool
    require_device_binding: bool
    risk_threshold: float
    created_at: datetime
    # Instructors assigned to teach this course (several when co-taught).
    instructor_ids: List[str] = []
    # Course structure: lecture-only unless it has tutorials and/or labs.
    has_tutorials: bool = False
    has_labs: bool = False


class CourseCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=255)
    semester: str = Field(min_length=1, max_length=20)
    description: Optional[str] = None
    venue_latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    venue_longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    venue_name: Optional[str] = None
    geofence_radius_meters: float = Field(default=100.0, gt=0)
    require_face_recognition: bool = False
    require_device_binding: bool = True
    risk_threshold: float = Field(default=0.5, ge=0, le=1)
    has_tutorials: bool = False
    has_labs: bool = False


class CourseUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    venue_latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    venue_longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    venue_name: Optional[str] = None
    geofence_radius_meters: Optional[float] = Field(default=None, gt=0)
    require_face_recognition: Optional[bool] = None
    require_device_binding: Optional[bool] = None
    risk_threshold: Optional[float] = Field(default=None, ge=0, le=1)


class EnrollmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    student_id: str
    course_id: str
    is_active: bool
    enrolled_at: datetime
    dropped_at: Optional[datetime] = None


class EnrollmentCreateRequest(BaseModel):
    student_id: str
    course_id: str


class BulkEnrollRequest(BaseModel):
    course_id: str
    emails: List[EmailStr]


class BulkEnrollResult(BaseModel):
    email: str
    status: str


class BulkEnrollResponse(BaseModel):
    results: List[BulkEnrollResult]


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    device_fingerprint: str
    device_name: Optional[str] = None
    platform: Optional[str] = None
    browser: Optional[str] = None
    os_version: Optional[str] = None
    app_version: Optional[str] = None
    is_trusted: bool
    trust_score: str
    browser_key_id: Optional[str] = None
    browser_key_usable: Optional[bool] = None
    is_active: bool
    total_checkins: int
    first_seen_at: datetime
    last_seen_at: datetime
    # public_key, attestation_token and revocation details are deliberately
    # absent: the key is a credential and the token may hold attestation data.


class DeviceRegisterRequest(BaseModel):
    device_fingerprint: str = Field(min_length=8, max_length=64)
    device_name: Optional[str] = Field(default=None, max_length=255)
    platform: Optional[str] = None
    browser: Optional[str] = Field(default=None, max_length=100)
    os_version: Optional[str] = Field(default=None, max_length=50)
    app_version: Optional[str] = Field(default=None, max_length=50)
    # Accepted for wire compatibility with older clients. The registration
    # endpoint ignores it; browser keys are set only by verified enrollment.
    public_key: Optional[str] = None


class PublicCourseResponse(BaseModel):
    """The course catalogue as shown to an UNAUTHENTICATED caller.

    Deliberately omits venue_latitude, venue_longitude, geofence_radius_meters
    and risk_threshold. Those are the exact figures an attacker needs to place
    a spoofed GPS fix and to know how much risk budget they have, and the
    endpoint is enumerable without a credential. Course code, name and
    semester are timetable information and carry no such risk.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    description: Optional[str] = None
    semester: str
    is_active: bool
    venue_name: Optional[str] = None
    created_at: datetime


class CourseStructureRequest(BaseModel):
    has_tutorials: bool
    has_labs: bool


class GroupAllocationRequest(BaseModel):
    """Exactly one of group_count / max_per_group for mode "reshuffle"."""

    kind: str = Field(pattern="^(tutorial|lab)$")
    group_count: Optional[int] = Field(default=None, ge=1, le=200)
    max_per_group: Optional[int] = Field(default=None, ge=1, le=1000)
    mode: str = Field(default="reshuffle", pattern="^(reshuffle|add_new)$")


class GroupMoveRequest(BaseModel):
    student_id: str
    group_id: str
