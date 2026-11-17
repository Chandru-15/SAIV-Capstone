"""Audit log endpoints — ``/audit``."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import client_ip, get_current_user, user_agent
from ..models import AuditLog, User, UserRole
from ..schemas import AuditLogCreateRequest, AuditLogResponse, PageResponse
from ..services import AuditService
from ..services.user_service import UserService

router = APIRouter(prefix="/audit", tags=["Audit"])


@router.get("/", response_model=PageResponse[AuditLogResponse])
def list_logs_at_root(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PageResponse:
    """Admin-only audit trail, paginated.

    The collection root alongside /audit/logs; same data, same envelope. A
    non-admin gets 403, not 404 — the endpoint exists, they may not read it.
    """
    UserService.require_role(current_user, UserRole.ADMIN)
    page = AuditService(db).search(limit=limit, offset=offset)
    return PageResponse.of(page, [AuditLogResponse.model_validate(a) for a in page.items])


@router.get("/summary")
def audit_summary(
    days: int = Query(default=7, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Action counts over a recent window, for the observability dashboard."""
    UserService.require_role(current_user, UserRole.ADMIN)
    return AuditService(db).summary(days)


@router.get("/logs", response_model=PageResponse[AuditLogResponse])
def list_logs(
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    success: Optional[bool] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PageResponse:
    """Admin only. The audit trail records who did what to whom, so reading it
    is itself a privileged action."""
    UserService.require_role(current_user, UserRole.ADMIN)
    page = AuditService(db).search(
        user_id=user_id, action=action, resource_type=resource_type,
        resource_id=resource_id, ip_address=ip_address, success=success,
        since=since, until=until, limit=limit, offset=offset,
    )
    return PageResponse.of(page, [AuditLogResponse.model_validate(a) for a in page.items])


@router.post("/logs", response_model=AuditLogResponse,
             status_code=status.HTTP_201_CREATED)
def create_log(
    payload: AuditLogCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Append a client-reported event.

    ``user_id`` is taken from the authenticated caller and ``ip_address`` from
    the connection — never from the request body. Otherwise this endpoint would
    let any caller forge trail entries attributed to someone else.
    """
    service = AuditService(db)
    entry = service.record(
        payload.action,
        user_id=current_user.id,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        ip_address=client_ip(request),
        user_agent=user_agent(request),
        details=payload.details,
        success=payload.success,
    )
    db.commit()
    return entry
