"""Aggregate statistics — ``/stats``, and attendance export — ``/export``.

Consumed by module 4's observability dashboard. Staff-only throughout: these
figures summarise every student's attendance, which is not self-service data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from ..models import User, UserRole
from ..services.stats_service import StatsService
from ..services.user_service import UserService


def _require_session_scope(db: Session, user: User, session_id: str) -> None:
    """Instructors and TAs may only pull figures for their own sessions."""
    from ..models import AttendanceSession
    from ..services.exceptions import NotFoundError
    from ..services.staff_scope import require_session_visible

    found = db.get(AttendanceSession, session_id)
    if found is None:
        raise NotFoundError(f"No session with id {session_id}")
    require_session_visible(db, user, found)

router = APIRouter(prefix="/stats", tags=["Statistics"])
export_router = APIRouter(prefix="/export", tags=["Export"])

_STAFF = (UserRole.ADMIN, UserRole.INSTRUCTOR, UserRole.TA)


@router.get("/overview")
def stats_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Institution-wide totals for the dashboard's headline tiles."""
    UserService.require_role(current_user, *_STAFF)
    return StatsService(db).overview()


@router.get("/sessions/{session_id}")
def stats_session(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    UserService.require_role(current_user, *_STAFF)
    _require_session_scope(db, current_user, session_id)
    return StatsService(db).session_stats(session_id)


@router.get("/courses/{course_id}")
def stats_course(
    course_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    from ..services.staff_scope import require_course_visible

    UserService.require_role(current_user, *_STAFF)
    require_course_visible(db, current_user, course_id)
    return StatsService(db).course_stats(course_id)


@router.get("/students/{student_id}")
def stats_student(
    student_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """One student's attendance record.

    Staff, or the student themselves — a student may see their own figures but
    not a classmate's.
    """
    if current_user.id != student_id:
        UserService.require_role(current_user, *_STAFF)
    return StatsService(db).student_stats(student_id)


@export_router.get("/session/{session_id}")
def export_session_attendance(
    session_id: str,
    format: str = Query(default="json", pattern="^(json|csv)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Attendance for one session, including absentees.

    CSV is served as text/csv so a browser downloads it; JSON carries the same
    records plus a summary block.
    """
    UserService.require_role(current_user, *_STAFF)
    _require_session_scope(db, current_user, session_id)
    payload = StatsService(db).session_attendance(session_id)
    from ..models import AuditAction
    from ..services.audit_service import AuditService

    AuditService(db).record_independently(
        AuditAction.DATA_EXPORTED, user_id=current_user.id, resource_type="session",
        resource_id=session_id, details={"format": format,
                                         "records": len(payload["records"])})
    if format == "csv":
        return PlainTextResponse(_to_csv(payload), media_type="text/csv",
                                 headers={"Content-Disposition":
                                          f'attachment; filename="attendance-{session_id}.csv"'})
    return payload


def _to_csv(payload: dict) -> str:
    """Build the CSV with the stdlib writer rather than join(',').

    A student's name can contain a comma or a quote, and hand-rolled joining
    would corrupt the row — in an attendance record, silently.
    """
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["student_id", "student_name", "student_email",
                     "status", "checked_in_at", "risk_score"])
    for record in payload["records"]:
        writer.writerow([record["student_id"], record["student_name"],
                         record["student_email"], record["status"],
                         record["checked_in_at"] or "", record["risk_score"] or ""])
    return buffer.getvalue()
