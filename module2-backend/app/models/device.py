"""Registered client devices used for device binding."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, NOW_UTC, enum_column, pk_column, utcnow
from .enums import DevicePlatform, TrustScore

if TYPE_CHECKING:
    from .checkin import CheckIn
    from .user import User


class Device(Base):
    """A device bound to a user.

    Only a key with a ``browser_key_id`` has passed the browser key enrollment
    ceremony. Platform attestation is separate.
    """

    __tablename__ = "devices"

    id: Mapped[str] = pk_column()
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    device_name: Mapped[Optional[str]] = mapped_column(String(255))
    platform: Mapped[Optional[DevicePlatform]] = mapped_column(
        enum_column(DevicePlatform, 50)
    )
    browser: Mapped[Optional[str]] = mapped_column(String(100))
    os_version: Mapped[Optional[str]] = mapped_column(String(50))
    app_version: Mapped[Optional[str]] = mapped_column(String(50))

    # Device-bound signing key
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    public_key_created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=NOW_UTC
    )
    public_key_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    browser_key_id: Mapped[Optional[str]] = mapped_column(String(43))

    # Platform attestation.
    #
    # ``last_attestation_at`` is what says a ceremony RAN; ``attestation_passed``
    # is only its verdict. Whatever comes to write these must stamp
    # ``last_attestation_at`` on failure too, because the risk engine reads a
    # null timestamp as "no verdict available" and omits the signal entirely
    # (see CheckInContext.attestation_known). Setting the flag without the
    # timestamp makes a failure invisible; setting the timestamp without a
    # genuine ceremony makes every device look attested.
    #
    # Nothing writes them yet: a browser cannot produce a Play Integrity or
    # App Attest token, so these stay at their defaults for every web device.
    # ``is_emulator`` and ``is_rooted_jailbroken`` below are in the same
    # position and are not knowable from a browser at all.
    attestation_passed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    last_attestation_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    attestation_token: Mapped[Optional[str]] = mapped_column(Text)

    # Trust posture
    is_trusted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    trust_score: Mapped[TrustScore] = mapped_column(
        enum_column(TrustScore, 20),
        nullable=False,
        default=TrustScore.LOW,
        server_default="low",
    )
    is_emulator: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_rooted_jailbroken: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=NOW_UTC
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=NOW_UTC
    )
    total_checkins: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    revocation_reason: Mapped[Optional[str]] = mapped_column(Text)

    @property
    def browser_key_usable(self) -> bool:
        return bool(
            self.is_active and self.revoked_at is None and self.browser_key_id
            and (self.public_key_expires_at is None or self.public_key_expires_at > utcnow())
        )

    # -- relationships ------------------------------------------------------
    user: Mapped["User"] = relationship(back_populates="devices")
    checkins: Mapped[List["CheckIn"]] = relationship(back_populates="device")

    __table_args__ = (
        CheckConstraint(
            "platform IS NULL OR platform IN ('ios', 'android', 'web', 'desktop')",
            name="ck_devices_platform",
        ),
        CheckConstraint(
            "trust_score IN ('low', 'medium', 'high')", name="ck_devices_trust_score"
        ),
        CheckConstraint("total_checkins >= 0", name="ck_devices_total_checkins"),
        Index("ix_devices_user_id", "user_id"),
        Index("ix_devices_fingerprint", "device_fingerprint"),
        Index("ix_devices_is_active", "is_active"),
        Index("ix_devices_is_trusted", "is_trusted"),
        Index("uq_devices_browser_key_id", "browser_key_id", unique=True),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Device {self.device_fingerprint[:12]}... user={self.user_id}>"
