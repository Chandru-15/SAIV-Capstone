"""DTO validation and, more importantly, what the response DTOs refuse to leak."""

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.models import User, UserRole
from app.schemas import (
    CheckInCreateRequest,
    CheckInResponse,
    CourseCreateRequest,
    RegisterRequest,
    SessionCreateRequest,
    SessionResponse,
    UserResponse,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 1, 10, 0)


class TestRequestValidation:
    def test_valid_registration(self):
        payload = RegisterRequest(email="a@b.edu", full_name="A", password="password123")
        assert payload.role == UserRole.STUDENT

    @pytest.mark.parametrize("email", ["notanemail", "@b.edu", "a@", ""])
    def test_malformed_email_rejected(self, email):
        with pytest.raises(PydanticValidationError):
            RegisterRequest(email=email, full_name="A", password="password123")

    def test_short_password_rejected_at_the_edge(self):
        with pytest.raises(PydanticValidationError):
            RegisterRequest(email="a@b.edu", full_name="A", password="1234567")

    @pytest.mark.parametrize("lat,lon", [(91, 0), (-91, 0), (0, 181), (0, -181)])
    def test_coordinates_outside_earth_rejected(self, lat, lon):
        with pytest.raises(PydanticValidationError):
            CourseCreateRequest(code="C", name="N", semester="S",
                                venue_latitude=lat, venue_longitude=lon)

    @pytest.mark.parametrize("threshold", [-0.1, 1.1])
    def test_risk_threshold_must_be_a_probability(self, threshold):
        with pytest.raises(PydanticValidationError):
            CourseCreateRequest(code="C", name="N", semester="S", risk_threshold=threshold)

    def test_geofence_radius_must_be_positive(self):
        with pytest.raises(PydanticValidationError):
            CourseCreateRequest(code="C", name="N", semester="S", geofence_radius_meters=0)

    @pytest.mark.parametrize("field", [
        "liveness_passed", "liveness_score", "liveness_challenge_type",
        "face_match_passed", "face_match_score", "face_embedding_hash",
    ])
    def test_biometric_verdicts_are_not_client_settable(self, field):
        """They were removed, and that removal is the security property.

        While they existed, a session with require_face_match=true could be
        satisfied by posting face_match_passed: true, which made the
        "required checks must pass" gate in CheckInService._decide constrain
        only honest clients. Every verdict now comes from module 3 via `image`
        or `challenge_frames`, or it does not exist.
        """
        assert field not in CheckInCreateRequest.model_fields

    def test_unknown_biometric_keys_are_ignored_not_honoured(self):
        """Pydantic drops unknown keys by default, so a client that keeps
        sending the old fields is ignored rather than believed."""
        payload = CheckInCreateRequest(session_id="s", face_match_passed=True,
                                       liveness_score=1.0)
        assert not hasattr(payload, "face_match_passed")
        assert not hasattr(payload, "liveness_score")


class TestSessionWindowValidator:
    def _body(self, **overrides):
        body = dict(
            course_id="c", name="Lecture",
            scheduled_start=NOW, scheduled_end=NOW + timedelta(hours=2),
            checkin_opens_at=NOW - timedelta(minutes=15),
            checkin_closes_at=NOW + timedelta(minutes=15),
        )
        body.update(overrides)
        return body

    def test_valid_window_accepted(self):
        assert SessionCreateRequest(**self._body()).name == "Lecture"

    def test_end_before_start_rejected(self):
        with pytest.raises(PydanticValidationError, match="scheduled_end"):
            SessionCreateRequest(**self._body(scheduled_end=NOW - timedelta(hours=1)))

    def test_zero_length_session_rejected(self):
        with pytest.raises(PydanticValidationError):
            SessionCreateRequest(**self._body(scheduled_end=NOW))

    def test_inverted_checkin_window_rejected(self):
        with pytest.raises(PydanticValidationError, match="checkin_closes_at"):
            SessionCreateRequest(**self._body(
                checkin_opens_at=NOW + timedelta(hours=1),
                checkin_closes_at=NOW - timedelta(hours=1)))


class TestResponsesDoNotLeak:
    """These are the tests that stop a future column addition from silently
    widening the API."""

    def test_user_response_omits_credentials_and_biometrics(self):
        user = User(id="u1", email="a@b.edu", full_name="A",
                    hashed_password="$2b$12$supersecret", role=UserRole.STUDENT,
                    is_active=True, camera_consent=False, geolocation_consent=False,
                    face_enrolled=False, face_embedding_hash="b" * 64,
                    created_at=NOW, updated_at=NOW)
        body = UserResponse.model_validate(user).model_dump()
        assert "hashed_password" not in body
        assert "face_embedding_hash" not in body
        assert "supersecret" not in str(body)

    def test_checkin_response_omits_the_face_hash(self):
        assert "face_embedding_hash" not in CheckInResponse.model_fields

    def test_session_response_omits_the_qr_secret(self):
        """The QR secret is the check-in credential. It is issued only through
        its own owner-only endpoint, never in a general session payload."""
        assert "qr_code_secret" not in SessionResponse.model_fields
