"""FaceClient against a stubbed module 3.

httpx.MockTransport means the whole client — retries, timeouts, status
mapping, response parsing — is exercised without a running face service.
"""

import base64

import httpx
import pytest

from app.clients import (
    FaceClient,
    FaceImageRejected,
    FaceServiceUnavailable,
    normalise_image,
)

pytestmark = pytest.mark.unit

IMAGE = base64.b64encode(b"not-really-a-jpeg-but-valid-base64").decode()
HASH = "a" * 64


def client_returning(handler) -> FaceClient:
    return FaceClient(base_url="http://face:8001",
                      transport=httpx.MockTransport(handler))


def json_handler(status: int, body: dict, calls: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return httpx.Response(status, json=body)
    return handler


class TestNormaliseImage:
    def test_bare_base64_passes_through(self):
        assert normalise_image(IMAGE) == IMAGE

    def test_data_url_prefix_is_stripped(self):
        """canvas.toDataURL() produces this by default, and module 3 would
        reject it with a confusing 400 two hops away."""
        assert normalise_image(f"data:image/png;base64,{IMAGE}") == IMAGE

    def test_jpeg_data_url_prefix_is_stripped(self):
        assert normalise_image(f"data:image/jpeg;base64,{IMAGE}") == IMAGE

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_empty_is_rejected(self, bad):
        with pytest.raises(FaceImageRejected):
            normalise_image(bad)

    def test_non_base64_is_rejected_before_the_network_call(self):
        with pytest.raises(FaceImageRejected, match="base64"):
            normalise_image("this is not base64 !!!")


class TestEnroll:
    def test_successful_enrolment_is_parsed(self):
        client = client_returning(json_handler(201, {
            "enrollment_successful": True, "face_template_hash": HASH,
            "quality_score": 0.85, "details": {"face_detected": True}}))
        result = client.enroll("user-1", IMAGE)
        assert result.enrollment_successful
        assert result.face_template_hash == HASH
        assert result.quality_score == 0.85
        assert result.details["face_detected"] is True

    def test_accepts_200_as_well_as_201(self):
        client = client_returning(json_handler(200, {
            "enrollment_successful": True, "face_template_hash": HASH,
            "quality_score": 0.9}))
        assert client.enroll("user-1", IMAGE).enrollment_successful

    def test_sends_the_documented_payload(self):
        calls: list = []
        client = client_returning(json_handler(201, {
            "enrollment_successful": True, "face_template_hash": HASH,
            "quality_score": 0.9}, calls))
        client.enroll("user-7", IMAGE, camera_consent=True)
        import json

        body = json.loads(calls[0].content)
        assert body == {"user_id": "user-7", "image": IMAGE, "camera_consent": True}
        assert calls[0].url.path == "/face/enroll"

    def test_no_face_detected_becomes_FaceImageRejected(self):
        client = client_returning(json_handler(400, {"detail": "No face detected"}))
        with pytest.raises(FaceImageRejected) as exc:
            client.enroll("user-1", IMAGE)
        assert exc.value.detail == "No face detected"


class TestVerifyAndLiveness:
    def test_verify_is_parsed(self):
        client = client_returning(json_handler(200, {
            "match_passed": True, "match_score": 0.81, "match_threshold": 0.70,
            "face_detected": True, "current_template_hash": "b" * 64}))
        result = client.verify(IMAGE, HASH)
        assert result.match_passed and result.match_score == 0.81
        assert result.match_threshold == 0.70

    def test_liveness_is_parsed(self):
        client = client_returning(json_handler(200, {
            "liveness_passed": True, "liveness_score": 0.77,
            "liveness_threshold": 0.60, "face_embedding_hash": HASH,
            "details": {"challenge": "passive"}}))
        result = client.check_liveness(IMAGE, "passive")
        assert result.liveness_passed and result.liveness_score == 0.77
        assert result.face_embedding_hash == HASH

    def test_liveness_sends_module3s_field_name(self):
        """Module 3 calls the image `challenge_response`, not `image`. Getting
        this wrong is a 422 that looks like a service outage."""
        calls: list = []
        client = client_returning(json_handler(200, {
            "liveness_passed": True, "liveness_score": 0.8,
            "liveness_threshold": 0.6, "face_embedding_hash": HASH}, calls))
        client.check_liveness(IMAGE, "blink")
        import json

        body = json.loads(calls[0].content)
        assert body["challenge_response"] == IMAGE
        assert body["challenge_type"] == "blink"

    def test_risk_assessment_is_parsed(self):
        client = client_returning(json_handler(200, {
            "risk_score": 0.42, "risk_level": "MEDIUM", "pass_threshold": True,
            "risk_threshold": 0.50, "signal_breakdown": {"liveness": 0.2},
            "recommendations": ["Disable VPN for check-in"]}))
        result = client.assess_risk(liveness_score=0.8)
        assert result.risk_level == "MEDIUM"
        assert result.recommendations == ["Disable VPN for check-in"]


class TestFailureHandling:
    def test_connection_refused_becomes_unavailable(self):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(FaceServiceUnavailable, match="unreachable"):
            client_returning(handler).check_liveness(IMAGE)

    def test_read_timeout_becomes_unavailable(self):
        def handler(request):
            raise httpx.ReadTimeout("too slow", request=request)

        with pytest.raises(FaceServiceUnavailable):
            client_returning(handler).check_liveness(IMAGE)

    def test_connection_faults_are_retried(self):
        attempts: list = []

        def handler(request):
            attempts.append(request)
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(FaceServiceUnavailable):
            client_returning(handler).check_liveness(IMAGE)
        assert len(attempts) == 2, "transient faults should be retried once"

    def test_a_retry_that_succeeds_returns_the_result(self):
        state = {"n": 0}

        def handler(request):
            state["n"] += 1
            if state["n"] == 1:
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(200, json={
                "liveness_passed": True, "liveness_score": 0.9,
                "liveness_threshold": 0.6, "face_embedding_hash": HASH})

        assert client_returning(handler).check_liveness(IMAGE).liveness_passed

    def test_server_errors_are_retried(self):
        attempts: list = []
        with pytest.raises(FaceServiceUnavailable):
            client_returning(json_handler(500, {"detail": "boom"}, attempts)).check_liveness(IMAGE)
        assert len(attempts) == 2

    def test_bad_requests_are_not_retried(self):
        """A 400 is a real answer about the image. Retrying wastes a second of
        MediaPipe time to get the same reply."""
        attempts: list = []
        with pytest.raises(FaceImageRejected):
            client_returning(json_handler(400, {"detail": "No face detected"},
                                          attempts)).check_liveness(IMAGE)
        assert len(attempts) == 1

    def test_contract_mismatch_is_not_retried(self):
        attempts: list = []
        with pytest.raises(FaceServiceUnavailable, match="unexpected 422"):
            client_returning(json_handler(422, {"detail": "field required"},
                                          attempts)).check_liveness(IMAGE)
        assert len(attempts) == 1

    def test_non_json_response_is_reported_clearly(self):
        def handler(request):
            return httpx.Response(200, text="<html>gateway</html>")

        with pytest.raises(FaceServiceUnavailable, match="non-JSON"):
            client_returning(handler).check_liveness(IMAGE)

    def test_health_returns_false_instead_of_raising(self):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        assert client_returning(handler).health() is False

    def test_health_returns_true_when_module3_answers(self):
        assert client_returning(json_handler(200, {"status": "healthy"})).health() is True


class TestPrivacy:
    def test_the_image_is_never_logged(self, caplog):
        """A log line containing base64 face data is a privacy breach that
        outlives the request."""
        import logging

        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(FaceServiceUnavailable):
                client_returning(handler).enroll("user-1", IMAGE)

        assert IMAGE not in caplog.text
        assert IMAGE[:24] not in caplog.text
