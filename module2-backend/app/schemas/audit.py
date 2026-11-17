"""Audit log DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: Optional[str] = None
    action: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    device_id: Optional[str] = None
    details: Optional[str] = None
    success: bool
    timestamp: datetime


class AuditLogCreateRequest(BaseModel):
    """``action`` is a free-form string, matching the schema: the spec's list
    of actions ends with "etc.", so new ones must be loggable without a
    migration."""

    action: str = Field(min_length=1, max_length=50)
    resource_type: Optional[str] = Field(default=None, max_length=50)
    resource_id: Optional[str] = Field(default=None, max_length=36)
    details: Optional[dict] = None
    success: bool = True
