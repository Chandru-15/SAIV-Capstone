"""Individual risk indicators contributing to a check-in's risk score."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, NOW_UTC, enum_column, pk_column, utcnow
from .enums import RiskSeverity, RiskSignalType

if TYPE_CHECKING:
    from .checkin import CheckIn

# Kept in sync with ck_risk_signals_type in db/schema.sql.
_SIGNAL_TYPE_VALUES = ", ".join(f"'{member.value}'" for member in RiskSignalType)


class RiskSignal(Base):
    """One detected indicator. ``weight`` is this signal's contribution to
    ``CheckIn.risk_score``; ``confidence`` is how sure the detector is."""

    __tablename__ = "risk_signals"

    id: Mapped[str] = pk_column()
    checkin_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("checkins.id", ondelete="CASCADE"), nullable=False
    )
    signal_type: Mapped[RiskSignalType] = mapped_column(
        enum_column(RiskSignalType, 50), nullable=False
    )
    severity: Mapped[RiskSeverity] = mapped_column(
        enum_column(RiskSeverity, 20), nullable=False
    )
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, server_default=text("1.0")
    )
    details: Mapped[Optional[str]] = mapped_column(Text)  # JSON metadata
    weight: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.1, server_default=text("0.1")
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=NOW_UTC
    )

    # -- relationships ------------------------------------------------------
    checkin: Mapped["CheckIn"] = relationship(back_populates="risk_signals")

    __table_args__ = (
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_risk_signals_severity",
        ),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_risk_signals_confidence"),
        CheckConstraint("weight BETWEEN 0 AND 1", name="ck_risk_signals_weight"),
        CheckConstraint(
            f"signal_type IN ({_SIGNAL_TYPE_VALUES})", name="ck_risk_signals_type"
        ),
        Index("ix_risk_signals_checkin_id", "checkin_id"),
        Index("ix_risk_signals_signal_type", "signal_type"),
        Index("ix_risk_signals_severity", "severity"),
        Index("ix_risk_signals_detected_at", "detected_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<RiskSignal {self.signal_type} {self.severity} w={self.weight}>"
