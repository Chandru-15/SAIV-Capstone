"""Repository for :class:`~app.models.device.Device`."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import func, select

from ..models import CheckIn, CheckInStatus, Device, TrustScore
from ..models.base import utcnow
from .base import BaseRepository


class DeviceRepository(BaseRepository[Device]):
    model = Device

    def find_by_fingerprint(self, fingerprint: str, *, lock: bool = False) -> Optional[Device]:
        """Fingerprints are globally unique, so this is not scoped to a user —
        which is exactly what makes it useful: it detects one device being
        shared across accounts."""
        stmt = select(Device).where(Device.device_fingerprint == fingerprint)
        if lock:
            stmt = stmt.with_for_update(key_share=True)
        return self.session.scalars(stmt).first()

    def find_by_user(self, user_id: str, active_only: bool = True) -> List[Device]:
        stmt = select(Device).where(Device.user_id == user_id)
        if active_only:
            stmt = stmt.where(Device.is_active.is_(True))
        return list(self.session.scalars(stmt.order_by(Device.last_seen_at.desc())))

    def find_for_user(self, user_id: str, fingerprint: str, *, lock: bool = False) -> Optional[Device]:
        """The device-binding check: is this fingerprint registered to *this*
        user and still active?"""
        stmt = select(Device).where(
            Device.user_id == user_id,
            Device.device_fingerprint == fingerprint,
            Device.is_active.is_(True),
        )
        if lock:
            stmt = stmt.with_for_update(key_share=True)
        return self.session.scalars(stmt).first()

    def lock_owned(self, user_id: str, *device_ids: str) -> dict[str, Device]:
        stmt = (
            select(Device)
            .where(Device.user_id == user_id, Device.id.in_(device_ids))
            .order_by(Device.id)
            .with_for_update(key_share=True)
        )
        return {device.id: device for device in self.session.scalars(stmt)}

    def count_by_user(self, user_id: str, active_only: bool = True) -> int:
        stmt = select(func.count()).select_from(Device).where(Device.user_id == user_id)
        if active_only:
            stmt = stmt.where(Device.is_active.is_(True))
        return int(self.session.scalar(stmt) or 0)

    # -- domain operations --------------------------------------------------

    def touch_seen(self, device: Device, count_checkin: bool = False) -> Device:
        device.last_seen_at = utcnow()
        if count_checkin:
            device.total_checkins += 1
        self.session.flush()
        return device

    # Approved check-ins a device needs before it is trusted. Approved, not
    # submitted: a device that keeps getting flagged must not earn trust by
    # volume alone.
    MEDIUM_TRUST_APPROVALS = 3
    HIGH_TRUST_APPROVALS = 10

    def refresh_trust(self, device: Device) -> Device:
        """Recompute ``trust_score`` / ``is_trusted`` from the device's history.

            low     new, revoked, emulator or rooted, or < 3 approved check-ins
            medium  >= 3 approved check-ins
            high    >= 10 approved check-ins AND a bound signing key AND
                    passed platform attestation
        """
        approved = int(self.session.scalar(
            select(func.count()).select_from(CheckIn).where(
                CheckIn.device_id == device.id,
                CheckIn.status == CheckInStatus.APPROVED)) or 0)

        if (not device.is_active or device.is_emulator or device.is_rooted_jailbroken
                or approved < self.MEDIUM_TRUST_APPROVALS):
            level = TrustScore.LOW
        elif (approved >= self.HIGH_TRUST_APPROVALS and device.public_key
              and device.attestation_passed):
            level = TrustScore.HIGH
        else:
            level = TrustScore.MEDIUM

        device.trust_score = level
        device.is_trusted = level != TrustScore.LOW
        self.session.flush()
        return device

    def revoke(self, device: Device, reason: str) -> Device:
        """Soft revoke. The row stays so historical check-ins keep pointing at
        a real device, and so a re-registration attempt can be recognised."""
        device.is_active = False
        device.is_trusted = False
        device.trust_score = TrustScore.LOW
        device.revoked_at = utcnow()
        device.revocation_reason = reason
        self.session.flush()
        return device
