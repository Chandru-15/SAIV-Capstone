"""Staff assignment endpoints — ``/admin/assignments``.

    POST   /admin/assignments/instructor                       admin
    GET    /admin/assignments/instructor/{course_id}           staff
    DELETE /admin/assignments/instructor/{course_id}/{user_id} admin

    POST   /admin/assignments/ta                               admin, course instructor
    GET    /admin/assignments/ta/{session_id}                  staff
    DELETE /admin/assignments/ta/{session_id}/{user_id}        admin, course instructor

    GET    /admin/assignments/directory/{role}                 pickers (instructor|ta)
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from ..models import User, UserRole
from ..services.assignment_service import AssignmentService

router = APIRouter(prefix="/admin/assignments", tags=["Assignments"])


class InstructorAssignmentRequest(BaseModel):
    course_id: str
    instructor_id: str


class TAAssignmentRequest(BaseModel):
    session_id: str
    ta_id: str


class StaffMember(BaseModel):
    id: str
    email: str
    full_name: str
    role: str


@router.post("/instructor", response_model=StaffMember, status_code=status.HTTP_201_CREATED)
def assign_instructor(payload: InstructorAssignmentRequest, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)) -> dict:
    """Idempotent: assigning someone already assigned is a no-op, not a 409."""
    return AssignmentService(db).assign_instructor(
        current_user, payload.course_id, payload.instructor_id)


@router.get("/instructor/{course_id}", response_model=List[StaffMember])
def list_instructors(course_id: str, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)) -> list:
    return AssignmentService(db).course_instructors(current_user, course_id)


@router.delete("/instructor/{course_id}/{instructor_id}",
               status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def unassign_instructor(course_id: str, instructor_id: str, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)) -> None:
    AssignmentService(db).unassign_instructor(current_user, course_id, instructor_id)


@router.post("/ta", response_model=StaffMember, status_code=status.HTTP_201_CREATED)
def assign_ta(payload: TAAssignmentRequest, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)) -> dict:
    return AssignmentService(db).assign_ta(current_user, payload.session_id, payload.ta_id)


@router.get("/ta/{session_id}", response_model=List[StaffMember])
def list_tas(session_id: str, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user)) -> list:
    return AssignmentService(db).session_tas(current_user, session_id)


@router.delete("/ta/{session_id}/{ta_id}", status_code=status.HTTP_204_NO_CONTENT,
               response_model=None)
def unassign_ta(session_id: str, ta_id: str, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)) -> None:
    AssignmentService(db).unassign_ta(current_user, session_id, ta_id)


@router.get("/directory/{role}", response_model=List[StaffMember])
def staff_directory(role: UserRole, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)) -> list:
    """Active instructors (admin only) or TAs (admin and instructors)."""
    return AssignmentService(db).staff_directory(current_user, role)
