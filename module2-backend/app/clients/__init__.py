"""Outbound clients for the other SAIV modules.

Everything that leaves this service over HTTP lives here, so the rest of the
backend never constructs a URL or parses another module's JSON.
"""

from .face_client import (
    MAX_CHALLENGE_FRAMES,
    FaceClient,
    FaceEnrollResult,
    FaceImageRejected,
    FaceServiceError,
    FaceServiceUnavailable,
    FaceVerifyResult,
    LivenessResult,
    RiskAssessResult,
    normalise_image,
)

__all__ = [
    "FaceClient",
    "MAX_CHALLENGE_FRAMES",
    "FaceServiceError",
    "FaceServiceUnavailable",
    "FaceImageRejected",
    "FaceEnrollResult",
    "FaceVerifyResult",
    "LivenessResult",
    "RiskAssessResult",
    "normalise_image",
]
