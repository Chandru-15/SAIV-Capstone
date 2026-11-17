"""Password hashing and JWT — no database needed.

AuthService is constructed with ``None`` for the session throughout: token and
password handling touch no repository, and pinning that here means a future
refactor that sneaks in a query breaks loudly.
"""

from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from app.models import User, UserRole
from app.services import ValidationError
from app.services.auth_service import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ALGORITHM,
    REFRESH_TOKEN_EXPIRE_DAYS,
    AuthService,
)
from app.services.exceptions import AuthenticationError

pytestmark = pytest.mark.unit

SECRET = "unit-test-secret"


@pytest.fixture
def auth() -> AuthService:
    return AuthService(None, secret_key=SECRET)


@pytest.fixture
def user() -> User:
    return User(id="user-1", email="stu@example.edu", full_name="Stu",
                hashed_password="x", role=UserRole.STUDENT, is_active=True)


class TestPasswords:
    def test_hash_is_bcrypt_and_not_reversible(self, auth):
        hashed = auth.hash_password("password123")
        assert hashed.startswith("$2b$")
        assert "password123" not in hashed

    def test_cost_factor_meets_the_security_requirement(self, auth):
        """SECURITY-REQUIREMENTS.md mandates >= 10."""
        assert int(auth.hash_password("password123").split("$")[2]) >= 10

    def test_verify_round_trip(self, auth):
        hashed = auth.hash_password("password123")
        assert auth.verify_password("password123", hashed)
        assert not auth.verify_password("password124", hashed)

    def test_salt_makes_identical_passwords_hash_differently(self, auth):
        assert auth.hash_password("password123") != auth.hash_password("password123")

    @pytest.mark.parametrize("bad", ["", "short", "1234567"])
    def test_short_passwords_rejected(self, auth, bad):
        with pytest.raises(ValidationError):
            auth.validate_password(bad)

    def test_eight_characters_is_accepted(self, auth):
        auth.validate_password("12345678")


class TestTokens:
    def test_claims_match_the_specification(self, auth, user):
        claims = jwt.decode(auth.create_access_token(user), SECRET, algorithms=[ALGORITHM])
        assert claims["sub"] == user.id
        assert claims["email"] == user.email
        assert claims["role"] == "student"
        assert {"iat", "exp"} <= set(claims)

    def test_role_is_serialised_as_its_value(self, auth, user):
        """'student', never 'UserRole.STUDENT'."""
        claims = jwt.decode(auth.create_access_token(user), SECRET, algorithms=[ALGORITHM])
        assert claims["role"] == UserRole.STUDENT.value

    def test_access_token_is_not_already_expired(self, auth, user):
        """Regression test.

        A naive-UTC datetime's .timestamp() is interpreted as LOCAL time, so on
        a machine at UTC+8 every token was issued 8 hours in the past and was
        therefore born 7 hours expired. Only aware datetimes convert correctly.
        This test fails on any machine east or west of UTC if that regresses.
        """
        claims = jwt.decode(auth.create_access_token(user), SECRET, algorithms=[ALGORITHM])
        now = datetime.now(timezone.utc).timestamp()
        assert claims["exp"] > now, "token is already expired at issue time"
        assert abs(claims["iat"] - now) < 60, "iat is not close to the real clock"

    def test_access_ttl_is_one_hour(self, auth, user):
        claims = jwt.decode(auth.create_access_token(user), SECRET, algorithms=[ALGORITHM])
        assert claims["exp"] - claims["iat"] == ACCESS_TOKEN_EXPIRE_MINUTES * 60 == 3600

    def test_refresh_ttl_is_seven_days(self, auth, user):
        claims = jwt.decode(auth.create_refresh_token(user), SECRET, algorithms=[ALGORITHM])
        assert claims["exp"] - claims["iat"] == REFRESH_TOKEN_EXPIRE_DAYS * 86400

    def test_algorithm_is_hs256(self, auth, user):
        header = jwt.get_unverified_header(auth.create_access_token(user))
        assert header["alg"] == "HS256"

    def test_decode_round_trip(self, auth, user):
        assert auth.decode_token(auth.create_access_token(user))["sub"] == user.id


class TestTokenAbuse:
    def test_refresh_token_is_rejected_where_an_access_token_is_required(self, auth, user):
        """Without the type claim, a 7-day refresh token would be usable as a
        bearer token and silently extend session lifetime."""
        with pytest.raises(AuthenticationError):
            auth.decode_token(auth.create_refresh_token(user), expected_type="access")

    def test_access_token_is_rejected_at_the_refresh_endpoint(self, auth, user):
        with pytest.raises(AuthenticationError):
            auth.decode_token(auth.create_access_token(user), expected_type="refresh")

    def test_tampered_signature_is_rejected(self, auth, user):
        token = auth.create_access_token(user)
        with pytest.raises(AuthenticationError):
            auth.decode_token(token[:-4] + "AAAA")

    def test_token_signed_with_another_key_is_rejected(self, auth, user):
        forged = AuthService(None, secret_key="a-different-secret").create_access_token(user)
        with pytest.raises(AuthenticationError):
            auth.decode_token(forged)

    def test_expired_token_is_rejected(self, auth, user):
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        expired = jwt.encode(
            {"sub": user.id, "type": "access",
             "iat": int(past.timestamp()), "exp": int((past + timedelta(minutes=1)).timestamp())},
            SECRET, algorithm=ALGORITHM,
        )
        with pytest.raises(AuthenticationError):
            auth.decode_token(expired)

    def test_alg_none_token_is_rejected(self, auth, user):
        """The classic JWT attack: strip the signature and set alg to none."""
        import base64, json

        def b64(data: dict) -> str:
            return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

        forged = f'{b64({"alg": "none", "typ": "JWT"})}.{b64({"sub": user.id, "type": "access"})}.'
        with pytest.raises(AuthenticationError):
            auth.decode_token(forged)

    def test_missing_secret_key_refuses_to_sign(self, user):
        """No insecure default: an unset SECRET_KEY must fail loudly rather
        than sign tokens anyone could forge."""
        service = AuthService(None, secret_key=None)
        service._secret_key = None
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            service.create_access_token(user)
