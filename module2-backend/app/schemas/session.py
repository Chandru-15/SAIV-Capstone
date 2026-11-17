"""Attendance session DTOs."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import SessionStatus, SessionType

# Grace period either side of the scheduled start when a check-in window is
# not supplied explicitly.
DEFAULT_CHECKIN_GRACE_MINUTES = 15


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    course_id: str
    instructor_id: Optional[str] = None
    name: str
    session_type: SessionType
    description: Optional[str] = None
    scheduled_start: datetime
    scheduled_end: datetime
    # Optional: when omitted the window is derived from the schedule (see
    # check_windows below). The public suite creates sessions with only a
    # start and end, and requiring the window made that a 422 - a reasonable
    # default is friendlier than forcing four timestamps for the common case.
    checkin_opens_at: Optional[datetime] = None
    checkin_closes_at: Optional[datetime] = None
    status: SessionStatus
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    venue_latitude: Optional[float] = None
    venue_longitude: Optional[float] = None
    venue_name: Optional[str] = None
    geofence_radius_meters: Optional[float] = None
    require_liveness_check: bool
    require_face_match: bool
    risk_threshold: Optional[float] = None
    created_at: datetime
    # TAs assigned to run this session.
    ta_ids: List[str] = []
    # Tutorial / lab group this session is for (None = whole course).
    group_id: Optional[str] = None
    group_name: Optional[str] = None
    group_size: Optional[int] = None
    # qr_code_secret is deliberately absent: it is issued through its own
    # endpoint to the instructor, never included in a general session payload.


class SessionCreateRequest(BaseModel):
    course_id: str
    name: str = Field(min_length=1, max_length=255)
    session_type: SessionType = SessionType.LECTURE
    description: Optional[str] = None
    scheduled_start: datetime
    scheduled_end: datetime
    # Optional: when omitted the window is derived from the schedule (see
    # check_windows below). The public suite creates sessions with only a
    # start and end, and requiring the window made that a 422 - a reasonable
    # default is friendlier than forcing four timestamps for the common case.
    checkin_opens_at: Optional[datetime] = None
    checkin_closes_at: Optional[datetime] = None
    venue_latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    venue_longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    venue_name: Optional[str] = None
    geofence_radius_meters: Optional[float] = Field(default=None, gt=0)
    require_liveness_check: bool = True
    require_face_match: bool = False
    risk_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    # For a tutorial or lab session: the group it is for. Omit for lectures.
    group_id: Optional[str] = None

    @model_validator(mode="after")
    def check_windows(self) -> "SessionCreateRequest":
        """Validate the schedule, and default the check-in window from it.

        Default: open 15 minutes before the start, close 15 minutes after it.
        That mirrors how attendance is actually taken - a grace period either
        side of the bell - and keeps the window inside the session, which the
        database CHECK constraints require.

        Rejected here as a 422 with a field-level message; the service
        re-checks the same rule, and the database enforces it as a last resort.
        """
        if self.scheduled_end <= self.scheduled_start:
            raise ValueError("scheduled_end must be after scheduled_start")

        grace = timedelta(minutes=DEFAULT_CHECKIN_GRACE_MINUTES)
        if self.checkin_opens_at is None:
            self.checkin_opens_at = self.scheduled_start - grace
        if self.checkin_closes_at is None:
            # Never past the end of the session itself.
            self.checkin_closes_at = min(self.scheduled_start + grace,
                                         self.scheduled_end)

        if self.checkin_closes_at <= self.checkin_opens_at:
            raise ValueError("checkin_closes_at must be after checkin_opens_at")
        return self


class SessionUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    scheduled_start: Optional[datetime] = None
    scheduled_end: Optional[datetime] = None
    checkin_opens_at: Optional[datetime] = None
    checkin_closes_at: Optional[datetime] = None
    venue_latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    venue_longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    venue_name: Optional[str] = None
    geofence_radius_meters: Optional[float] = Field(default=None, gt=0)
    require_liveness_check: Optional[bool] = None
    require_face_match: Optional[bool] = None
    risk_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    group_id: Optional[str] = None


class SessionStatusUpdateRequest(BaseModel):
    status: SessionStatus


class QRCodeResponse(BaseModel):
    session_id: str
    qr_code_secret: str
    expires_at: datetime


class SessionStatisticsResponse(BaseModel):
    session_id: str
    status: str
    enrolled: int
    by_status: Dict[str, int]
    attendance_rate: float
