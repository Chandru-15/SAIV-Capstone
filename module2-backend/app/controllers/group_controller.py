"""Course structure and tutorial / lab groups — ``/admin/courses``.

    PUT  /admin/courses/{course_id}/structure       admin   lecture-only, tutorials, labs
    GET  /admin/courses/{course_id}/groups          staff of the course
    POST /admin/courses/{course_id}/groups/allocate admin   random, even allocation
    POST /admin/courses/{course_id}/groups/move     admin   move one student
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from ..models import Course, User
from ..schemas import (
    CourseResponse,
    CourseStructureRequest,
    GroupAllocationRequest,
    GroupMoveRequest,
)
from ..services.group_service import GroupService

router = APIRouter(prefix="/admin/courses", tags=["Course groups"])


@router.put("/{course_id}/structure", response_model=CourseResponse)
def set_structure(course_id: str, payload: CourseStructureRequest,
                  db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)) -> Course:
    return GroupService(db).update_structure(current_user, course_id,
                                             payload.has_tutorials, payload.has_labs)


@router.get("/{course_id}/groups")
def list_groups(course_id: str, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)) -> dict:
    return GroupService(db).overview(current_user, course_id)


@router.post("/{course_id}/groups/allocate")
def allocate(course_id: str, payload: GroupAllocationRequest, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user)) -> dict:
    """mode "reshuffle": randomly re-split everyone, by group_count OR
    max_per_group. mode "add_new": place only students not yet in a group."""
    return GroupService(db).allocate(
        current_user, course_id, payload.kind, group_count=payload.group_count,
        max_per_group=payload.max_per_group, mode=payload.mode)


@router.post("/{course_id}/groups/move")
def move(course_id: str, payload: GroupMoveRequest, db: Session = Depends(get_db),
         current_user: User = Depends(get_current_user)) -> dict:
    return GroupService(db).move(current_user, course_id, payload.student_id, payload.group_id)
