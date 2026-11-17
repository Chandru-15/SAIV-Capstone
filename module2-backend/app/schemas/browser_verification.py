"""DTOs for browser key enrollment and check-in proofs."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class BrowserChallengeResponse(BaseModel):
    challenge_id: str
    message: str
    expires_at: datetime


class BrowserEnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, max_length=36)
    public_key: str = Field(min_length=122, max_length=122)
    recovery_device_id: str | None = Field(default=None, min_length=1, max_length=36)


class BrowserEnrollmentComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: str = Field(min_length=43, max_length=43)
    public_key: str = Field(min_length=122, max_length=122)
    signature: str = Field(min_length=86, max_length=86)
    password: SecretStr


class BrowserCheckInChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=36)
    device_fingerprint: str = Field(min_length=8, max_length=64)
