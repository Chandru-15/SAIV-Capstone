"""HTTP client for the Module 3 face recognition service.

This is the only place in the backend that knows module 3's wire format. If
its contract changes, this file changes and nothing else does.

Privacy: images pass through in memory and are never logged, cached or
persisted. Only the SHA-256 template hashes module 3 returns are stored.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import httpx

logger = logging.getLogger(__name__)

FACE_SERVICE_URL = os.environ.get("FACE_SERVICE_URL", "http://localhost:8001")

# Face meshing with MediaPipe is CPU-bound and not fast. A generous read
# timeout with a short connect timeout distinguishes "service is down" (fail
# immediately) from "service is thinking" (wait for it).
DEFAULT_TIMEOUT = httpx.Timeout(connect=2.0, read=15.0, write=5.0, pool=2.0)

# Only connection-level faults and 5xx are retried. A 400 ("No face detected")
# is a real answer about the image and will say the same thing next time.
MAX_ATTEMPTS = 2

# Module 3 rejects more than this many frames with a 400. Enforced here too
# so an over-eager client is refused before a megabyte of images is shipped.
MAX_CHALLENGE_FRAMES = 30

_DATA_URL_PREFIX = re.compile(r"^data:image/[a-zA-Z.+-]+;base64,")


class FaceServiceError(Exception):
    """Base class for face service failures."""


class FaceServiceUnavailable(FaceServiceError):
    """Module 3 could not be reached, timed out, or returned 5xx."""


class FaceImageRejected(FaceServiceError):
    """Module 3 understood the request and refused the image.

    Carries module 3's own ``detail`` string — "No face detected", "Quality
    score too low" — which is safe and useful to show the end user.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class FaceEnrollResult:
    enrollment_successful: bool
    face_template_hash: str
    quality_score: float
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FaceVerifyResult:
    match_passed: bool
    match_score: float
    match_threshold: float
    face_detected: bool
    current_template_hash: str


@dataclass(frozen=True)
class LivenessResult:
    liveness_passed: bool
    liveness_score: float
    liveness_threshold: float
    face_embedding_hash: str
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def challenge_passed(self) -> Optional[bool]:
        """Whether the ACTIVE challenge was completed.

        Distinct from ``liveness_passed``, which also folds in image quality
        and depth.

        ``None`` carries two meanings and the caller must keep them apart.
        For a passive check there was no challenge to complete. For
        ``colour_reflection`` it means module 3 declined to judge -- the
        screen's light never reached the face measurably, the face was too far
        away, the room was too bright. That is "we could not tell", which is
        neither a pass nor an accusation, and ``inconclusive_reason`` below
        carries the wording to show the student.
        """
        value = self.details.get("challenge_passed")
        return value if isinstance(value, bool) else None

    @property
    def inconclusive_reason(self) -> Optional[str]:
        """Module 3's own explanation when it reached no verdict.

        Written for the student to read, so it is safe to pass through: it
        talks about brightness and distance, never about what was suspected.
        """
        if self.challenge_passed is not None:
            return None
        value = self.details.get("colour_reason")
        return value if isinstance(value, str) and value.strip() else None

    @property
    def frames_with_face(self) -> Optional[int]:
        value = self.details.get("frames_with_face")
        return value if isinstance(value, int) else None


@dataclass(frozen=True)
class RiskAssessResult:
    risk_score: float
    risk_level: str
    pass_threshold: bool
    risk_threshold: float
    signal_breakdown: Dict[str, float] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)


def normalise_image(image: str) -> str:
    """Strip a data-URL prefix and verify the payload is really base64.

    The API spec asks for bare base64, but browser code that reaches for
    ``canvas.toDataURL()`` sends ``data:image/png;base64,...`` by default.
    Accepting both here costs one regex and saves a confusing 400 from a
    service two hops away.
    """
    if not image or not image.strip():
        raise FaceImageRejected("Image payload is empty")
    candidate = _DATA_URL_PREFIX.sub("", image.strip())
    try:
        base64.b64decode(candidate, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise FaceImageRejected(f"Image is not valid base64: {exc}") from exc
    return candidate


class FaceClient:
    """Synchronous client. The backend's endpoints are sync, so an async client
    here would mean juggling event loops for no benefit."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[httpx.Timeout] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.base_url = (base_url or FACE_SERVICE_URL).rstrip("/")
        self.timeout = timeout or DEFAULT_TIMEOUT
        # Injectable for tests: a MockTransport lets the whole client be
        # exercised without a running module 3.
        self._transport = transport

    # -- public API ---------------------------------------------------------

    def health(self) -> bool:
        """True if module 3 answers. Never raises — callers use this to decide
        whether to degrade, not to fail."""
        try:
            with self._open() as client:
                return client.get("/health").status_code == 200
        except Exception:
            return False

    def enroll(self, user_id: str, image: str, camera_consent: bool = True) -> FaceEnrollResult:
        payload = {
            "user_id": user_id,
            "image": normalise_image(image),
            "camera_consent": camera_consent,
        }
        body = self._post("/face/enroll", payload, expect=(200, 201))
        return FaceEnrollResult(
            enrollment_successful=bool(body.get("enrollment_successful")),
            face_template_hash=str(body.get("face_template_hash", "")),
            quality_score=float(body.get("quality_score", 0.0)),
            details=body.get("details") or {},
        )

    def verify(self, image: str, reference_template_hash: str) -> FaceVerifyResult:
        payload = {
            "image": normalise_image(image),
            "reference_template_hash": reference_template_hash,
        }
        body = self._post("/face/verify", payload)
        return FaceVerifyResult(
            match_passed=bool(body.get("match_passed")),
            match_score=float(body.get("match_score", 0.0)),
            match_threshold=float(body.get("match_threshold", 0.70)),
            face_detected=bool(body.get("face_detected")),
            current_template_hash=str(body.get("current_template_hash", "")),
        )

    def check_liveness(self, image: str, challenge_type: str = "passive") -> LivenessResult:
        """Single-frame check. Fine for `passive`; blink and head turn need
        :meth:`check_liveness_frames`, since one frame cannot show a change."""
        return self._liveness({
            "challenge_response": normalise_image(image),
            "challenge_type": challenge_type,
        })

    def check_liveness_frames(
        self, frames: Sequence[str], challenge_type: str,
        colour_sequence: Optional[Sequence[str]] = None,
    ) -> LivenessResult:
        """Multi-frame check for an active challenge.

        Module 3 expects ``challenge_frames`` ordered first-to-last and caps
        the list at MAX_CHALLENGE_FRAMES; it reads the blink sequence and the
        yaw sweep from that ordering, so the order is part of the contract.

        ``colour_sequence`` is required for ``colour_reflection`` and is one
        hex colour PER FRAME, in the same order -- not the list of distinct
        colours shown. Module 3 is stateless and has no idea what the screen
        was asked to do, so this is the only thing that makes the submission
        checkable; it comes from the stored challenge, never from the client.
        """
        if not frames:
            raise FaceImageRejected("No challenge frames supplied")
        if len(frames) > MAX_CHALLENGE_FRAMES:
            raise FaceImageRejected(
                f"At most {MAX_CHALLENGE_FRAMES} challenge frames are allowed")
        payload: Dict[str, Any] = {
            "challenge_frames": [normalise_image(f) for f in frames],
            "challenge_type": challenge_type,
        }
        if colour_sequence is not None:
            if len(colour_sequence) != len(frames):
                raise FaceImageRejected(
                    f"colour_reflection needs one colour per frame: "
                    f"{len(colour_sequence)} colours for {len(frames)} frames")
            payload["colour_sequence"] = list(colour_sequence)
        return self._liveness(payload)

    def _liveness(self, payload: Dict[str, Any]) -> LivenessResult:
        body = self._post("/liveness/check", payload)
        return LivenessResult(
            liveness_passed=bool(body.get("liveness_passed")),
            liveness_score=float(body.get("liveness_score", 0.0)),
            liveness_threshold=float(body.get("liveness_threshold", 0.60)),
            face_embedding_hash=str(body.get("face_embedding_hash", "")),
            details=body.get("details") or {},
        )

    def assess_risk(
        self,
        liveness_score: Optional[float] = None,
        face_match_score: Optional[float] = None,
        device_signature: Optional[str] = None,
        device_public_key: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        geolocation: Optional[Dict[str, float]] = None,
    ) -> RiskAssessResult:
        """Module 3's own risk fusion.

        The backend does not depend on this for the check-in decision — it has
        its own RiskService, which must keep working when module 3 is down, and
        which knows about enrolment and session policy that module 3 cannot
        see. This is exposed for comparison and for the observability module.
        """
        payload = {
            "liveness_score": liveness_score,
            "face_match_score": face_match_score,
            "device_signature": device_signature,
            "device_public_key": device_public_key,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "geolocation": geolocation,
        }
        body = self._post("/risk/assess", {k: v for k, v in payload.items() if v is not None})
        return RiskAssessResult(
            risk_score=float(body.get("risk_score", 0.0)),
            risk_level=str(body.get("risk_level", "LOW")),
            pass_threshold=bool(body.get("pass_threshold")),
            risk_threshold=float(body.get("risk_threshold", 0.50)),
            signal_breakdown=body.get("signal_breakdown") or {},
            recommendations=body.get("recommendations") or [],
        )

    # -- plumbing -----------------------------------------------------------

    def _open(self) -> httpx.Client:
        kwargs: Dict[str, Any] = {"base_url": self.base_url, "timeout": self.timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def _post(self, path: str, payload: Dict[str, Any],
              expect: tuple = (200,)) -> Dict[str, Any]:
        last_error: Optional[Exception] = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with self._open() as client:
                    response = client.post(path, json=payload)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                    httpx.PoolTimeout, httpx.RemoteProtocolError) as exc:
                last_error = exc
                # Never log `payload`: it holds a face image.
                logger.warning("face service %s attempt %d/%d failed: %s",
                               path, attempt, MAX_ATTEMPTS, type(exc).__name__)
                continue

            if response.status_code in expect:
                try:
                    return response.json()
                except ValueError as exc:
                    raise FaceServiceUnavailable(
                        f"face service returned non-JSON from {path}") from exc

            if response.status_code == 400:
                raise FaceImageRejected(self._detail(response))

            if response.status_code >= 500:
                last_error = FaceServiceUnavailable(
                    f"face service returned {response.status_code} from {path}")
                logger.warning("face service %s returned %d, attempt %d/%d",
                               path, response.status_code, attempt, MAX_ATTEMPTS)
                continue

            # 401/404/422 and friends: a contract mismatch, not a transient
            # fault. Retrying cannot help and would hide the bug.
            raise FaceServiceUnavailable(
                f"face service returned unexpected {response.status_code} from {path}: "
                f"{self._detail(response)}")

        raise FaceServiceUnavailable(
            f"face service unreachable at {self.base_url}{path}") from last_error

    @staticmethod
    def _detail(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text[:200]
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, str):
            return detail
        return str(detail or body)[:200]
