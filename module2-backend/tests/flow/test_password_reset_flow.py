"""Forgot password: emailed one-time codes, with an admin fallback.

Email is replaced by capturing the code UserService would send, so these run
without a mail server and can use the real code end to end.
"""

from datetime import timedelta

import pytest

pytestmark = pytest.mark.flow

SAME_REPLY = "If that email has an account, we have sent it a 6-digit code."


@pytest.fixture
def mailbox(monkeypatch) -> list:
    """Every (to, code, by_admin) UserService would have emailed."""
    from app.services.user_service import UserService

    sent: list = []
    monkeypatch.setattr(UserService, "_email_code", staticmethod(
        lambda to, code, by_admin=False: sent.append((to, code, by_admin))))
    return sent


def forgot(client, email: str):
    return client.post("/auth/forgot-password", json={"email": email})


def reset(client, email: str, code: str, new_password: str = "brand-new-pass1"):
    return client.post("/auth/reset-password",
                       json={"email": email, "code": code, "new_password": new_password})


def login(client, email: str, password: str) -> int:
    return client.post("/auth/login", json={"email": email, "password": password}).status_code


class TestRequestingACode:
    def test_a_known_email_gets_a_code(self, client, student, mailbox):
        response = forgot(client, student.email)
        assert response.status_code == 200
        assert response.json()["message"].startswith(SAME_REPLY)
        assert len(mailbox) == 1
        to, code, by_admin = mailbox[0]
        assert to == student.email and len(code) == 6 and code.isdigit() and not by_admin

    def test_an_unknown_email_gets_the_identical_reply_and_nothing_is_sent(self, client,
                                                                          mailbox):
        """The endpoint must not reveal which emails are registered."""
        response = forgot(client, "ghost@example.edu")
        assert response.status_code == 200
        assert response.json()["message"].startswith(SAME_REPLY)
        assert mailbox == []

    def test_an_unknown_email_is_still_audited_with_the_address(self, client, admin, mailbox):
        forgot(client, "ghost@example.edu")
        logs = client.get("/audit/logs?action=password_reset_requested&success=false",
                          headers=admin.headers).json()
        assert logs["total"] >= 1
        assert "ghost@example.edu" in str(logs["items"][0].get("details"))

    def test_the_code_is_not_stored_in_plain_text(self, client, student, mailbox, db):
        from sqlalchemy import select

        from app.models import PasswordResetCode

        forgot(client, student.email)
        code = mailbox[0][1]
        stored = db.scalars(select(PasswordResetCode.code_hash)).all()
        assert stored and all(code not in value for value in stored)

    def test_a_quick_repeat_does_not_send_a_second_code(self, client, student, mailbox):
        forgot(client, student.email)
        forgot(client, student.email)
        assert len(mailbox) == 1

    def test_a_deleted_account_gets_no_code(self, client, admin, student, mailbox):
        client.delete(f"/users/{student.id}", headers=admin.headers)
        forgot(client, student.email)
        assert mailbox == []


class TestUsingTheCode:
    def test_the_right_code_sets_the_new_password(self, client, student, mailbox):
        forgot(client, student.email)
        assert reset(client, student.email, mailbox[0][1]).status_code == 200
        assert login(client, student.email, "brand-new-pass1") == 200
        assert login(client, student.email, "password123") == 401

    def test_a_wrong_code_is_refused(self, client, student, mailbox):
        forgot(client, student.email)
        wrong = "000000" if mailbox[0][1] != "000000" else "111111"
        refused = reset(client, student.email, wrong)
        assert refused.status_code == 422
        assert "invalid or has expired" in refused.json()["message"]
        assert login(client, student.email, "password123") == 200

    def test_a_code_works_once(self, client, student, mailbox):
        forgot(client, student.email)
        code = mailbox[0][1]
        assert reset(client, student.email, code).status_code == 200
        assert reset(client, student.email, code, "another-pass12").status_code == 422

    def test_five_wrong_guesses_cancel_the_code(self, client, student, mailbox):
        """A 6-digit code is only safe because guesses are limited."""
        forgot(client, student.email)
        code = mailbox[0][1]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            reset(client, student.email, wrong)
        assert reset(client, student.email, code).status_code == 422

    def test_an_expired_code_is_refused(self, client, student, mailbox, db):
        from sqlalchemy import update

        from app.models import PasswordResetCode
        from tests.conftest import utcnow

        forgot(client, student.email)
        db.execute(update(PasswordResetCode).values(expires_at=utcnow() - timedelta(minutes=1)))
        db.commit()
        assert reset(client, student.email, mailbox[0][1]).status_code == 422

    def test_a_newer_code_replaces_the_older_one(self, client, student, mailbox, db):
        from sqlalchemy import update

        from app.models import PasswordResetCode

        forgot(client, student.email)
        # Step past the resend cooldown without sleeping.
        db.execute(update(PasswordResetCode).values(
            created_at=PasswordResetCode.created_at - timedelta(minutes=2)))
        db.commit()
        forgot(client, student.email)
        assert len(mailbox) == 2
        old, new = mailbox[0][1], mailbox[1][1]
        if old != new:
            assert reset(client, student.email, old).status_code == 422
        assert reset(client, student.email, new).status_code == 200

    def test_the_new_password_must_meet_the_rules(self, client, student, mailbox):
        forgot(client, student.email)
        assert reset(client, student.email, mailbox[0][1], "short").status_code == 422


class TestAdminFallback:
    def test_requests_appear_on_the_admins_list_until_resolved(self, client, admin,
                                                              student, mailbox):
        forgot(client, student.email)
        pending = client.get("/admin/password-reset-requests", headers=admin.headers).json()
        assert student.email in {row["email"] for row in pending}

        reset(client, student.email, mailbox[0][1])
        pending = client.get("/admin/password-reset-requests", headers=admin.headers).json()
        assert student.email not in {row["email"] for row in pending}

    def test_temporary_password_lets_the_user_sign_in(self, client, admin, student):
        issued = client.post(f"/admin/users/{student.id}/reset-password", headers=admin.headers)
        assert issued.status_code == 200
        temporary = issued.json()["temporary_password"]
        assert login(client, student.email, temporary) == 200
        assert login(client, student.email, "password123") == 401

    def test_emailed_code_needs_smtp(self, client, admin, student, mailbox, monkeypatch):
        monkeypatch.delenv("SMTP_HOST", raising=False)
        assert client.get("/admin/email-status", headers=admin.headers).json() == {"enabled": False}
        refused = client.post(f"/admin/users/{student.id}/send-reset-email",
                              headers=admin.headers)
        assert refused.status_code == 400
        assert mailbox == []

    def test_admin_can_email_a_code_the_user_then_uses(self, client, admin, student,
                                                       mailbox, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "mail.test")
        sent = client.post(f"/admin/users/{student.id}/send-reset-email",
                           headers=admin.headers)
        assert sent.status_code == 200
        to, code, by_admin = mailbox[0]
        assert to == student.email and by_admin is True
        assert reset(client, student.email, code).status_code == 200

    @pytest.mark.parametrize("path,method", [
        ("/admin/password-reset-requests", "get"),
        ("/admin/email-status", "get"),
        ("/admin/users/{id}/reset-password", "post"),
        ("/admin/users/{id}/send-reset-email", "post"),
    ])
    def test_admin_only(self, client, make_actor, student, path, method):
        for role in ("student", "ta", "instructor"):
            actor = student if role == "student" else make_actor(role)
            response = getattr(client, method)(path.format(id=student.id), headers=actor.headers)
            assert response.status_code == 403, f"{role} reached {path}"