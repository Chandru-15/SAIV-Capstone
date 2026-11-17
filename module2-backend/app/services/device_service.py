"""Device registration and binding."""

from __future__ import annotations

from typing import List, Optional

from ..models import AuditAction, Device, DevicePlatform, User
from ..repositories import DeviceRepository
from .audit_service import AuditService
from .base import BaseService, transactional
from .exceptions import ConflictError, NotFoundError, PermissionDeniedError


class DeviceService(BaseService):
    def __init__(self, session) -> None:
        super().__init__(session)
        self.devices = DeviceRepository(session)
        self.audit = AuditService(session)

    @transactional
    def register(
        self, user: User, device_fingerprint: str, device_name: Optional[str] = None,
        platform: Optional[str] = None, browser: Optional[str] = None,
        os_version: Optional[str] = None, app_version: Optional[str] = None,
    ) -> Device:
        existing = self.devices.find_by_fingerprint(device_fingerprint, lock=True)
        if existing is not None and existing.user_id != user.id:
            # Fingerprints are globally unique, and one device shared across two
            # accounts is exactly the signal device binding exists to surface.
            # Refuse rather than silently reassign it.
            raise ConflictError("This device is already registered to another account")

        if existing is not None:
            if not existing.is_active or existing.revoked_at is not None:
                raise PermissionDeniedError("This browser registration has been revoked")
            # Idempotent for the caller's own device: a browser that clears
            # storage and re-announces itself must not be locked out.
            existing.device_name = device_name or existing.device_name
            existing.platform = self._platform(platform) or existing.platform
            existing.browser = browser or existing.browser
            existing.os_version = os_version or existing.os_version
            existing.app_version = app_version or existing.app_version
            self.devices.touch_seen(existing)
            return existing

        device = self.devices.save(
            Device(
                user_id=user.id,
                device_fingerprint=device_fingerprint,
                device_name=device_name,
                platform=self._platform(platform),
                browser=browser,
                os_version=os_version,
                app_version=app_version,
                # Only the proof-of-possession enrollment endpoint binds keys.
                public_key="",
                is_trusted=False,
            )
        )
        self.audit.record(
            AuditAction.DEVICE_REGISTERED, user_id=user.id, resource_type="device",
            resource_id=device.id,
            details={"platform": device.platform.value if device.platform else None, "key_bound": False},
        )
        return device

    def list_for(self, user: User) -> List[Device]:
        return self.devices.find_by_user(user.id, active_only=False)

    def search(self, user_id: Optional[str] = None, is_active: Optional[bool] = None,
               limit: int = 50, offset: int = 0):
        """Fleet-wide device search for the admin view."""
        from sqlalchemy import select

        from ..models import Device as DeviceModel

        stmt = select(DeviceModel)
        if user_id is not None:
            stmt = stmt.where(DeviceModel.user_id == user_id)
        if is_active is not None:
            stmt = stmt.where(DeviceModel.is_active.is_(is_active))
        return self.devices.paginate(stmt.order_by(DeviceModel.last_seen_at.desc()),
                                     limit, offset)

    @transactional
    def revoke(self, user: User, device_id: str, reason: str) -> Device:
        device = self.devices.lock_owned(user.id, device_id).get(device_id)
        if device is None:
            if self.devices.find_by_id(device_id) is not None:
                raise PermissionDeniedError("You may only revoke your own devices")
            raise NotFoundError(f"No device with id {device_id}")
        self.devices.revoke(device, reason)
        return device

    @staticmethod
    def _platform(value: Optional[str]) -> Optional[DevicePlatform]:
        if not value:
            return None
        try:
            return DevicePlatform(value.lower())
        except ValueError:
            # The column has a CHECK constraint; an unrecognised platform string
            # is dropped rather than failing registration over a cosmetic field.
            return None
