"""Face enrolment and verification DTOs.

``image`` is base64, and these models are the only place it appears. It is
never echoed back in a response and never stored.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from ..models import FaceReEnrollStatus, LivenessChallengeType


class FaceEnrollRequest(BaseModel):
    # A face photo is tens of kilobytes of base64; the ceiling stops a
    # multi-megabyte upload reaching module 3 and tying up its CPU.
    image: str = Field(min_length=32, max_length=10_000_000,
                       description="Base64-encoded PNG/JPEG, data-URL prefix optional")


class FaceEnrollResponse(BaseModel):
    success: bool
    message: str
    face_enrolled: bool
    quality_score: float


class FaceReEnrollSubmitRequest(FaceEnrollRequest):
    """A replacement photo, and nothing else.

    Carries no password. Replacing a template destroys the only biometric
    reference the system holds for this user, and the replacement is what every
    later check-in is matched against -- but a password is something the
    student always knows, so requiring it proved the account holder was present
    and never that anybody else agreed. Authority now comes from an approved
    ``face_reenroll_requests`` row instead, granted by an instructor or admin
    and spent by this call.

    No approval id is carried either: the server finds the caller's own unspent
    approval. A client that could name one could try to name somebody else's.
    """


class FaceReEnrollResponse(BaseModel):
    """Result of replacing an existing template.

    Separate from :class:`FaceEnrollResponse` so a client can tell that a
    previous template was discarded rather than a first one created — the
    user's old template stops working the moment this succeeds.
    """

    success: bool
    message: str
    face_enrolled: bool
    quality_score: float
    previous_template_replaced: bool


class FaceVerifyRequest(BaseModel):
    image: str = Field(min_length=32, max_length=10_000_000)


class FaceVerifyResponse(BaseModel):
    match_passed: bool
    match_score: float
    match_threshold: float
    face_detected: bool


class LivenessCheckRequest(BaseModel):
    image: str = Field(min_length=32, max_length=10_000_000)
    challenge_type: LivenessChallengeType = LivenessChallengeType.PASSIVE


class LivenessCheckResponse(BaseModel):
    liveness_passed: bool
    liveness_score: float
    liveness_threshold: float
    details: Dict[str, Any] = {}


class FaceServiceHealthResponse(BaseModel):
    """Whether module 3 is reachable from this service.

    Surfaced so the frontend can hide the camera step, and the observability
    module can alarm, instead of discovering the outage one failed check-in at
    a time.
    """

    face_service_url: str
    available: bool


class LivenessChallengeResponse(BaseModel):
    """A server-issued active liveness challenge.

    The client must perform `challenge_type`, capture at least `min_frames`
    ordered frames, and submit them with this `challenge_id`. The type is
    chosen by the server: a client that picks its own can pre-record the
    answer, which is exactly what an active challenge is meant to prevent.
    """

    challenge_id: str
    challenge_type: str
    instruction: str
    min_frames: int
    recommended_frames: int
    max_frames: int
    expires_at: datetime

    # -- colour_reflection only; empty / zero for every other type ----------
    #
    # The sequence IS sent to the client, because the client has to display
    # it -- there is no way to light a face with a colour the screen does not
    # show. Knowing the colours is not the same as being able to answer:
    # the frames still have to show a real 3D face responding to them in the
    # right ORDER, inside a single-use sixty-second window, and the server
    # keeps the frame-to-colour pairing to itself.
    colour_sequence: List[str] = Field(default_factory=list)
    # How long to hold each colour, and how long to wait after the change
    # before taking the first frame of that step. An LCD takes tens of
    # milliseconds to finish a transition and the camera starts compensating
    # immediately, so a frame taken too early shows the previous colour.
    hold_ms: int = 0
    settle_ms: int = 0
    # Frames to capture per colour step. The submission must carry exactly
    # len(colour_sequence) * frames_per_step frames, in order: the server
    # recovers which colour each frame belongs to from its position.
    frames_per_step: int = 1


class LivenessFramesRequest(BaseModel):
    """Standalone multi-frame check, for pre-flighting the camera UI.

    Not bound to a challenge, so its result is NOT accepted at check-in —
    that path issues and consumes its own challenge.
    """

    challenge_id: Optional[str] = None
    frames: List[str] = Field(min_length=1, max_length=30)
    challenge_type: LivenessChallengeType = LivenessChallengeType.BLINK


class LivenessFramesResponse(BaseModel):
    liveness_passed: bool
    liveness_score: float
    liveness_threshold: float
    challenge_type: str
    challenge_passed: Optional[bool] = None
    frames_received: int
    frames_with_face: Optional[int] = None
    details: Dict[str, Any] = {}


# -- staff-approved re-enrolment ----------------------------------------------


class FaceReEnrollRequestCreate(BaseModel):
    """What a student sends when they ask to re-enrol.

    No photo. The request is a permission, not a submission: storing a pending
    capture would put biometric data at rest that ``unenroll`` could not erase.
    """

    reason: Optional[str] = Field(
        default=None, max_length=500,
        description="Why the student needs a new template, in their own words")


class FaceReEnrollReviewRequest(BaseModel):
    """An instructor's or admin's decision on one request."""

    approve: bool
    notes: Optional[str] = Field(
        default=None, max_length=500,
        description="Why it was approved or denied; shown to the student")


class FaceReEnrollRequestResponse(BaseModel):
    """One request record.

    ``user_id`` rather than an embedded student object: the dashboard already
    holds the user list it needs to resolve names, and nesting one here would
    hand every reviewer a second route to account details.

    ``used_at`` is what tells a client an approval has already been spent --
    the status stays ``approved``, because it was.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    status: FaceReEnrollStatus
    reason: Optional[str] = None
    reviewed_by_id: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_notes: Optional[str] = None
    used_at: Optional[datetime] = None
    created_at: datetime

    @field_serializer("reason", "review_notes")
    def _escape_free_text(self, value: Optional[str]) -> Optional[str]:
        """SECURITY-REQUIREMENTS.md: HTML escape user-provided content in
        responses. Both of these are typed by a person and rendered in the
        student portal and the dashboard. Escaped on the way out so the stored
        text stays faithful to what was written."""
        return None if value is None else html.escape(value, quote=False)
