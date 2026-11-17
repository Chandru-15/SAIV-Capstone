"""Course endpoints — ``/courses``."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user, get_current_user_optional
from ..models import Course, User
from ..schemas import (
    CourseCreateRequest,
    CourseResponse,
    CourseUpdateRequest,
    EnrollmentResponse,
    PublicCourseResponse,
    PageResponse,
)
from ..services import CourseService, EnrollmentService

router = APIRouter(prefix="/courses", tags=["Courses"])


@router.get("/")
def list_courses(
    semester: Optional[str] = None,
    is_active: Optional[bool] = True,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    caller: Optional[User] = Depends(get_current_user_optional),
):
    """The course catalogue.

    Readable without a token, but REDACTED for anonymous callers: they get the
    code, name, semester and venue name, and not venue_latitude,
    venue_longitude, geofence_radius_meters or risk_threshold.

    Those four are what an attacker needs - exactly where to place a spoofed
    GPS fix, and how much risk budget they have before being flagged - and this
    endpoint is enumerable without a credential, so leaving them public would
    hand over a standing map of every venue and policy. Authenticated callers
    see the full record, which is the real use case: the dashboard reads this
    after login.
    """
    page = CourseService(db).list(semester=semester, is_active=is_active,
                                  limit=limit, offset=offset)
    model = CourseResponse if caller is not None else PublicCourseResponse
    return PageResponse.of(page, [model.model_validate(c) for c in page.items])


@router.get("/me", response_model=List[CourseResponse])
def my_courses(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[Course]:
    """Declared before ``/{course_id}`` on purpose: FastAPI matches routes in
    registration order, and a path parameter would otherwise swallow "me"."""
    return CourseService(db).list_for_student(current_user.id)


@router.post("/", response_model=CourseResponse, status_code=status.HTTP_201_CREATED)
def create_course(
    payload: CourseCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Course:
    return CourseService(db).create(current_user, **payload.model_dump())


@router.get("/{course_id}", response_model=CourseResponse)
def get_course(
    course_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Course:
    return CourseService(db).get(course_id)


@router.patch("/{course_id}", response_model=CourseResponse)
def update_course(
    course_id: str,
    payload: CourseUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Course:
    # exclude_unset so "not sent" stays distinct from "explicitly null".
    return CourseService(db).update(current_user, course_id,
                                    **payload.model_dump(exclude_unset=True))


@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_course(
    course_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Admin only. Refused with 409 once anyone has checked in to the course,
    so attendance history is never deleted along with it."""
    CourseService(db).delete(current_user, course_id)


@router.get("/{course_id}/roster", response_model=PageResponse[EnrollmentResponse])
def course_roster(
    course_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PageResponse:
    page = EnrollmentService(db).roster(current_user, course_id, limit=limit, offset=offset)
    return PageResponse.of(page, [EnrollmentResponse.model_validate(e) for e in page.items])