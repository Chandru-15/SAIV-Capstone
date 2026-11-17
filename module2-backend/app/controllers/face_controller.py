"""Face enrolment endpoints — ``/users/me/face``.

Thin, like every controller: these delegate to FaceService, which owns consent
and persistence, and which talks to module 3 through FaceClient.

Note the absence of an admin "enrol this other user's face" endpoint. Face
enrolment is something a person does to themselves, in front of a camera; an
endpoint that let staff submit a photo on someone else's behalf would make the
whole biometric check meaningless. The same reasoning applies to re-enrolment,
which is why PUT here is also scoped to ``/users/me``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from ..clients.face_client import FACE_SERVICE_URL
from ..database import get_db
from ..dependencies import client_ip, get_current_user, user_agent
from ..models import FaceReEnrollStatus, User
from ..schemas import (
    FaceEnrollRequest,
    FaceReEnrollRequestCreate,
    FaceReEnrollRequestResponse,
    FaceReEnrollReviewRequest,
    FaceReEnrollSubmitRequest,
    LivenessFramesRequest,
    LivenessFramesResponse,
    FaceEnrollResponse,
    FaceReEnrollResponse,
    FaceServiceHealthResponse,
    FaceVerifyRequest,
    FaceVerifyResponse,
    LivenessCheckRequest,
    LivenessCheckResponse,
    PageResponse,
    UserResponse,
)
from ..services.face_service import FaceService

router = APIRouter(prefix="/users/me/face", tags=["Face Recognition"])

# Reviewing is not something a caller does to themselves, so it cannot sit
# under /users/me. Only admins decide; the path is kept as it was so the
# dashboard and any existing clients keep working.
reenroll_review_router = APIRouter(prefix="/face-reenroll-requests",
                                   tags=["Face Recognition"])


@router.post("/enroll", response_model=FaceEnrollResponse)
def enroll_face(
    payload: FaceEnrollRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Enrol the caller's own face.

    Requires ``camera_consent``. Returns 400 without consent or when module 3
    finds no usable face, and 503 when module 3 is unreachable — the three
    outcomes the API specification documents.
    """
    return FaceService(db).enroll(
        current_user, payload.image,
        ip_address=client_ip(request), user_agent=user_agent(request),
    )


@router.put("", response_model=FaceReEnrollResponse)
def reenroll_face(
    payload: FaceReEnrollSubmitRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Replace the caller's own enrolled face template, spending an approval.

    A PUT, because it replaces the one template this user has rather than
    adding another. Kept apart from ``POST /enroll`` so that discarding a
    working template is always a deliberate act and shows up in the audit
    trail as ``face_re_enrolled``.

    Needs an approved request from ``POST /reenroll-requests`` that the caller
    has not spent yet; the approval is consumed here. Returns 403 when there is
    none, so holding a valid access token is no longer enough on its own to
    replace the biometric reference every later check-in is matched against.

    Returns 400 when nothing is enrolled yet, when consent is missing, or when
    module 3 refuses the photo — in which case the old template is untouched
    and the approval is NOT consumed, so the student can retry.
    """
    return FaceService(db).reenroll(
        current_user, payload.image,
        ip_address=client_ip(request), user_agent=user_agent(request),
    )


@router.post("/reenroll-requests", response_model=FaceReEnrollRequestResponse,
             status_code=status.HTTP_201_CREATED)
def create_reenroll_request(
    payload: FaceReEnrollRequestCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Ask an admin for permission to replace your face template.

    Sends no photo. The student captures the replacement only once this has
    been approved, so nothing biometric is stored while a request waits.

    Returns 400 when no face is enrolled yet, or when the caller already has a
    request pending or an approval they have not used.
    """
    return FaceService(db).request_reenroll(
        current_user, payload.reason,
        ip_address=client_ip(request), user_agent=user_agent(request),
    )


@router.get("/reenroll-requests",
            response_model=PageResponse[FaceReEnrollRequestResponse])
def my_reenroll_requests(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PageResponse:
    """The caller's own requests, newest first, so the portal can show whether
    one is pending, approved and unused, or was denied and why."""
    page = FaceService(db).my_reenroll_requests(current_user, limit=limit, offset=offset)
    return PageResponse.of(
        page, [FaceReEnrollRequestResponse.model_validate(r) for r in page.items])


@reenroll_review_router.get(
    "", response_model=PageResponse[FaceReEnrollRequestResponse])
def reenroll_queue(
    status_filter: Optional[FaceReEnrollStatus] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PageResponse:
    """Every re-enrolment request, oldest first. Admin only.

    Enforced on the server, not just in the dashboard: instructors, TAs and
    students get 403.
    """
    page = FaceService(db).reenroll_queue(
        current_user, status=status_filter, limit=limit, offset=offset)
    return PageResponse.of(
        page, [FaceReEnrollRequestResponse.model_validate(r) for r in page.items])


@reenroll_review_router.patch(
    "/{request_id}", response_model=FaceReEnrollRequestResponse)
def review_reenroll_request(
    request_id: str,
    payload: FaceReEnrollReviewRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approve or deny one request. Admin only.

    The decision is final: a request that has already been decided returns 400
    rather than flipping, so an approval cannot be withdrawn after the student
    has spent it. An approval is single-use and does not expire.
    """
    return FaceService(db).review_reenroll(
        current_user, request_id, approve=payload.approve, notes=payload.notes,
        ip_address=client_ip(request), user_agent=user_agent(request),
    )


@router.post("/verify", response_model=FaceVerifyResponse)
def verify_face(
    payload: FaceVerifyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Check a fresh photo against the caller's enrolled template.

    Useful for letting a student confirm their enrolment still works before a
    session starts, rather than discovering it at check-in.
    """
    result = FaceService(db).verify(current_user, payload.image)
    return {
        "match_passed": result.match_passed,
        "match_score": result.match_score,
        "match_threshold": result.match_threshold,
        "face_detected": result.face_detected,
    }


@router.post("/liveness", response_model=LivenessCheckResponse)
def check_liveness(
    payload: LivenessCheckRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Run a standalone liveness check.

    Exposed so a client can pre-flight the camera step. The result is NOT
    trusted at check-in: that path calls module 3 again itself.
    """
    result = FaceService(db).check_liveness(payload.image, payload.challenge_type.value)
    return {
        "liveness_passed": result.liveness_passed,
        "liveness_score": result.liveness_score,
        "liveness_threshold": result.liveness_threshold,
        "details": result.details,
    }


@router.post("/liveness-frames", response_model=LivenessFramesResponse)
def check_liveness_frames(
    payload: LivenessFramesRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Run a multi-frame blink / head-turn check without a bound challenge.

    For the frontend to pre-flight its capture loop: confirm the camera,
    framing and frame count produce a pass before a real check-in depends on
    it. The result carries no weight at check-in, which issues and consumes
    its own server-chosen challenge.
    """
    result = FaceService(db).check_liveness_frames(
        payload.frames, payload.challenge_type.value)
    return {
        "liveness_passed": result.liveness_passed,
        "liveness_score": result.liveness_score,
        "liveness_threshold": result.liveness_threshold,
        "challenge_type": payload.challenge_type.value,
        "challenge_passed": result.challenge_passed,
        "frames_received": len(payload.frames),
        "frames_with_face": result.frames_with_face,
        "details": result.details,
    }


@router.delete("/", response_model=UserResponse)
def unenroll_face(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> User:
    """Delete the stored face template. The user's right to withdraw."""
    return FaceService(db).unenroll(current_user)


@router.get("/status", response_model=FaceServiceHealthResponse)
def face_service_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Is module 3 reachable? Lets a client hide the camera step during an
    outage instead of failing at submit time."""
    return {
        "face_service_url": FACE_SERVICE_URL,
        "available": FaceService(db).health(),
    }