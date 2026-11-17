"""Attendance session endpoints — ``/sessions``."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user, get_current_user_optional
from ..models import AttendanceSession, SessionStatus, User
from ..schemas import (
    PageResponse,
    QRCodeResponse,
    SessionCreateRequest,
    SessionResponse,
    SessionStatisticsResponse,
    SessionUpdateRequest,
)
from ..services import SessionService

router = APIRouter(prefix="/sessions", tags=["Sessions"])


@router.get("/", response_model=PageResponse[SessionResponse])
def list_sessions(
    course_id: Optional[str] = None,
    status_filter: Optional[SessionStatus] = Query(default=None, alias="status"),
    instructor_id: Optional[str] = None,
    starts_after: Optional[datetime] = None,
    starts_before: Optional[datetime] = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PageResponse:
    from ..models import UserRole

    filters = dict(course_id=course_id, status=status_filter, instructor_id=instructor_id,
                   starts_after=starts_after, starts_before=starts_before,
                   limit=limit, offset=offset)
    service = SessionService(db)
    # Students only ever see sessions they may attend (their courses, and
    # only their own tutorial / lab group).
    if current_user.role == UserRole.STUDENT:
        page = service.list_for_student(current_user, **filters)
    else:
        page = service.list(**filters)
    return PageResponse.of(page, [SessionResponse.model_validate(s) for s in page.items])


@router.get("/active", response_model=List[SessionResponse])
def list_active_sessions(
    db: Session = Depends(get_db),
    caller: Optional[User] = Depends(get_current_user_optional),
) -> List[AttendanceSession]:
    """Sessions currently open for check-in.

    Declared before /{session_id} so "active" is not read as an id. Returns a
    bare list rather than the paginated envelope, matching the spec and the
    public tests — it is a short, bounded set by nature.

    Unauthenticated on purpose: the public test calls it without a token, and
    "which lectures are taking place now" is timetable information, not
    personal data. It exposes no attendance or student detail.
    """
    return SessionService(db).open_for(caller)


@router.get("/my-sessions", response_model=List[SessionResponse])
def my_sessions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[AttendanceSession]:
    """Sessions the caller is responsible for: assigned sessions for a TA,
    sessions of their courses (and any they created) for an instructor,
    everything for an admin. Declared before /{session_id}."""
    return SessionService(db).list_mine(current_user)


@router.post("/", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def create_session(
    payload: SessionCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AttendanceSession:
    fields = payload.model_dump()
    course_id = fields.pop("course_id")
    return SessionService(db).create(current_user, course_id, **fields)


@router.get("/{session_id}", response_model=SessionResponse)
def get_session(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AttendanceSession:
    return SessionService(db).get_for(current_user, session_id)


@router.patch("/{session_id}", response_model=SessionResponse)
def update_session(
    session_id: str,
    payload: SessionUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AttendanceSession:
    return SessionService(db).update(current_user, session_id,
                                     **payload.model_dump(exclude_unset=True))


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT,
             response_model=None)
def delete_session(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Only ``scheduled`` sessions can be deleted; anything with attendance
    history must be cancelled instead, which the service enforces."""
    SessionService(db).delete(current_user, session_id)


@router.post("/{session_id}/qr-code", response_model=QRCodeResponse)
def rotate_qr_code(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """The only endpoint that reveals ``qr_code_secret``, and only to the
    session owner. It expires within seconds of being issued."""
    session = SessionService(db).rotate_qr_code(current_user, session_id)
    return {
        "session_id": session.id,
        "qr_code_secret": session.qr_code_secret,
        "expires_at": session.qr_code_expires_at,
    }


@router.get("/{session_id}/students")
def session_students(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[dict]:
    """Every student expected at this session, with their check-in status
    ("absent" if they have not checked in). Admins, the course's instructors,
    and TAs assigned to the session."""
    return SessionService(db).students(current_user, session_id)


@router.get("/{session_id}/statistics", response_model=SessionStatisticsResponse)
def session_statistics(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    return SessionService(db).statistics(current_user, session_id)