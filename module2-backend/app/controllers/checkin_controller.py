"""Check-in endpoints — ``/checkins``."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import (
    client_ip,
    client_ip_is_forwarded,
    get_current_user,
    rate_limit_by_user,
    user_agent,
)
from ..models import CheckIn, CheckInStatus, User
from ..clients import MAX_CHALLENGE_FRAMES
from ..schemas import (
    CheckInAppealRequest,
    CheckInCreateRequest,
    CheckInDetailResponse,
    CheckInResponse,
    CheckInReviewRequest,
    LivenessChallengeResponse,
    PageResponse,
)
from ..schemas.browser_verification import BrowserChallengeResponse, BrowserCheckInChallengeRequest
from ..services import CheckInService
from ..services.browser_verification import (
    BrowserProof, BrowserProofError, CHALLENGE_HEADER, SIGNATURE_HEADER, base64url,
)
from ..services.rate_limit import BROWSER_CHECKIN_CHALLENGE, CHECKIN

router = APIRouter(prefix="/checkins", tags=["Check-ins"])


class CheckInSubmissionRoute(APIRoute):
    def get_route_handler(self):
        handle = super().get_route_handler()

        async def redact_validation_errors(request: Request):
            try:
                return await handle(request)
            except RequestValidationError as error:
                # Keep the validation envelope and field names without
                # reflecting submitted QR, coordinates or biometric frames.
                return JSONResponse(status_code=422, content={"detail": [
                    {field: issue[field] for field in ("type", "loc", "msg")} for issue in error.errors()
                ]})

        return redact_validation_errors


submission_router = APIRouter(route_class=CheckInSubmissionRoute)


async def read_browser_proof(request: Request) -> Optional[BrowserProof]:
    challenges = request.headers.getlist(CHALLENGE_HEADER)
    signatures = request.headers.getlist(SIGNATURE_HEADER)
    if not challenges and not signatures:
        return None
    if len(challenges) != 1 or len(signatures) != 1 or not challenges[0] or not signatures[0]:
        raise BrowserProofError("Both browser proof headers are required exactly once.")
    return BrowserProof(challenges[0], signatures[0], base64url(sha256(await request.body()).digest()))


# 10 check-in attempts per minute per user, per the specification's
# rate-limit table. This is the ONLY pacing on retries: the service no longer
# imposes a growing cooldown, so a client may retry freely inside the budget
# and is told to wait only once the budget is spent.
@submission_router.post("/", response_model=CheckInResponse,
             status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(rate_limit_by_user(CHECKIN))])
def submit_checkin(
    payload: CheckInCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    browser_proof: Optional[BrowserProof] = Depends(read_browser_proof),
) -> CheckIn:
    """Submit a check-in.

    Returns 201 whatever the risk verdict: an ``approved``, ``flagged`` and
    ``rejected`` check-in are all successfully *recorded* submissions. The
    outcome is in ``status``, not the HTTP code. 4xx is reserved for
    submissions that never became a record at all — window closed, not
    enrolled, already checked in.
    """
    fields = payload.model_dump()
    session_id = fields.pop("session_id")
    return CheckInService(db).submit(
        current_user, session_id,
        ip_address=client_ip(request), user_agent=user_agent(request),
        # True only when the address came from a TRUSTED proxy's
        # X-Forwarded-For, i.e. it really is the client's rather than the peer
        # we happen to be talking to. Merely receiving the header proves
        # nothing: any caller can send one.
        ip_is_forwarded=client_ip_is_forwarded(request),
        browser_proof=browser_proof,
        **fields,
    )


router.include_router(submission_router)


@router.post("/browser-challenge", response_model=BrowserChallengeResponse,
             status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(rate_limit_by_user(BROWSER_CHECKIN_CHALLENGE))])
def request_browser_challenge(
    payload: BrowserCheckInChallengeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    return CheckInService(db).issue_browser_challenge(
        current_user, payload.session_id, payload.device_fingerprint
    )


@router.post("/liveness-challenge", response_model=LivenessChallengeResponse,
             status_code=status.HTTP_201_CREATED)
def request_liveness_challenge(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Request an active liveness challenge for a session.

    The server picks blink or head-turn unpredictably and binds the challenge
    to this student, this session, a 60-second window, and a single use. Submit
    the captured frames to POST /checkins/ as `challenge_frames` together with
    the returned `challenge_id`.

    Runs the same gates as the check-in itself, so a student who is not
    enrolled or whose window has closed is told before switching the camera on.
    """
    challenge = CheckInService(db).issue_liveness_challenge(current_user, session_id)
    return {
        "challenge_id": challenge.challenge_id,
        "challenge_type": challenge.challenge_type,
        "instruction": challenge.instruction,
        "min_frames": challenge.min_frames,
        "recommended_frames": challenge.recommended_frames,
        "max_frames": MAX_CHALLENGE_FRAMES,
        "expires_at": challenge.expires_at,
        "colour_sequence": list(challenge.colour_sequence),
        "hold_ms": challenge.hold_ms,
        "settle_ms": challenge.settle_ms,
        "frames_per_step": challenge.frames_per_step,
    }


@router.get("/", response_model=PageResponse[CheckInResponse])
def list_checkins(
    session_id: Optional[str] = None,
    student_id: Optional[str] = None,
    course_id: Optional[str] = None,
    status_filter: Optional[CheckInStatus] = Query(default=None, alias="status"),
    min_risk_score: Optional[float] = Query(default=None, ge=0, le=1),
    checked_in_after: Optional[datetime] = None,
    checked_in_before: Optional[datetime] = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PageResponse:
    # Scoped: instructors and TAs see only their own sessions' check-ins.
    page = CheckInService(db).search_for(
        current_user,
        session_id=session_id, student_id=student_id, course_id=course_id,
        status=status_filter, min_risk_score=min_risk_score,
        checked_in_after=checked_in_after, checked_in_before=checked_in_before,
        limit=limit, offset=offset,
    )
    return PageResponse.of(page, [CheckInResponse.model_validate(c) for c in page.items])


@router.get("/me", response_model=PageResponse[CheckInResponse])
def my_checkins(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PageResponse:
    """Registered before ``/{checkin_id}`` so the literal path wins."""
    page = CheckInService(db).my_checkins(current_user, limit=limit, offset=offset)
    return PageResponse.of(page, [CheckInResponse.model_validate(c) for c in page.items])


@router.get("/review-queue", response_model=PageResponse[CheckInResponse])
def review_queue(
    course_id: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PageResponse:
    page = CheckInService(db).review_queue(current_user, course_id=course_id,
                                           limit=limit, offset=offset)
    return PageResponse.of(page, [CheckInResponse.model_validate(c) for c in page.items])


@router.get("/my-checkins", response_model=List[CheckInResponse])
def my_checkins_list(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[CheckIn]:
    """The caller's own check-ins as a bare list.

    Same data as GET /checkins/me, which returns the paginated envelope. Both
    exist because the spec and the public tests use this shape while the
    frontend already consumes the paginated one.
    """
    return list(CheckInService(db).my_checkins(current_user, limit=100).items)


@router.get("/session/{session_id}", response_model=List[CheckInResponse])
def session_checkins(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[CheckIn]:
    """Every check-in for one session. Staff only — this is the attendance
    register, so it names every student who did and did not attend."""
    return CheckInService(db).for_session(current_user, session_id)


@router.get("/flagged", response_model=PageResponse[CheckInResponse])
def flagged_checkins(
    course_id: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PageResponse:
    """Check-ins awaiting review — flagged or appealed.

    The same queue as /checkins/review-queue; this is the address module 4's
    dashboard uses. Declared before /{checkin_id} so "flagged" is not read as
    an id.
    """
    page = CheckInService(db).review_queue(current_user, course_id=course_id,
                                           limit=limit, offset=offset)
    return PageResponse.of(page, [CheckInResponse.model_validate(c) for c in page.items])


@router.get("/{checkin_id}", response_model=CheckInDetailResponse)
def get_checkin(
    checkin_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CheckIn:
    """Includes the risk signals inline, eager-loaded by the repository."""
    return CheckInService(db).get(current_user, checkin_id)


@router.patch("/{checkin_id}/review", response_model=CheckInResponse)
def review_checkin(
    checkin_id: str,
    payload: CheckInReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CheckIn:
    return CheckInService(db).review(current_user, checkin_id,
                                     approve=payload.approve, notes=payload.notes)


@router.post("/{checkin_id}/appeal", response_model=CheckInResponse)
def appeal_checkin(
    checkin_id: str,
    payload: CheckInAppealRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CheckIn:
    return CheckInService(db).appeal(current_user, checkin_id, payload.reason)
