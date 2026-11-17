"""
SAIV Face Recognition & Risk Service - Module 3

This is the skeleton implementation for the Face Recognition module.
Students must implement face enrollment, verification, liveness detection,
and risk scoring.

Privacy Requirements:
- NO raw face images should be stored
- Process images in-memory only
- Store only SHA-256 hashes of face embeddings

Recommended Libraries:
- MediaPipe: Face detection and 468-landmark face mesh
- OpenCV: Image processing
- Pillow: Image loading from base64
- NumPy: Numerical operations
"""

import base64
import gc
import hashlib
import ipaddress
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple

import cv2
import mediapipe as mp
import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel
from starlette.responses import Response

try:  # package import when run as `app.main`, plain import under the benchmark
    from . import colour_liveness
except ImportError:  # pragma: no cover
    import colour_liveness

logger = logging.getLogger("saiv.face_recognition")

# Thresholds
FACE_DETECTION_MIN_CONFIDENCE = 0.5
ENROLL_MIN_DETECTION_CONFIDENCE = 0.7
ENROLL_MIN_QUALITY_SCORE = 0.5
FACE_MATCH_THRESHOLD = 0.70
LIVENESS_THRESHOLD = 0.60

# Challenge types that ask the subject (or their screen) to DO something, and
# so must be positively satisfied rather than merely not contradicted.
ACTIVE_CHALLENGE_TYPES = frozenset({"blink", "head_turn", "colour_reflection"})
RISK_THRESHOLD = 0.50

# Signal weights for risk fusion 
WEIGHT_LIVENESS = 0.25
WEIGHT_FACE_MATCH = 0.25
WEIGHT_DEVICE = 0.20
WEIGHT_NETWORK = 0.15
WEIGHT_GEOLOCATION = 0.15

# -----------------------------------------------------------------------------
# Face matching: pretrained ArcFace embedding + SimHash (locality-sensitive hash)
#
#   photo -> MediaPipe landmarks -> 5-point alignment (112x112)
#         -> ArcFace MobileFaceNet (512-d, L2-normalised)       [in memory only]
#         -> 256 random hyperplanes, keep only the sign bits    [this is all we store]
#         -> 64 lowercase hex characters
#
# Hamming distance between two hashes estimates the angle between the original
# embeddings, so cos(pi * hamming / bits) estimates their cosine similarity
# without the embedding ever being stored (design: "Approach 3: SimHash").
# -----------------------------------------------------------------------------
FACE_EMBED_DIM = 512
FACE_EMBED_INPUT_SIZE = 112
FACE_HASH_BITS = 256  # 256 bits == 64 hex characters (API contract)
# Hyperplanes are fixed per deployment. Set FACE_HASH_SEED to a private value in
# production; changing it invalidates every enrolled hash (users must re-enroll).
FACE_HASH_SEED = int(os.getenv("FACE_HASH_SEED", "20240"))
_FACE_HASH_PROJECTION = np.random.RandomState(FACE_HASH_SEED).standard_normal(
    (FACE_HASH_BITS, FACE_EMBED_DIM)
).astype(np.float32)

# Estimated cosine similarity at which the public score equals FACE_MATCH_THRESHOLD.
# ArcFace same-person cosines are typically 0.45-0.80 and different people sit near
# 0.0-0.28 (see benchmark/), so 0.36 is the midpoint of that gap. 0.36 <=> a Hamming
# distance of at most 98 of 256 bits. Re-calibrate on your own users' photos.
FACE_COSINE_THRESHOLD = float(os.getenv("FACE_COSINE_THRESHOLD", "0.36"))

EMBED_MODEL_PATH = Path(
    os.getenv(
        "FACE_EMBED_MODEL_PATH",
        Path(__file__).resolve().parent.parent / "models" / "w600k_mbf.onnx",
    )
)

# Standard ArcFace 5-point template for a 112x112 crop:
# image-left eye, image-right eye, nose tip, image-left mouth corner, image-right mouth corner.
_ARCFACE_TEMPLATE = np.float32([
    [38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
    [41.5493, 92.3655], [70.7299, 92.2041],
])

# Set by the startup load below when the model file is present but will not
# load -- truncated download, wrong format, unreadable. Without this, a corrupt
# file reports as "available" forever while every embedding request 503s.
_embed_model_unloadable = False


def embed_model_status() -> str:
    """Readiness of the ArcFace embedding model, without loading it.

    "loaded"     - a session is in memory and embedding works now.
    "available"  - the file is present but has not been loaded yet.
    "unloadable" - the file is present and failed to load.
    "missing"    - the file is absent; every embedding endpoint will 503.
    """
    if _get_embedder.cache_info().currsize:
        return "loaded"
    if not EMBED_MODEL_PATH.is_file():
        return "missing"
    return "unloadable" if _embed_model_unloadable else "available"


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Load the embedding model before serving instead of on first use.

    Without this, a model that was never provisioned stays invisible until a
    student tries to enrol, which may be hours later and looks to them like a
    server fault. Loading here puts it in the startup log and on /health at
    once, and spares the first real request the cold-load latency.

    A failure is logged, not raised: liveness detection and risk scoring do
    not use this model, so a container that can still answer those should keep
    serving rather than restart-loop.
    """
    try:
        _get_embedder()
        logger.info("Face embedding model loaded from %s", EMBED_MODEL_PATH)
    except Exception as exc:  # noqa: BLE001 - startup must not fail on this
        global _embed_model_unloadable
        _embed_model_unloadable = EMBED_MODEL_PATH.is_file()
        logger.error(
            "Face embedding model unavailable (%s: %s). /face/enroll, /face/verify "
            "and /face/match will answer 503 until a model is provisioned at %s",
            type(exc).__name__, exc, EMBED_MODEL_PATH,
        )
    yield


app = FastAPI(
    title="SAIV Face Recognition Service",
    description="Face enrollment, verification, liveness detection, and risk scoring service",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# =============================================================================
# METRICS
# =============================================================================
# Prometheus scrapes face-recognition:8001/metrics every 15s
# (module4-observability/prometheus.yml). The two metric names match the
# backend's on purpose: Prometheus attaches a `job` label when it scrapes, so
# one dashboard query can cover both services. `route` is the route template
# and anything unmatched collapses into a single bucket, so a scanner probing
# random URLs cannot mint unbounded time series. The full reasoning is written
# out in module2-backend/app/metrics.py.
#
# /metrics stays off any API prefix for the same reason /health does: it is an
# infrastructure probe, not a service resource, so it is also left out of the
# endpoint list returned by GET / that the public tests read.

REQUESTS = Counter(
    "saiv_http_requests_total",
    "HTTP requests handled, by route template and response status.",
    ["method", "route", "status"],
)

LATENCY = Histogram(
    "saiv_http_request_duration_seconds",
    "Wall-clock time to produce a response, by route template.",
    ["method", "route"],
    # Wider than the backend's top bucket: enrolment runs face detection, mesh
    # alignment and a 512-d ArcFace forward pass on CPU, so a slow request here
    # is seconds rather than milliseconds.
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)


def _observe_request(request: Request, status: int, elapsed: float) -> None:
    # scope["route"] is set by the router once a path matches, so it can only
    # be read after the request has been handled; a 404 leaves it absent.
    route = getattr(request.scope.get("route"), "path", None) or "unmatched"
    REQUESTS.labels(method=request.method, route=route, status=str(status)).inc()
    LATENCY.labels(method=request.method, route=route).observe(elapsed)


@app.middleware("http")
async def record_request(request: Request, call_next):
    if request.url.path == "/metrics":
        # Prometheus scraping us is not application traffic.
        return await call_next(request)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # An unhandled exception becomes a 500 in Starlette's
        # ServerErrorMiddleware, which sits OUTSIDE this middleware, so it
        # arrives here as an exception rather than as a response. Without this
        # branch the requests that matter most would never be counted.
        _observe_request(request, 500, time.perf_counter() - started)
        raise
    _observe_request(request, response.status_code, time.perf_counter() - started)
    return response


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    # Content-Type as an explicit header, not media_type: Starlette appends a
    # second "; charset=utf-8" to any text/* media type and CONTENT_TYPE_LATEST
    # already carries one.
    return Response(generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class FaceEnrollRequest(BaseModel):
    """Request model for face enrollment."""
    user_id: str
    image: str  # Base64 encoded image
    camera_consent: bool = False


class FaceEnrollResponse(BaseModel):
    """Response model for face enrollment."""
    enrollment_successful: bool
    face_template_hash: str  # 64-char SHA-256 hex string
    quality_score: float  # 0.0 to 1.0
    details: Dict[str, Any]


class FaceVerifyRequest(BaseModel):
    """Request model for face verification."""
    image: str  # Base64 encoded image
    reference_template_hash: str  # Hash from enrollment


class FaceVerifyResponse(BaseModel):
    """Response model for face verification."""
    match_passed: bool
    match_score: float  # 0.0 to 1.0
    match_threshold: float  # Default: 0.70
    face_detected: bool
    current_template_hash: str


class LivenessRequest(BaseModel):
    """Liveness request; challenge_frames are ordered from first to last."""
    challenge_response: Optional[str] = None  # Backwards-compatible single frame
    challenge_frames: Optional[List[str]] = None
    challenge_type: str = "blink"  # blink, head_turn, colour_reflection, passive
    # For colour_reflection: the hex colour the screen was showing when each
    # frame was taken, ONE PER FRAME and in the same order. Not a list of the
    # distinct colours used -- the pairing of frame to colour is the whole
    # evidence, so a caller that cannot state it is refused rather than
    # guessed at. The caller is also responsible for the sequence spanning a
    # wide enough luminance ratio; see app/colour_liveness.py.
    colour_sequence: Optional[List[str]] = None


class LivenessResponse(BaseModel):
    """Response model for liveness check."""
    liveness_passed: bool
    liveness_score: float  # 0.0 to 1.0
    liveness_threshold: float  # Default: 0.60
    face_embedding_hash: str
    details: Dict[str, Any]


class GeolocationData(BaseModel):
    """Geolocation data for risk assessment."""
    latitude: float
    longitude: float
    accuracy: float


class RiskAssessRequest(BaseModel):
    """Request model for risk assessment."""
    liveness_score: Optional[float] = None
    face_match_score: Optional[float] = None
    device_signature: Optional[str] = None
    device_public_key: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    geolocation: Optional[GeolocationData] = None


class RiskAssessResponse(BaseModel):
    """Response model for risk assessment."""
    risk_score: float  # 0.0 to 1.0
    risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL
    pass_threshold: bool
    risk_threshold: float  # Default: 0.50
    signal_breakdown: Dict[str, float]
    recommendations: List[str]


# =============================================================================
# HEALTH & ROOT ENDPOINTS
# =============================================================================

@app.get("/health")
async def health_check():
    """Basic health check endpoint.

    Includes `service` so a caller polling several modules can tell which one
    answered; the public test suite asserts it is present.

    `status` reports this process, and stays "healthy" with a 200 whenever it
    is serving. It deliberately does not fail on a missing embedding model:
    docker-compose gates the stack on this probe, so a 503 would restart a
    container that is working and take down liveness detection and risk
    scoring, neither of which needs the model. The model is reported as its
    own field instead, so an absent one is visible on the first poll rather
    than on the first enrolment. Its path is kept out of the response and
    written to the startup log: this endpoint is unauthenticated.
    """
    return {
        "status": "healthy",
        "service": "saiv-face-recognition",
        "version": "1.0.0",
        "face_embedding_model": embed_model_status(),
    }


@app.get("/")
async def root():
    """List available endpoints."""
    return {
        "service": "SAIV Face Recognition & Risk Service",
        "version": "1.0.0",
        # Bare paths first: callers (and the public test suite) look for an
        # exact element such as "/face/enroll"; the descriptive strings below
        # are for humans reading the root document.
        "paths": [
            "/health",
            "/face/enroll",
            "/face/verify",
            "/face/match",
            "/liveness/check",
            "/risk/assess",
        ],
        "endpoints": [
            "/health",
            "/face/enroll",
            "/face/verify",
            "/face/match",
            "/liveness/check",
            "/risk/assess",
            "GET /health - Health check",
            "POST /face/enroll - Enroll a face for verification",
            "POST /face/verify - Verify a face against enrolled template",
            "POST /face/match - Legacy face matching (use /face/verify)",
            "POST /liveness/check - Perform liveness detection",
            "POST /risk/assess - Multi-signal risk assessment"
        ]
    }


# =============================================================================
# FACE ENROLLMENT ENDPOINT (REQUIRED - 4 points in public tests)
# =============================================================================

@app.post("/face/enroll", response_model=FaceEnrollResponse, status_code=201)
async def enroll_face(request: FaceEnrollRequest):
    """
    Enroll a user's face for future verification.

    TODO: Implement the following:
    1. Validate camera_consent is True (return 400 if False)
    2. Decode base64 image to numpy array
    3. Detect face using MediaPipe FaceDetection
    4. If no face detected, return 400 with "No face detected"
    5. Extract face features/embedding
    6. Generate SHA-256 hash of embedding (64 hex chars)
    7. Calculate quality score based on:
       - Face detection confidence
       - Image resolution
       - Face size relative to image
    8. Return enrollment response

    Success Criteria:
    - Face detected with confidence >= 0.7
    - Quality score >= 0.5
    - Returns 64-char SHA-256 hex hash
    """
    if not request.camera_consent:
        raise HTTPException(status_code=400, detail="Camera consent is required for face enrollment")

    image_array = decode_base64_image(request.image)

    try:
        detection = detect_face(image_array)

        if not detection["detected"]:
            raise HTTPException(status_code=400, detail="No face detected")

        if detection["confidence"] < ENROLL_MIN_DETECTION_CONFIDENCE:
            raise HTTPException(
                status_code=400,
                detail=f"Face detection confidence too low ({detection['confidence']:.2f} < {ENROLL_MIN_DETECTION_CONFIDENCE})",
            )

        embedding = extract_face_embedding(image_array, detection)
        face_hash = generate_face_hash(embedding)
        quality_score = calculate_quality_score(image_array, detection)

        if quality_score < ENROLL_MIN_QUALITY_SCORE:
            raise HTTPException(
                status_code=400,
                detail=f"Image quality too low ({quality_score:.2f} < {ENROLL_MIN_QUALITY_SCORE})",
            )

        return FaceEnrollResponse(
            enrollment_successful=True,
            face_template_hash=face_hash,
            quality_score=quality_score,
            details={
                "detection_confidence": detection["confidence"],
                "image_width": int(image_array.shape[1]),
                "image_height": int(image_array.shape[0]),
            },
        )
    finally:
        # Privacy: ensure raw image data is not retained beyond this request
        del image_array
        gc.collect()


# =============================================================================
# FACE VERIFICATION ENDPOINT (REQUIRED - 4 points in public tests)
# =============================================================================

@app.post("/face/verify", response_model=FaceVerifyResponse)
async def verify_face(request: FaceVerifyRequest):
    """
    Verify a face against an enrolled template.

    TODO: Implement the following:
    1. Decode base64 image to numpy array
    2. Detect face using MediaPipe FaceDetection
    3. If no face detected, return with face_detected=False
    4. Extract face features/embedding
    5. Generate SHA-256 hash of current face
    6. Compare hashes or embeddings (choose your approach)
    7. Calculate match_score (0.0 to 1.0)
    8. match_passed = (match_score >= 0.70)

    Note: Hash comparison alone gives binary match. For continuous
    scores, consider perceptual hashing or embedding similarity.
    """
    image_array = decode_base64_image(request.image)

    try:
        detection = detect_face(image_array)

        if not detection["detected"]:
            return FaceVerifyResponse(
                match_passed=False,
                match_score=0.0,
                match_threshold=FACE_MATCH_THRESHOLD,
                face_detected=False,
                current_template_hash="",
            )

        embedding = extract_face_embedding(image_array, detection)
        current_hash = generate_face_hash(embedding)

        match_score = compare_face_hashes(current_hash, request.reference_template_hash)
        match_passed = match_score >= FACE_MATCH_THRESHOLD

        return FaceVerifyResponse(
            match_passed=match_passed,
            match_score=match_score,
            match_threshold=FACE_MATCH_THRESHOLD,
            face_detected=True,
            current_template_hash=current_hash,
        )
    finally:
        del image_array
        gc.collect()


@app.post("/face/match")
async def match_face(request: FaceVerifyRequest):
    """
    Legacy face matching endpoint. Redirects to /face/verify.
    Kept for backwards compatibility.
    """
    return await verify_face(request)


# =============================================================================
# LIVENESS DETECTION ENDPOINT (REQUIRED - partial; BONUS for advanced)
# =============================================================================

@app.post("/liveness/check", response_model=LivenessResponse)
async def check_liveness(request: LivenessRequest):
    """Verify passive signals and ordered blink/head-turn challenge frames.

    Active challenge success is independent of image quality. Images are only
    processed in memory and are never persisted.
    """
    challenge_type = (request.challenge_type or "").strip().lower()
    if challenge_type not in {"passive", "blink", "head_turn", "colour_reflection"}:
        raise HTTPException(
            status_code=400,
            detail="challenge_type must be passive, blink, head_turn, or colour_reflection")

    encoded_frames = request.challenge_frames or []
    if not encoded_frames and request.challenge_response:
        encoded_frames = [request.challenge_response]
    if not encoded_frames:
        raise HTTPException(status_code=400, detail="Provide challenge_response or challenge_frames")
    if len(encoded_frames) > 30:
        raise HTTPException(status_code=400, detail="A maximum of 30 challenge frames is allowed")

    images: List[np.ndarray] = []
    try:
        images = [decode_base64_image(encoded) for encoded in encoded_frames]
        frames: List[Dict[str, Any]] = []

        wants_region = challenge_type == "colour_reflection"
        for image_array in images:
            detection = detect_face(image_array)
            if not detection["detected"]:
                frames.append({"detected": False, "confidence": 0.0, "quality": 0.0,
                               "ear": None, "yaw": None, "mesh": None, "region": None})
                continue

            mesh = analyze_face_mesh(image_array)
            ear = yaw = None
            if mesh is not None and mesh.get("landmarks") is not None:
                landmarks = mesh["landmarks"]
                frame_height, frame_width = image_array.shape[:2]
                _, ear = detect_blink(landmarks, (frame_width, frame_height))
                points = landmarks.landmark
                left_x, right_x, nose_x = float(points[33].x), float(points[263].x), float(points[1].x)
                eye_span = abs(right_x - left_x)
                if eye_span > 1e-6:
                    yaw = (nose_x - (left_x + right_x) / 2.0) / eye_span

            frames.append({
                "detected": True, "confidence": detection["confidence"],
                "quality": calculate_quality_score(image_array, detection),
                "ear": ear, "yaw": yaw, "mesh": mesh,
                # Only cropped when the colour challenge will read it: this is
                # a copy of pixels from a biometric frame and should not be
                # held for a challenge that has no use for it.
                "region": _crop_face(image_array, detection["bounding_box"]) if wants_region else None,
            })

        valid = [frame for frame in frames if frame["detected"]]
        if not valid:
            return LivenessResponse(
                liveness_passed=False, liveness_score=0.0,
                liveness_threshold=LIVENESS_THRESHOLD, face_embedding_hash="",
                details={"reason": "No face detected in any frame",
                         "challenge_type": challenge_type, "frames_received": len(images),
                         "frames_with_face": 0, "challenge_passed": False},
            )

        first_index = next(i for i, frame in enumerate(frames) if frame["detected"])
        detection = detect_face(images[first_index])
        face_hash = generate_face_hash(extract_face_embedding(images[first_index], detection))

        confidence = float(np.mean([frame["confidence"] for frame in valid]))
        quality = float(np.mean([frame["quality"] for frame in valid]))
        base_score = 0.6 * confidence + 0.4 * quality

        challenge_passed: Optional[bool] = None
        challenge_details: Dict[str, Any] = {}
        colour_result: Optional["colour_liveness.ColourReflectionResult"] = None
        if challenge_type == "blink":
            ears = [frame["ear"] for frame in frames]
            blink = False
            # Require open -> closed -> open; one closed-eye frame is not a blink.
            for i in range(1, len(ears) - 1):
                if ears[i] is not None and ears[i] < _EAR_BLINK_THRESHOLD:
                    opened_before = any(v is not None and v >= _EAR_BLINK_THRESHOLD + 0.02 for v in ears[:i])
                    opened_after = any(v is not None and v >= _EAR_BLINK_THRESHOLD + 0.02 for v in ears[i + 1:])
                    if opened_before and opened_after:
                        blink = True
                        break
            challenge_passed = blink
            challenge_details = {
                "blink_detected": blink,
                "eye_aspect_ratios": [round(float(v), 4) if v is not None else None for v in ears],
                "required_sequence": "open -> closed -> open",
            }
        elif challenge_type == "head_turn":
            yaws = [frame["yaw"] for frame in frames if frame["yaw"] is not None]
            delta = max(yaws) - min(yaws) if len(yaws) >= 2 else 0.0
            challenge_passed = len(yaws) >= 2 and delta >= 0.12
            challenge_details = {
                "head_turn_detected": challenge_passed,
                "yaw_proxy_values": [round(float(f["yaw"]), 4) if f["yaw"] is not None else None for f in frames],
                "yaw_proxy_delta": round(float(delta), 4),
                "minimum_yaw_proxy_delta": 0.12,
            }

        elif challenge_type == "colour_reflection":
            expected = request.colour_sequence or []
            if len(expected) != len(frames):
                raise HTTPException(
                    status_code=400,
                    detail=f"colour_reflection needs one colour per frame: got "
                           f"{len(expected)} colours for {len(frames)} frames")
            try:
                colour_result = colour_liveness.analyse(
                    [frame["region"] for frame in frames], expected)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            challenge_passed = colour_result.passed
            challenge_details = colour_result.as_details()

        depth_values = []
        for frame in valid:
            mesh = frame["mesh"]
            if mesh is not None and mesh["face_mesh_complete"]:
                depth_values.append({"good": 1.0, "moderate": 0.6, "poor": 0.0}.get(mesh["depth_quality"], 0.0))
        depth_score = float(np.mean(depth_values)) if depth_values else 0.0
        score = 0.5 * base_score + 0.5 * depth_score if depth_values else base_score
        # Where the colour challenge reached a verdict it carries half the
        # score. Detection confidence, image quality and mesh depth are all
        # proxies for "is this a real face"; the colour response is direct
        # evidence about the surface actually in front of the camera, so a
        # textbook response should outscore a barely-adequate one.
        if colour_result is not None and colour_result.passed is not None:
            score = 0.5 * score + 0.5 * colour_result.score
        score = float(_clamp01(score))
        score_passed = score >= LIVENESS_THRESHOLD
        # An ACTIVE challenge must be positively satisfied. `is not False`
        # would let an undecided colour verdict -- too dim to measure, face
        # too far away -- pass on the quality score alone, which is exactly
        # what a tablet held at arm's length produces. Only `passive`, which
        # asks for nothing and so can never be satisfied, keeps the laxer
        # rule.
        passed = score_passed and (
            challenge_passed is True if challenge_type in ACTIVE_CHALLENGE_TYPES
            else challenge_passed is not False)

        details: Dict[str, Any] = {
            "challenge_type": challenge_type, "frames_received": len(frames),
            "frames_with_face": len(valid), "detection_confidence": round(confidence, 4),
            "quality_score": round(quality, 4), "depth_score": round(depth_score, 4),
            "score_threshold_passed": score_passed, "challenge_passed": challenge_passed,
            **challenge_details,
        }
        if len(valid) != len(frames):
            details["frames_without_face"] = len(frames) - len(valid)

        return LivenessResponse(
            liveness_passed=passed, liveness_score=score,
            liveness_threshold=LIVENESS_THRESHOLD, face_embedding_hash=face_hash,
            details=details,
        )
    finally:
        images.clear()
        gc.collect()


# =============================================================================
# RISK ASSESSMENT ENDPOINT (REQUIRED - 3 points in public tests)
# =============================================================================

@app.post("/risk/assess", response_model=RiskAssessResponse)
async def assess_risk(request: RiskAssessRequest):
    """
    Perform multi-signal risk assessment.

    TODO: Implement the following:
    1. Collect all available signals from request
    2. Calculate individual signal scores (0.0 = safe, 1.0 = risky)
    3. Apply weighted fusion:
       - Liveness: 25%
       - Face match: 25%
       - Device attestation: 20%
       - Network/VPN: 15%
       - Geolocation: 15%
    4. Calculate combined risk_score
    5. Determine risk_level:
       - LOW: risk_score < 0.3
       - MEDIUM: 0.3 <= risk_score < 0.5
       - HIGH: 0.5 <= risk_score < 0.7
       - CRITICAL: risk_score >= 0.7
    6. pass_threshold = (risk_score < 0.50)
    7. Generate recommendations for low-scoring signals

    Signal Analysis:
    - Liveness: Invert score (low liveness = high risk)
    - Face match: Invert score (low match = high risk)
    - Device: Check signature validity, public key format
    - Network: Detect VPN/proxy (private IPs, Tor exit nodes)
    - Geolocation: Check accuracy, validate coordinates

    VPN/Proxy Detection Hints:
    - Private IP ranges: 10.x.x.x, 172.16-31.x.x, 192.168.x.x
    - Check user_agent for VPN indicators
    - High geolocation accuracy (< 10m) might be spoofed
    - Very low accuracy (> 5000m) indicates issues
    """
    signal_breakdown: Dict[str, float] = {}
    recommendations: List[str] = []
    weighted_sum = 0.0
    weight_used = 0.0

    # Liveness (low liveness -> high risk)
    if request.liveness_score is not None:
        liveness_risk = 1.0 - _clamp01(request.liveness_score)
        signal_breakdown["liveness"] = liveness_risk
        weighted_sum += WEIGHT_LIVENESS * liveness_risk
        weight_used += WEIGHT_LIVENESS
        if liveness_risk > 0.4:
            recommendations.append("Liveness score is low; request a fresh liveness challenge.")

    # Face match (low match -> high risk)
    if request.face_match_score is not None:
        face_risk = 1.0 - _clamp01(request.face_match_score)
        signal_breakdown["face_match"] = face_risk
        weighted_sum += WEIGHT_FACE_MATCH * face_risk
        weight_used += WEIGHT_FACE_MATCH
        if face_risk > 0.4:
            recommendations.append("Face match score is low; re-verify identity.")

    if request.device_signature is not None or request.device_public_key is not None:
        device_risk = assess_device_risk(request.device_signature, request.device_public_key)
        signal_breakdown["device"] = device_risk
        weighted_sum += WEIGHT_DEVICE * device_risk
        weight_used += WEIGHT_DEVICE
        if device_risk > 0.4:
            recommendations.append("Device attestation is weak or missing; consider requiring device re-registration.")

    if request.ip_address is not None or request.user_agent is not None:
        is_vpn, vpn_confidence = detect_vpn_proxy(request.ip_address or "", request.user_agent or "")
        network_risk = vpn_confidence if is_vpn else 0.0
        signal_breakdown["network"] = network_risk
        weighted_sum += WEIGHT_NETWORK * network_risk
        weight_used += WEIGHT_NETWORK
        if is_vpn:
            recommendations.append("VPN or proxy usage detected; flag for manual review.")

    # Geolocation
    if request.geolocation is not None:
        geo_risk = assess_geolocation_risk(request.geolocation)
        signal_breakdown["geolocation"] = geo_risk
        weighted_sum += WEIGHT_GEOLOCATION * geo_risk
        weight_used += WEIGHT_GEOLOCATION
        if geo_risk > 0.4:
            recommendations.append("Geolocation signal is suspicious (low accuracy or invalid coordinates).")

    # Max risk if no signal is provided
    if weight_used > 0:
        risk_score = weighted_sum / weight_used
    else:
        risk_score = 1.0
        recommendations.append("No risk signals were provided; unable to verify identity or context.")

    risk_score = float(max(0.0, min(1.0, risk_score)))

    if risk_score < 0.3:
        risk_level = "LOW"
    elif risk_score < 0.5:
        risk_level = "MEDIUM"
    elif risk_score < 0.7:
        risk_level = "HIGH"
    else:
        risk_level = "CRITICAL"

    if not recommendations:
        recommendations.append("No elevated risk signals detected.")

    return RiskAssessResponse(
        risk_score=risk_score,
        risk_level=risk_level,
        pass_threshold=risk_score < RISK_THRESHOLD,
        risk_threshold=RISK_THRESHOLD,
        signal_breakdown=signal_breakdown,
        recommendations=recommendations,
    )


def _clamp01(value: float) -> float:
    """Clamp a value into the [0.0, 1.0] range."""
    return max(0.0, min(1.0, value))


def assess_device_risk(device_signature: Optional[str], device_public_key: Optional[str]) -> float:
    """
    Assess device-attestation risk from a device signature/public key.

    This is a lightweight structural check (not cryptographic verification):
    - Missing signature or key -> high risk
    - Implausibly short/malformed values -> elevated risk
    - Present and well-formed -> low risk
    """
    if not device_signature or not device_public_key:
        return 0.9

    risk = 0.1
    if len(device_signature) < 16:
        risk += 0.4
    if len(device_public_key) < 32:
        risk += 0.4

    return _clamp01(risk)


def assess_geolocation_risk(geo: "GeolocationData") -> float:
    """
    Assess geolocation risk based on coordinate validity and GPS accuracy.

    - Invalid coordinates -> maximum risk
    - Very low accuracy (large error radius, > 5000m) -> high risk (unreliable)
    - Implausibly high accuracy (< 1m) -> elevated risk (possible spoofing)
    - Normal accuracy range -> low risk
    """
    if not (-90.0 <= geo.latitude <= 90.0) or not (-180.0 <= geo.longitude <= 180.0):
        return 1.0

    if geo.accuracy > 5000:
        return 0.8
    if geo.accuracy < 1:
        return 0.5
    if geo.accuracy > 1000:
        return 0.5

    return 0.1


# =============================================================================
# HELPER FUNCTIONS (Implement these to support your endpoints)
# =============================================================================

def decode_base64_image(base64_string: str) -> np.ndarray:
    """
    Decode a base64 encoded image to a numpy array (RGB).

    Raises HTTPException(400) on invalid base64 / corrupt image data,
    since this is called directly from request handlers.
    """
    try:
        image_data = base64.b64decode(base64_string, validate=True)
        image = Image.open(BytesIO(image_data))
        image.load()  # force decode now, so corrupt image data fails here
        return np.array(image.convert("RGB"))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image data: {exc}")


# MediaPipe solutions are stateless-safe to re-create per request; for a
# higher-throughput service these could be initialized once at module load
# instead, but per-request instances avoid any cross-request state leaking
# between users, which matters for a biometric endpoint.
_mp_face_detection = mp.solutions.face_detection
_mp_face_mesh = mp.solutions.face_mesh


def detect_face(image_array: np.ndarray) -> Dict[str, Any]:
    """
    Detect the most confident face in an image using MediaPipe FaceDetection.

    Returns a dict:
        {"detected": bool, "confidence": float, "bounding_box": RelativeBoundingBox | None}
    """
    with _mp_face_detection.FaceDetection(
        model_selection=0,
        min_detection_confidence=FACE_DETECTION_MIN_CONFIDENCE,
    ) as face_detection:
        results = face_detection.process(image_array)

        if not results.detections:
            return {"detected": False, "confidence": 0.0, "bounding_box": None, "keypoints": []}

        # Take the most confident detection if there are multiple faces
        best_detection = max(results.detections, key=lambda d: d.score[0])

        return {
            "detected": True,
            "confidence": float(best_detection.score[0]),
            "bounding_box": best_detection.location_data.relative_bounding_box,
            "keypoints": list(best_detection.location_data.relative_keypoints),
        }


def _crop_face(image_array: np.ndarray, bbox) -> np.ndarray:
    """Crop the face region described by a MediaPipe relative bounding box."""
    h, w = image_array.shape[:2]

    x = int(bbox.xmin * w)
    y = int(bbox.ymin * h)
    width = int(bbox.width * w)
    height = int(bbox.height * h)

    # Clamp to valid image bounds (MediaPipe boxes can extend slightly
    # outside the frame for faces near the edge)
    x = max(0, x)
    y = max(0, y)
    width = max(1, min(width, w - x))
    height = max(1, min(height, h - y))

    return image_array[y:y + height, x:x + width]


@lru_cache(maxsize=1)
def _get_embedder() -> Tuple[Any, str]:
    """Load the ArcFace ONNX model once (lazily) and reuse the session."""
    if not EMBED_MODEL_PATH.is_file():
        logger.error("Face embedding model missing at %s", EMBED_MODEL_PATH)
        raise HTTPException(status_code=503, detail="Face embedding model is not available on the server")
    options = ort.SessionOptions()
    options.log_severity_level = 3
    session = ort.InferenceSession(str(EMBED_MODEL_PATH), sess_options=options, providers=["CPUExecutionProvider"])
    return session, session.get_inputs()[0].name


def _order_by_face_axis(points: List[np.ndarray], up: np.ndarray) -> List[np.ndarray]:
    """
    Order two points "left then right" along the face's own horizontal axis.

    `up` points from the mouth towards the eyes, so the order stays correct when the
    frame is rotated (landscape phone) or mirrored (selfie camera), where sorting by
    image x would not be.
    """
    norm = float(np.linalg.norm(up))
    right = np.array([-up[1], up[0]], dtype=np.float32) / (norm if norm > 0 else 1.0)
    return sorted(points, key=lambda p: float(np.dot(p, right)))


def _mesh_alignment_points(image_array: np.ndarray, landmarks) -> np.ndarray:
    """5 alignment points from the MediaPipe mesh (pupils, nose tip, mouth corners)."""
    h, w = image_array.shape[:2]

    def pt(i: int) -> np.ndarray:
        return np.array([landmarks.landmark[i].x * w, landmarks.landmark[i].y * h], dtype=np.float32)

    if len(landmarks.landmark) >= 478:  # refine_landmarks=True adds iris centres 468 / 473
        eyes = [pt(468), pt(473)]
    else:
        eyes = [(pt(33) + pt(133)) / 2.0, (pt(362) + pt(263)) / 2.0]
    mouth = [pt(61), pt(291)]
    up = (eyes[0] + eyes[1]) / 2.0 - (mouth[0] + mouth[1]) / 2.0
    eyes = _order_by_face_axis(eyes, up)
    mouth = _order_by_face_axis(mouth, up)
    return np.float32([eyes[0], eyes[1], pt(1), mouth[0], mouth[1]])


def _detection_alignment_points(image_array: np.ndarray, detection: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
    """Fallback when no face mesh is found: eyes, nose and mouth centre from the detector (4 points)."""
    keypoints = detection.get("keypoints") or []
    if len(keypoints) < 4:
        raise HTTPException(status_code=400, detail="Could not locate facial landmarks for alignment")
    h, w = image_array.shape[:2]
    pts = [np.array([k.x * w, k.y * h], dtype=np.float32) for k in keypoints[:4]]
    up = (pts[0] + pts[1]) / 2.0 - pts[3]
    eyes = _order_by_face_axis(pts[:2], up)
    src = np.float32([eyes[0], eyes[1], pts[2], pts[3]])
    dst = np.float32([_ARCFACE_TEMPLATE[0], _ARCFACE_TEMPLATE[1], _ARCFACE_TEMPLATE[2],
                      (_ARCFACE_TEMPLATE[3] + _ARCFACE_TEMPLATE[4]) / 2.0])
    return src, dst


def extract_face_embedding(image_array: np.ndarray, detection: Dict[str, Any]) -> np.ndarray:
    """
    Extract an L2-normalised 512-d ArcFace embedding for the detected face.

    The face is aligned to the standard 112x112 ArcFace template using MediaPipe
    landmarks, then passed through a pretrained MobileFaceNet. The embedding exists
    only in memory for the duration of the request; callers hash it immediately.
    """
    session, input_name = _get_embedder()

    mesh = analyze_face_mesh(image_array)
    if mesh is not None and mesh["landmarks"] is not None:
        src = _mesh_alignment_points(image_array, mesh["landmarks"])
        dst = _ARCFACE_TEMPLATE
    else:
        src, dst = _detection_alignment_points(image_array, detection)

    matrix, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.LMEDS)
    if matrix is None:
        raise HTTPException(status_code=400, detail="Could not align face")
    chip = cv2.warpAffine(
        image_array, matrix, (FACE_EMBED_INPUT_SIZE, FACE_EMBED_INPUT_SIZE), flags=cv2.INTER_LINEAR
    )

    blob = ((chip.astype(np.float32) - 127.5) / 127.5).transpose(2, 0, 1)[None]  # RGB, NCHW
    embedding = np.asarray(session.run(None, {input_name: blob})[0][0], dtype=np.float32)
    del chip, blob

    norm = float(np.linalg.norm(embedding))
    if not np.isfinite(norm) or norm == 0.0:
        raise HTTPException(status_code=400, detail="Could not compute a face embedding")
    return embedding / norm


def generate_face_hash(embedding: np.ndarray) -> str:
    """SimHash of a face embedding: sign of 256 random projections -> 64 lowercase hex characters."""
    vector = np.asarray(embedding, dtype=np.float32).ravel()
    if vector.size != FACE_EMBED_DIM:
        raise ValueError(f"Expected a {FACE_EMBED_DIM}-d embedding, got {vector.size}")
    norm = float(np.linalg.norm(vector))
    if norm > 0:
        vector = vector / norm
    bits = (_FACE_HASH_PROJECTION @ vector) > 0
    return np.packbits(bits).tobytes().hex()


def _cosine_to_score(cosine: float) -> float:
    """
    Map an estimated cosine similarity onto the public 0..1 match score.

    Piecewise linear and monotonic, pinned so that cosine == FACE_COSINE_THRESHOLD
    gives exactly FACE_MATCH_THRESHOLD (0.70) and cosine 1.0 gives 1.0.
    """
    t = FACE_COSINE_THRESHOLD
    if cosine <= t:
        return _clamp01(FACE_MATCH_THRESHOLD * max(cosine, 0.0) / t)
    return _clamp01(FACE_MATCH_THRESHOLD + (1.0 - FACE_MATCH_THRESHOLD) * (cosine - t) / (1.0 - t))


def compare_face_hashes(current_hash: str, reference_hash: str) -> float:
    """Return a continuous match score in [0, 1] estimated from the Hamming distance of two face hashes."""
    hex_pattern = re.compile(r"[0-9a-fA-F]{64}")
    if not hex_pattern.fullmatch(current_hash or "") or not hex_pattern.fullmatch(reference_hash or ""):
        return 0.0

    distance = bin(int(current_hash, 16) ^ int(reference_hash, 16)).count("1")
    estimated_cosine = float(np.cos(np.pi * distance / FACE_HASH_BITS))
    return _cosine_to_score(estimated_cosine)


def analyze_face_mesh(image_array: np.ndarray) -> Optional[Dict[str, Any]]:
    """
    Analyze a face using MediaPipe Face Mesh (BONUS liveness signal).

    Returns a dict with face_mesh_complete, landmark_count, nose_tip_z,
    depth_quality, and the raw landmarks (for challenge-response checks),
    or None if no face mesh could be extracted at all.
    """
    with _mp_face_mesh.FaceMesh(
        static_image_mode=True,
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=FACE_DETECTION_MIN_CONFIDENCE,
    ) as face_mesh:
        results = face_mesh.process(image_array)

        if not results.multi_face_landmarks:
            return {
                "face_mesh_complete": False,
                "landmark_count": 0,
                "nose_tip_z": 0.0,
                "depth_quality": "poor",
                "landmarks": None,
            }

        landmarks = results.multi_face_landmarks[0]
        nose_tip_z = float(landmarks.landmark[1].z)

        if abs(nose_tip_z) > 0.03:
            depth_quality = "good"
        elif abs(nose_tip_z) > 0.01:
            depth_quality = "moderate"
        else:
            depth_quality = "poor"

        return {
            "face_mesh_complete": len(landmarks.landmark) >= 400,
            "landmark_count": len(landmarks.landmark),
            "nose_tip_z": nose_tip_z,
            "depth_quality": depth_quality,
            "landmarks": landmarks,
        }


# MediaPipe FaceMesh landmark indices for eye aspect ratio (EAR) calculation.
# Left/right here follow MediaPipe's canonical face mesh indexing.
_LEFT_EYE_EAR_IDX = (33, 160, 158, 133, 153, 144)
_RIGHT_EYE_EAR_IDX = (362, 385, 387, 263, 373, 380)
_EAR_BLINK_THRESHOLD = 0.21


# An eye narrower than this many pixels cannot be measured: treat it as missing data.
_MIN_EYE_WIDTH_PX = 1.0


def _eye_aspect_ratio(
    landmarks, idx: Tuple[int, int, int, int, int, int], image_size: Tuple[int, int]
) -> Optional[float]:
    """
    Eye Aspect Ratio (EAR) for one eye from 6 landmark indices, measured in PIXELS.

    MediaPipe returns x normalised by the image WIDTH and y by the image HEIGHT. EAR is a
    ratio of a mostly-vertical distance to a mostly-horizontal one, so computing it on the
    raw normalised values scales it by roughly width/height: ~1.33x too high in a 640x480
    landscape frame, ~0.75x too low in a 480x640 portrait frame (an open eye can then never
    reach the "open" level). Converting to pixels first makes EAR independent of the frame's
    shape and size.

    `image_size` is (width, height) of the frame that was passed to MediaPipe.
    Returns None when the evidence is unavailable (missing / non-finite landmarks, degenerate
    eye width, invalid frame size). None means "no measurement", never "eye closed".
    """
    width, height = image_size
    if not width or not height or width <= 0 or height <= 0:
        return None
    try:
        p = np.array(
            [[landmarks.landmark[i].x * width, landmarks.landmark[i].y * height] for i in idx],
            dtype=np.float64,
        )
    except (IndexError, AttributeError, TypeError, ValueError):
        return None
    if not np.all(np.isfinite(p)):
        return None
    p1, p2, p3, p4, p5, p6 = p

    horizontal = float(np.linalg.norm(p1 - p4))
    if horizontal < _MIN_EYE_WIDTH_PX:
        return None

    vertical_1 = float(np.linalg.norm(p2 - p6))
    vertical_2 = float(np.linalg.norm(p3 - p5))
    return (vertical_1 + vertical_2) / (2.0 * horizontal)


def detect_blink(face_mesh_landmarks, image_size: Tuple[int, int]) -> Tuple[bool, Optional[float]]:
    """
    Detect whether the eyes look closed in ONE frame, using Eye Aspect Ratio (EAR).

    `image_size` is (width, height) in pixels of the frame the landmarks came from; it is
    required so EAR can never be computed on distorted normalised coordinates.

    Returns (eyes_closed, average_ear). If either eye cannot be measured the result is
    (False, None): unavailable evidence is not a closed eye. A true blink needs the caller
    to compare EAR across a sequence of frames (open -> closed -> open).
    """
    left_ear = _eye_aspect_ratio(face_mesh_landmarks, _LEFT_EYE_EAR_IDX, image_size)
    right_ear = _eye_aspect_ratio(face_mesh_landmarks, _RIGHT_EYE_EAR_IDX, image_size)
    if left_ear is None or right_ear is None:
        return False, None

    avg_ear = (left_ear + right_ear) / 2.0
    return avg_ear < _EAR_BLINK_THRESHOLD, avg_ear


def calculate_quality_score(image_array: np.ndarray, detection: Dict[str, Any]) -> float:
    """
    Calculate an overall image/face quality score in [0, 1] based on:
    - Face detection confidence
    - Image resolution
    - Face size relative to the image
    """
    confidence = detection["confidence"]

    h, w = image_array.shape[:2]
    # Reward resolutions at/above a modest baseline (e.g. 480p-ish); very
    # small images are penalized since they limit useful detail.
    resolution_score = _clamp01(min(w, h) / 480.0)

    bbox = detection["bounding_box"]
    face_area_ratio = bbox.width * bbox.height if bbox is not None else 0.0
    # A face occupying roughly 10%-70% of the frame is considered well-framed.
    if face_area_ratio < 0.02:
        size_score = face_area_ratio / 0.02  # too small / far away
    elif face_area_ratio > 0.9:
        size_score = 1.0 - (face_area_ratio - 0.9) / 0.1  # too close/cropped
    else:
        size_score = 1.0
    size_score = _clamp01(size_score)

    quality_score = 0.5 * confidence + 0.3 * resolution_score + 0.2 * size_score
    return float(_clamp01(quality_score))


def detect_vpn_proxy(ip_address: str, user_agent: str) -> Tuple[bool, float]:
    """
    Heuristically detect VPN/proxy usage from an IP address and user agent.

    Returns (is_vpn, confidence). This is a lightweight heuristic, not a
    real IP-intelligence lookup:
    - Private/reserved IP ranges are treated as a signal of proxying
      (a real client IP reaching this service should be a public address)
    - Loopback addresses are treated similarly
    - Known VPN/proxy/Tor keywords in the user agent raise confidence further
    """
    is_vpn = False
    confidence = 0.0

    if ip_address:
        try:
            ip_obj = ipaddress.ip_address(ip_address)
            if ip_obj.is_loopback:
                is_vpn = True
                confidence = max(confidence, 0.6)
            elif ip_obj.is_private:
                is_vpn = True
                confidence = max(confidence, 0.7)
            elif ip_obj.is_reserved or ip_obj.is_link_local:
                is_vpn = True
                confidence = max(confidence, 0.5)
        except ValueError:
            # Not a parseable IP address at all — treat as a weak risk signal
            # rather than silently ignoring it.
            confidence = max(confidence, 0.2)

    if user_agent:
        vpn_keywords = ("vpn", "proxy", "tunnel", "tor")
        if any(kw in user_agent.lower() for kw in vpn_keywords):
            is_vpn = True
            confidence = max(confidence, 0.8)

    return is_vpn, confidence


# =============================================================================
# PRIVACY REQUIREMENTS (IMPORTANT!)
# =============================================================================
"""
Your implementation MUST follow these privacy requirements:

1. NO RAW IMAGES STORED
   - Process images in-memory only
   - Do not write images to disk
   - Do not send images to external APIs

2. HASH-ONLY STORAGE
   - Store only SHA-256 hashes (64 hex characters)
   - Hashes are one-way - cannot reconstruct face
   - Different faces must produce different hashes

3. EPHEMERAL PROCESSING
   - Clear image data after processing
   - No caching of raw biometric data
   - Use Python's memory management (del, gc.collect)

4. CONSENT TRACKING
   - Require camera_consent=True for enrollment
   - Log consent in audit trail (backend responsibility)

5. RESPONSE HYGIENE
   - Never include base64 image data in responses
   - Only return hashes, scores, and metadata
"""
