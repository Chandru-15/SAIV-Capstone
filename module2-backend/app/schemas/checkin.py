"""Check-in and risk DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..models import CheckInStatus, LivenessChallengeType, RiskSeverity, RiskSignalType


class RiskSignalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    signal_type: RiskSignalType
    severity: RiskSeverity
    confidence: float
    weight: float
    details: Optional[str] = None
    detected_at: datetime


class CheckInResponse(BaseModel):
    """Note the absence of face_embedding_hash: it is stored for comparison,
    never returned to a client."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    student_id: str
    device_id: Optional[str] = None
    browser_key_verified: bool = False
    status: CheckInStatus
    checked_in_at: datetime
    verified_at: Optional[datetime] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_accuracy_meters: Optional[float] = None
    distance_from_venue_meters: Optional[float] = None
    liveness_passed: Optional[bool] = None
    liveness_score: Optional[float] = None
    # Which path produced the verdict above: "blink" / "head_turn" for a
    # server-issued challenge, "passive" for a single still image. Exposed
    # because a record flagged for liveness while liveness_passed is true is
    # otherwise unreadable -- this column is the explanation. Carries no
    # personal data; the image itself is never stored or returned.
    liveness_challenge_type: Optional[str] = None
    face_match_passed: Optional[bool] = None
    face_match_score: Optional[float] = None
    risk_score: float
    risk_factors: Optional[str] = None
    qr_code_verified: bool
    reviewed_by_id: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_notes: Optional[str] = None
    appeal_reason: Optional[str] = None
    appealed_at: Optional[datetime] = None
    # Appeal rules, computed by the model so every client applies the same
    # ones: one appeal per check-in, within APPEAL_WINDOW_DAYS of checking in.
    appeal_deadline: Optional[datetime] = None
    can_appeal: bool = False


class CheckInDetailResponse(CheckInResponse):
    risk_signals: List[RiskSignalResponse] = []
    # Trust level of the bound device (low / medium / high), or None when the
    # check-in came from an unregistered device.
    device_trust_score: Optional[str] = None


class CheckInCreateRequest(BaseModel):
    session_id: str

    # Opt-in re-verification of an earlier failed attempt at this session.
    # Explicit rather than implied by a second POST, so a plain duplicate
    # submission still fails with "already checked in" exactly as the API
    # specification describes, and only a client that means to retry does.
    retry: bool = False
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    location_accuracy_meters: Optional[float] = Field(default=None, ge=0)
    device_fingerprint: Optional[str] = Field(default=None, max_length=64)

    # Preferred path: send the photo and let the backend call module 3 for the
    # liveness and face-match scores. When `image` is present the four
    # client-supplied score fields below are IGNORED.
    image: Optional[str] = Field(
        default=None, min_length=32, max_length=10_000_000,
        description="Base64 face image. When supplied, liveness and face match "
                    "are derived server-side and any client-supplied scores are "
                    "discarded.")

    # Active-challenge path: request a challenge from
    # POST /checkins/liveness-challenge, perform it, then send the ordered
    # frames here with the challenge_id. Takes precedence over `image`.
    challenge_id: Optional[str] = None
    challenge_frames: Optional[List[str]] = Field(
        default=None, max_length=30,
        description="Ordered frames (first to last) for a blink or head-turn "
                    "challenge. Requires challenge_id.")

    # The client-supplied biometric verdicts (liveness_passed, liveness_score,
    # liveness_challenge_type, face_match_passed, face_match_score,
    # face_embedding_hash) were REMOVED.
    #
    # They were kept as a deprecated path for callers predating the module 3
    # integration, which was a mistake: they made the "a required check must
    # pass" rule in CheckInService._decide bypassable. A session with
    # require_face_match=true could be satisfied by simply posting
    # face_match_passed: true, so the gate only constrained honest clients -
    # i.e. not the ones it exists for.
    #
    # Every biometric verdict now comes from module 3, derived from `image` or
    # `challenge_frames` above, or it does not exist. A required check with no
    # verdict counts as unmet.
    qr_code_secret: Optional[str] = None

    # vpn_detected / proxy_detected / tor_detected were removed deliberately.
    # A client-reported network flag is worthless in a risk engine: its only
    # effect is to raise the reporting client's own score, so an honest client
    # gains nothing and a dishonest one omits it. The backend now derives these
    # from the connection itself - see services/network_service.py.


class CheckInReviewRequest(BaseModel):
    approve: bool
    notes: Optional[str] = None


class CheckInAppealRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
