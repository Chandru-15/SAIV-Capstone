"""The /liveness/check contract for the colour-reflection challenge.

Face detection is stubbed: MediaPipe will not find a face in a rendered
ellipsoid, and these tests are about the endpoint's contract and the JPEG
pipeline, not about MediaPipe. The algorithm itself is tested directly in
test_colour_liveness.py.
"""

import base64
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import colour_liveness as cl          # noqa: E402
from app import main as m                     # noqa: E402
from tests import synthetic as syn            # noqa: E402

SEQUENCE = ["#ff0000", "#00ffff", "#00ff00", "#ff00ff", "#0000ff", "#ffff00"]

WHOLE_FRAME = SimpleNamespace(xmin=0.0, ymin=0.0, width=1.0, height=1.0)


def encode(frame: np.ndarray, fmt: str = "PNG", quality: int = 95) -> str:
    buffer = io.BytesIO()
    kwargs = {"quality": quality} if fmt == "JPEG" else {}
    Image.fromarray(frame).save(buffer, format=fmt, **kwargs)
    return base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(m, "detect_face", lambda image: {
        "detected": True, "confidence": 0.95,
        "bounding_box": WHOLE_FRAME, "keypoints": []})
    monkeypatch.setattr(m, "analyze_face_mesh", lambda image: None)
    monkeypatch.setattr(m, "calculate_quality_score", lambda image, detection: 0.9)
    monkeypatch.setattr(m, "extract_face_embedding",
                        lambda image, detection: np.zeros(m.FACE_EMBED_DIM, dtype=np.float32))
    monkeypatch.setattr(m, "generate_face_hash", lambda embedding: "a" * 64)
    return TestClient(m.app)


def post(client, frames, colours, fmt="PNG", quality=95):
    return client.post("/liveness/check", json={
        "challenge_type": "colour_reflection",
        "challenge_frames": [encode(f, fmt, quality) for f in frames],
        "colour_sequence": colours,
    })


class TestTheContract:
    def test_a_live_face_passes(self, client):
        response = post(client, syn.render_real(SEQUENCE), SEQUENCE)
        assert response.status_code == 200
        body = response.json()
        assert body["liveness_passed"] is True
        assert body["details"]["colour_challenge_passed"] is True

    def test_a_print_is_refused(self, client):
        body = post(client, syn.render_print(SEQUENCE), SEQUENCE).json()
        assert body["liveness_passed"] is False
        assert body["details"]["colour_challenge_passed"] is False
        assert "flat" in body["details"]["colour_reason"].lower()

    def test_a_tablet_is_refused(self, client):
        body = post(client, syn.render_screen(SEQUENCE), SEQUENCE).json()
        assert body["liveness_passed"] is False
        assert body["details"]["colour_challenge_passed"] is False

    def test_one_colour_per_frame_is_required(self, client):
        response = post(client, syn.render_real(SEQUENCE), SEQUENCE[:3])
        assert response.status_code == 400
        assert "one colour per frame" in response.json()["detail"]

    def test_a_missing_sequence_is_refused(self, client):
        response = client.post("/liveness/check", json={
            "challenge_type": "colour_reflection",
            "challenge_frames": [encode(f) for f in syn.render_real(SEQUENCE)]})
        assert response.status_code == 400

    def test_an_unknown_challenge_type_is_refused(self, client):
        response = client.post("/liveness/check", json={
            "challenge_type": "colour", "challenge_response": encode(
                syn.render_real(SEQUENCE)[0])})
        assert response.status_code == 400
        assert "colour_reflection" in response.json()["detail"]

    def test_a_bad_colour_is_refused_not_crashed(self, client):
        response = post(client, syn.render_real(SEQUENCE),
                        ["#ff0000", "not-a-colour"] + SEQUENCE[2:])
        assert response.status_code == 400

    def test_the_existing_challenge_types_still_work(self, client):
        """The new branch must not disturb blink, head_turn or passive."""
        frame = encode(syn.render_real(SEQUENCE)[0])
        for challenge_type in ("passive", "blink", "head_turn"):
            response = client.post("/liveness/check", json={
                "challenge_type": challenge_type, "challenge_frames": [frame] * 4})
            assert response.status_code == 200, challenge_type
            assert "colour_challenge_passed" not in response.json()["details"]


class TestAnUndecidedVerdictCannotPassOnQualityAlone:
    def test_a_dim_capture_does_not_pass(self, client):
        """The gap this closes.

        Quality and detection confidence are high here -- it is a clean,
        well-focused image. Only the colour evidence is missing. Scoring that
        as a pass would mean a tablet held far enough away to wash out the
        reflection satisfies an active liveness challenge on sharpness alone.
        """
        body = post(client, syn.render_real(SEQUENCE, gain=0.004), SEQUENCE).json()
        assert body["details"]["colour_challenge_passed"] is None
        assert body["details"]["score_threshold_passed"] is True
        assert body["liveness_passed"] is False
        assert "bright" in body["details"]["colour_reason"].lower()


class TestItSurvivesTheRealCapturePipeline:
    """The browser sends JPEG at quality 0.9, not PNG.

    JPEG subsamples chroma 2x1 or 2x2 and quantises it hard, which is exactly
    the information this challenge measures. If the signal did not survive
    that, the method would work in tests and fail in production.
    """

    @pytest.mark.parametrize("quality", [90, 75, 60])
    def test_a_live_face_still_passes_as_jpeg(self, client, quality):
        body = post(client, syn.render_real(SEQUENCE), SEQUENCE,
                    fmt="JPEG", quality=quality).json()
        assert body["liveness_passed"] is True, body["details"]["colour_reason"]

    @pytest.mark.parametrize("quality", [90, 75, 60])
    def test_a_print_is_still_refused_as_jpeg(self, client, quality):
        body = post(client, syn.render_print(SEQUENCE), SEQUENCE,
                    fmt="JPEG", quality=quality).json()
        assert body["details"]["colour_challenge_passed"] is False, quality

    @staticmethod
    def _as_jpeg(frames, quality=90):
        return [np.array(Image.open(io.BytesIO(base64.b64decode(
            encode(frame, "JPEG", quality))))) for frame in frames]

    def test_jpeg_leaves_the_decision_margin_intact(self):
        """Asserted as headroom against the threshold, which is what the
        decision uses, rather than as a relative change.

        JPEG's chroma quantisation adds a small noise floor to
        pattern_variation -- it lifts a flat print from about 0.0004 to about
        0.0017. That is a fourfold relative jump on a number near zero and
        says nothing useful; what matters is that 0.0017 is still an order of
        magnitude below the 0.016 threshold while a live face stays well
        above it.
        """
        live = cl.analyse(self._as_jpeg(syn.render_real(SEQUENCE)), SEQUENCE)
        printed = cl.analyse(self._as_jpeg(syn.render_print(SEQUENCE)), SEQUENCE)

        assert live.pattern_variation > cl.PATTERN_VARIATION_MIN * 1.5
        assert printed.pattern_variation < cl.PATTERN_VARIATION_MIN / 5
        assert live.passed is True and printed.passed is False

    def test_jpeg_does_not_manufacture_a_specular_hotspot(self):
        """Blocking artefacts are spatially clustered, so they could in
        principle imitate the glass highlight that refuses tablets."""
        live = cl.analyse(self._as_jpeg(syn.render_real(SEQUENCE), 60), SEQUENCE)
        assert live.specular_concentration < cl.SPECULAR_CONCENTRATION_MAX / 2
