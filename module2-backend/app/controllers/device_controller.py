"""Device registration — ``/devices``.

Legacy registration identifies a browser by UUID. Browser key enrollment
separately proves possession and requires password confirmation.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user, rate_limit_by_user
from ..models import Device, User, UserRole
from ..schemas import DeviceRegisterRequest, DeviceResponse, PageResponse
from ..schemas.browser_verification import (
    BrowserChallengeResponse, BrowserEnrollmentComplete, BrowserEnrollmentRequest,
)
from ..services.browser_verification import BrowserVerificationService
from ..services.device_service import DeviceService
from ..services.exceptions import ValidationError
from ..services.rate_limit import BROWSER_ENROLLMENT
from ..services.user_service import UserService

router = APIRouter(prefix="/devices", tags=["Devices"])


class BrowserEnrollmentRoute(APIRoute):
    def get_route_handler(self):
        handle = super().get_route_handler()

        async def redact_validation_errors(request: Request):
            try:
                return await handle(request)
            except RequestValidationError:
                # FastAPI normally returns invalid input, which can include
                # the password from a malformed enrollment request.
                raise ValidationError("Invalid browser enrollment request") from None

        return redact_validation_errors


enrollment_router = APIRouter(route_class=BrowserEnrollmentRoute,
                              dependencies=[Depends(rate_limit_by_user(BROWSER_ENROLLMENT))])


@enrollment_router.post("/browser-key/challenge", response_model=BrowserChallengeResponse,
                        status_code=status.HTTP_201_CREATED)
def request_browser_enrollment(
    payload: BrowserEnrollmentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    return BrowserVerificationService(db).issue_enrollment(
        current_user, payload.device_id, payload.public_key, payload.recovery_device_id
    )


@enrollment_router.post("/browser-key/enroll", response_model=DeviceResponse)
def enroll_browser_key(
    payload: BrowserEnrollmentComplete,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Device:
    return BrowserVerificationService(db).enroll(
        current_user, payload.challenge_id, payload.public_key,
        payload.signature, payload.password.get_secret_value(),
    )


router.include_router(enrollment_router)


@router.post("/", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
def register_device(
    payload: DeviceRegisterRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Device:
    """Register, or re-announce, the caller's device."""
    return DeviceService(db).register(current_user, **payload.model_dump(exclude={"public_key"}))


@router.get("/my-devices", response_model=List[DeviceResponse])
def my_devices(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[Device]:
    """The caller's own devices, as a bare list per the spec.

    Declared before /{device_id} so the literal path wins.
    """
    return DeviceService(db).list_for(current_user)


@router.get("/", response_model=PageResponse[DeviceResponse])
def list_devices(
    user_id: Optional[str] = None,
    is_active: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PageResponse:
    """All registered devices, paginated. Admin only.

    Distinct from /my-devices, which is any user's own devices as a bare list.
    The two shapes are deliberate: /my-devices is a short, bounded set the
    client renders directly, while this is a fleet view that needs paging — and
    seeing every device in the institution is an administrative capability, not
    a self-service one.
    """
    UserService.require_role(current_user, UserRole.ADMIN)
    page = DeviceService(db).search(user_id=user_id, is_active=is_active,
                                    limit=limit, offset=offset)
    return PageResponse.of(page, [DeviceResponse.model_validate(d) for d in page.items])


@router.delete("/{device_id}", response_model=DeviceResponse)
def revoke_device(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Device:
    """Soft revoke: the row stays so historical check-ins still point at a real
    device and a re-registration attempt can be recognised."""
    return DeviceService(db).revoke(current_user, device_id, reason="revoked by user")
