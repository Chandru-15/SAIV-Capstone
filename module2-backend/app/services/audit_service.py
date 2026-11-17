"""Audit logging.

Every other service depends on this one, so it deliberately has no
dependencies of its own beyond the repository. It is not decorated with
``@transactional``: audit entries must join the caller's transaction so they
commit or roll back together with the action they describe.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Optional, Union

import json
import logging

from ..models import AuditAction, AuditLog
from ..models.base import utcnow
from ..repositories import AuditLogRepository, Page
from .base import BaseService

logger = logging.getLogger(__name__)


class AuditService(BaseService):
    def __init__(self, session) -> None:
        super().__init__(session)
        self.audit_logs = AuditLogRepository(session)

    def record(
        self,
        action: Union[AuditAction, str],
        *,
        user_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        device_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        success: bool = True,
    ) -> AuditLog:
        """Append one entry. ``details`` is serialised to JSON for the TEXT column."""
        return self.audit_logs.record(
            action=action,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            device_id=device_id,
            details=json.dumps(details, default=str) if details else None,
            success=success,
        )

    def record_independently(self, action, **kwargs: Any) -> None:
        """Write an entry that must survive the caller's rollback.

        Security-relevant failures (a rejected login, a permission violation)
        are recorded on the way out of a service method that then raises — and
        the raise rolls the transaction back, taking the audit row with it.
        That would leave no trace of exactly the events most worth tracing, and
        make brute-force detection impossible.

        So this opens a second short-lived session on the same engine and
        commits there. A failure to audit must never mask the original error,
        hence the broad except.
        """
        from sqlalchemy.orm import Session as SASession

        # Pop unconditionally: leaving `details` in kwargs while also passing it
        # explicitly raises "got multiple values for keyword argument".
        details = kwargs.pop("details", None)
        bind = self.session.get_bind()
        try:
            with SASession(bind) as side_session:
                AuditLogRepository(side_session).record(
                    action=action,
                    details=json.dumps(details, default=str) if details is not None else None,
                    **kwargs,
                )
                side_session.commit()
        except Exception:  # never mask the caller's original failure
            # Logged rather than silently swallowed: a broken audit path must
            # not be invisible just because it cannot be allowed to propagate.
            logger.exception("Failed to write independent audit entry for %s", action)

    def search(self, **filters: Any) -> Page[AuditLog]:
        return self.audit_logs.search(**filters)

    def summary(self, days: int = 7) -> Dict[str, Any]:
        """Action counts over the last `days`, newest window first.

        Aggregated in SQL rather than by loading rows: the audit table is the
        fastest-growing one in the schema, and a dashboard polling this must
        not pull a week of entries into memory to count them.
        """
        from sqlalchemy import func, select

        from ..models import AuditLog

        since = utcnow() - timedelta(days=days)
        rows = self.session.execute(
            select(AuditLog.action, func.count())
            .where(AuditLog.timestamp >= since)
            .group_by(AuditLog.action)
            .order_by(func.count().desc())
        ).all()
        by_action = {action: count for action, count in rows}
        failures = int(self.session.scalar(
            select(func.count()).select_from(AuditLog)
            .where(AuditLog.timestamp >= since, AuditLog.success.is_(False))) or 0)
        return {
            "period_days": days,
            "since": since.isoformat(),
            "total_logs": sum(by_action.values()),
            "by_action": by_action,
            "failures": failures,
        }

    def recent_failures(
        self, minutes: int = 15, ip_address: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> int:
        """Failed actions in a recent window — the input to rate limiting and
        the ``suspicious_ip`` risk signal."""
        since = utcnow() - timedelta(minutes=minutes)
        return self.audit_logs.count_failures_since(
            since, ip_address=ip_address, user_id=user_id
        )
