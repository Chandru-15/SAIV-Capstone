"""Controllers — the HTTP layer.

One module per resource, each exposing an ``APIRouter``. The Spring parallel is
one ``@RestController`` per resource with a class-level ``@RequestMapping``.

A controller is deliberately thin: bind the request to a DTO, resolve the
caller, make one service call, shape the response. Business rules live in
services; queries live in repositories. If a controller grows an ``if`` about
domain state, that rule belongs one layer down.
"""

from fastapi import APIRouter

from .admin_controller import enrollment_router
from .admin_controller import router as admin_router
from .assignment_controller import router as assignment_router
from .audit_controller import router as audit_router
from .auth_controller import router as auth_router
from .checkin_controller import router as checkin_router
from .course_controller import router as course_router
from .device_controller import router as device_router
from .face_controller import reenroll_review_router
from .face_controller import router as face_router
from .group_controller import router as group_router
from .session_controller import router as session_router
from .stats_controller import export_router, router as stats_router
from .user_controller import router as user_router

# Aggregate router: main.py includes this one object rather than seven.
api_router = APIRouter()
for _router in (
    auth_router,
    face_router,   # before user_router: /users/me/face/* must not be shadowed
    reenroll_review_router,
    user_router,
    course_router,
    session_router,
    checkin_router,
    device_router,
    audit_router,
    assignment_router,  # before admin_router: both live under /admin
    group_router,
    admin_router,
    enrollment_router,
    stats_router,
    export_router,
):
    api_router.include_router(_router)

__all__ = [
    "api_router",
    "auth_router",
    "user_router",
    "face_router",
    "reenroll_review_router",
    "course_router",
    "session_router",
    "checkin_router",
    "device_router",
    "audit_router",
    "admin_router",
    "assignment_router",
    "group_router",
    "enrollment_router",
    "stats_router",
    "export_router",
]
