"""Authentication flows, end to end over HTTP."""

import pytest

pytestmark = pytest.mark.flow


class TestRegistrationAndLogin:
    def test_register_then_login_then_read_self(self, client):
        register = client.post("/auth/register", json={
            "email": "new@example.edu", "full_name": "New Student",
            "password": "password123"})
        assert register.status_code == 201
        assert register.json()["role"] == "student"
        assert "hashed_password" not in register.json()

        login = client.post("/auth/login", json={
            "email": "new@example.edu", "password": "password123"})
        assert login.status_code == 200
        body = login.json()
        assert {"access_token", "refresh_token", "token_type", "user"} <= set(body)

        me = client.get("/auth/me",
                        headers={"Authorization": f"Bearer {body['access_token']}"})
        assert me.status_code == 200
        assert me.json()["email"] == "new@example.edu"

    def test_email_is_case_insensitive_on_login(self, client):
        client.post("/auth/register", json={
            "email": "Mixed.Case@Example.edu", "full_name": "M", "password": "password123"})
        assert client.post("/auth/login", json={
            "email": "mixed.case@example.edu", "password": "password123"}).status_code == 200

    def test_duplicate_registration_conflicts(self, client):
        payload = {"email": "dup@example.edu", "full_name": "D", "password": "password123"}
        assert client.post("/auth/register", json=payload).status_code == 201
        conflict = client.post("/auth/register", json=payload)
        # 400 per the spec: "Email already registered" is a 400 for this endpoint.
        assert conflict.status_code == 400
        assert conflict.json()["code"] == "business_rule_violation"

    def test_duplicate_detection_ignores_case(self, client):
        client.post("/auth/register", json={
            "email": "case@example.edu", "full_name": "C", "password": "password123"})
        assert client.post("/auth/register", json={
            "email": "CASE@EXAMPLE.EDU", "full_name": "C", "password": "password123"
        }).status_code == 400

    @pytest.mark.parametrize("password", ["short", "", "1234567"])
    def test_weak_password_rejected(self, client, password):
        assert client.post("/auth/register", json={
            "email": "weak@example.edu", "full_name": "W", "password": password
        }).status_code == 422


class TestLoginFailures:
    def test_wrong_password_is_401(self, client, student):
        assert client.post("/auth/login", json={
            "email": student.email, "password": "wrongpassword"}).status_code == 401

    def test_unknown_user_is_401_not_404(self, client):
        """404 here would let anyone enumerate which addresses are registered."""
        assert client.post("/auth/login", json={
            "email": "ghost@example.edu", "password": "password123"}).status_code == 401

    def test_failure_messages_are_indistinguishable(self, client, student):
        unknown = client.post("/auth/login", json={
            "email": "ghost@example.edu", "password": "password123"})
        wrong = client.post("/auth/login", json={
            "email": student.email, "password": "wrongpassword"})
        assert unknown.json()["message"] == wrong.json()["message"]

    def test_deactivated_account_cannot_log_in(self, client, admin, student):
        client.patch(f"/admin/users/{student.id}/deactivate", headers=admin.headers)
        response = client.post("/auth/login", json={
            "email": student.email, "password": "password123"})
        assert response.status_code == 403


class TestTokens:
    def test_missing_token_is_401_with_challenge_header(self, client):
        response = client.get("/auth/me")
        assert response.status_code == 401
        assert response.headers.get("www-authenticate") == "Bearer"

    @pytest.mark.parametrize("token", ["garbage", "a.b.c", ""])
    def test_malformed_tokens_rejected(self, client, token):
        assert client.get("/auth/me",
                          headers={"Authorization": f"Bearer {token}"}).status_code == 401

    def test_refresh_returns_a_usable_access_token(self, client, student):
        refreshed = client.post("/auth/refresh", json={
            "refresh_token": client.post("/auth/login", json={
                "email": student.email, "password": "password123"}).json()["refresh_token"]})
        assert refreshed.status_code == 200
        assert client.get("/auth/me", headers={
            "Authorization": f"Bearer {refreshed.json()['access_token']}"}).status_code == 200

    def test_access_token_cannot_be_used_to_refresh(self, client, student):
        assert client.post("/auth/refresh",
                           json={"refresh_token": student.token}).status_code == 401

    def test_token_of_a_deactivated_user_stops_working(self, client, admin, student):
        """The token is still cryptographically valid; the check is on the
        account, so deactivation takes effect immediately rather than at expiry."""
        assert client.get("/auth/me", headers=student.headers).status_code == 200
        client.patch(f"/admin/users/{student.id}/deactivate", headers=admin.headers)
        assert client.get("/auth/me", headers=student.headers).status_code == 403


class TestConsent:
    def test_consent_defaults_to_false(self, client, make_actor):
        actor = make_actor("student")
        body = client.get("/auth/me", headers=actor.headers).json()
        assert body["camera_consent"] is False
        assert body["geolocation_consent"] is False

    def test_consent_can_be_granted_and_read_back(self, client, make_actor):
        actor = make_actor("student")
        updated = client.patch("/auth/me", json={
            "camera_consent": True, "geolocation_consent": True}, headers=actor.headers)
        assert updated.json()["camera_consent"] is True
        assert client.get("/auth/me", headers=actor.headers).json()["geolocation_consent"] is True

    def test_partial_update_leaves_the_other_flag_alone(self, client, student):
        client.patch("/auth/me", json={"camera_consent": False}, headers=student.headers)
        body = client.get("/auth/me", headers=student.headers).json()
        assert body["camera_consent"] is False
        assert body["geolocation_consent"] is True

    def test_withdrawing_camera_consent_clears_face_enrolment(self, client, student, db):
        """Retaining biometric-derived data after consent is withdrawn is
        exactly what the privacy requirements forbid."""
        from app.models import User

        user = db.get(User, student.id)
        user.face_embedding_hash = "b" * 64
        user.face_enrolled = True
        db.commit()

        client.patch("/auth/me", json={"camera_consent": False}, headers=student.headers)

        db.expire_all()
        user = db.get(User, student.id)
        assert user.face_enrolled is False
        assert user.face_embedding_hash is None

    def test_logout_succeeds(self, client, student):
        assert client.post("/auth/logout", headers=student.headers).status_code == 200


class TestLogoutEndsEverySession:
    """Logout has to mean something on the server.

    A JWT is self-contained: nothing is stored, so the server cannot take an
    issued token back. Before ``token_version`` existed, ``POST /auth/logout``
    only wrote an audit row and the presented token stayed usable until it
    expired -- the UI said "signed out" while the credential still worked.

    These tests pin the stronger promise the endpoint now makes: logging out
    retires EVERY token the user holds, on every device, not just the one that
    happened to make the request.
    """

    @staticmethod
    def login(client, email, password="password123") -> dict:
        response = client.post("/auth/login",
                               json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return response.json()

    def test_the_presented_access_token_stops_working(self, client, student):
        assert client.get("/auth/me", headers=student.headers).status_code == 200
        client.post("/auth/logout", headers=student.headers)
        assert client.get("/auth/me", headers=student.headers).status_code == 401

    def test_a_session_on_another_device_is_ended_too(self, client, student):
        """The whole point. The token used to log out is the only one the
        server can even see; the phone in the user's pocket holds another."""
        other_device = self.login(client, student.email)
        other = {"Authorization": f"Bearer {other_device['access_token']}"}
        assert client.get("/auth/me", headers=other).status_code == 200

        client.post("/auth/logout", headers=student.headers)

        assert client.get("/auth/me", headers=other).status_code == 401

    def test_the_refresh_token_cannot_mint_a_new_session(self, client, student):
        """A refresh token outlives an access token by days. Leaving it valid
        would let a logged-out client issue itself a fresh access token and
        carry on indefinitely, which is the leak this is meant to close."""
        tokens = self.login(client, student.email)
        client.post("/auth/logout", headers=student.headers)

        response = client.post("/auth/refresh",
                               json={"refresh_token": tokens["refresh_token"]})
        assert response.status_code == 401, response.text

    def test_signing_back_in_works_normally(self, client, student):
        """Invalidation must not be a lockout: the next login mints tokens
        carrying the new version, which match."""
        client.post("/auth/logout", headers=student.headers)
        fresh = self.login(client, student.email)
        headers = {"Authorization": f"Bearer {fresh['access_token']}"}
        assert client.get("/auth/me", headers=headers).status_code == 200

    def test_one_user_logging_out_leaves_everybody_else_alone(self, client, student,
                                                               make_actor):
        bystander = make_actor("student")
        client.post("/auth/logout", headers=student.headers)
        assert client.get("/auth/me", headers=bystander.headers).status_code == 200
