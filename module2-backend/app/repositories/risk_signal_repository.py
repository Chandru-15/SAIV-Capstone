"""Repository for :class:`~app.models.risk_signal.RiskSignal`."""

from __future__ import annotations

from typing import Dict, Iterable, List

from sqlalchemy import func, select

from ..models import RiskSeverity, RiskSignal, RiskSignalType
from .base import BaseRepository


class RiskSignalRepository(BaseRepository[RiskSignal]):
    model = RiskSignal

    def find_by_checkin(self, checkin_id: str) -> List[RiskSignal]:
        stmt = (
            select(RiskSignal)
            .where(RiskSignal.checkin_id == checkin_id)
            .order_by(RiskSignal.detected_at)
        )
        return list(self.session.scalars(stmt))

    def find_by_type(self, signal_type: RiskSignalType, limit: int = 100) -> List[RiskSignal]:
        stmt = (
            select(RiskSignal)
            .where(RiskSignal.signal_type == signal_type)
            .order_by(RiskSignal.detected_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def has_critical(self, checkin_id: str) -> bool:
        """A single critical signal rejects a check-in outright, regardless of
        the weighted score — see the check-in status logic in the API spec."""
        stmt = (
            select(func.count())
            .select_from(RiskSignal)
            .where(
                RiskSignal.checkin_id == checkin_id,
                RiskSignal.severity == RiskSeverity.CRITICAL,
            )
        )
        return bool(self.session.scalar(stmt))

    def record_all(self, checkin_id: str, signals: Iterable[RiskSignal]) -> List[RiskSignal]:
        batch = list(signals)
        for signal in batch:
            signal.checkin_id = checkin_id
        return self.save_all(batch)

    def weighted_score(self, checkin_id: str) -> float:
        """Sum of ``weight * confidence``, clamped to the 0..1 the column allows."""
        stmt = select(func.coalesce(func.sum(RiskSignal.weight * RiskSignal.confidence), 0.0)).where(
            RiskSignal.checkin_id == checkin_id
        )
        return min(1.0, max(0.0, float(self.session.scalar(stmt) or 0.0)))

    def count_by_severity(self, checkin_id: str) -> Dict[str, int]:
        stmt = (
            select(RiskSignal.severity, func.count())
            .where(RiskSignal.checkin_id == checkin_id)
            .group_by(RiskSignal.severity)
        )
        return {
            (severity.value if hasattr(severity, "value") else str(severity)): count
            for severity, count in self.session.execute(stmt)
        }
