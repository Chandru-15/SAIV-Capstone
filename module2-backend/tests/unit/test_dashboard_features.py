"""Pure rules behind the features added for the Module 4 dashboard.

Group allocation arithmetic, the appeal window, reset-code hashing and the
SMTP helper. No database, no network.
"""

from datetime import timedelta

import pytest

from app.models import CheckIn, CheckInStatus
from app.models.base import utcnow

pytestmark = pytest.mark.unit


# -- tutorial / lab groups -----------------------------------------------------


class TestGroupArithmetic:
    def test_names_are_zero_padded_by_kind(self):
        from app.services.group_service import group_name

        assert group_name("tutorial", 1) == "T01"
        assert group_name("lab", 12) == "L12"

    @pytest.mark.parametrize("students,groups", [(240, 8), (241, 3), (7, 7), (5, 2)])
    def test_split_is_even_to_within_one(self, students, groups):
        """"Split into 8 tutorials" must never give one group 40 and another 20."""
        from app.services.group_service import split_evenly

        buckets = split_evenly(list(range(students)), groups)
        sizes = [len(b) for b in buckets]
        assert len(buckets) == groups
        assert sum(sizes) == students
        assert max(sizes) - min(sizes) <= 1

    def test_split_places_everyone_exactly_once(self):
        from app.services.group_service import split_evenly

        people = list(range(23))
        placed = [p for bucket in split_evenly(people, 4) for p in bucket]
        assert sorted(placed) == people

    @pytest.mark.parametrize("students,cap,expected", [
        (240, 30, 8), (241, 30, 9), (30, 30, 1), (1, 30, 1), (0, 30, 1),
    ])
    def test_groups_needed_for_a_maximum_size(self, students, cap, expected):
        """"At most 30 per tutorial" rounds UP: 241 students need a ninth group."""
        from app.services.group_service import groups_needed

        assert groups_needed(students, cap) == expected


# -- appeal window ---------------------------------------------------------------


def checkin(status=CheckInStatus.FLAGGED, *, days_ago=0, appealed=False) -> CheckIn:
    row = CheckIn(status=status, checked_in_at=utcnow() - timedelta(days=days_ago))
    if appealed:
        row.appealed_at = utcnow()
    return row


class TestAppealRules:
    def test_deadline_is_the_window_after_checking_in(self):
        from app.models.checkin import APPEAL_WINDOW_DAYS

        row = checkin()
        assert row.appeal_deadline - row.checked_in_at == timedelta(days=APPEAL_WINDOW_DAYS)

    @pytest.mark.parametrize("status", [CheckInStatus.FLAGGED, CheckInStatus.REJECTED])
    def test_fresh_flagged_or_rejected_can_be_appealed(self, status):
        assert checkin(status).can_appeal is True

    @pytest.mark.parametrize("status", [CheckInStatus.APPROVED, CheckInStatus.PENDING,
                                        CheckInStatus.APPEALED])
    def test_other_statuses_cannot(self, status):
        assert checkin(status).can_appeal is False

    def test_one_appeal_per_check_in(self):
        """A decided appeal is final. Without this, a rejected appeal would be
        appealable again, since a rejected check-in is itself appealable."""
        assert checkin(CheckInStatus.REJECTED, appealed=True).can_appeal is False

    def test_closed_after_the_window(self):
        from app.models.checkin import APPEAL_WINDOW_DAYS

        assert checkin(days_ago=APPEAL_WINDOW_DAYS + 1).can_appeal is False
        assert checkin(days_ago=APPEAL_WINDOW_DAYS - 1).can_appeal is True


# -- password reset codes -------------------------------------------------------


class TestResetCodeHash:
    def test_hash_is_deterministic(self):
        from app.services.user_service import UserService

        assert UserService._code_hash("row-1", "123456") == UserService._code_hash("row-1", "123456")

    def test_same_code_hashes_differently_per_row(self):
        """Salted with the row id: two users who happen to get the same 6
        digits do not share a stored value."""
        from app.services.user_service import UserService

        assert UserService._code_hash("row-1", "123456") != UserService._code_hash("row-2", "123456")

    def test_hash_is_keyed_with_the_secret(self, monkeypatch):
        """A database dump alone cannot brute-force the 10^6 codes offline."""
        from app.services.user_service import UserService

        before = UserService._code_hash("row-1", "123456")
        monkeypatch.setenv("SECRET_KEY", "a-different-secret")
        assert UserService._code_hash("row-1", "123456") != before

    def test_the_code_itself_is_not_in_the_hash(self):
        from app.services.user_service import UserService

        assert "123456" not in UserService._code_hash("row-1", "123456")


# -- email ------------------------------------------------------------------------


class TestEmailService:
    def test_disabled_without_smtp_host(self, monkeypatch):
        from app.services import email_service

        monkeypatch.delenv("SMTP_HOST", raising=False)
        assert email_service.email_configured() is False
        assert email_service.send_email("a@b.edu", "s", "b") is False

    def test_sends_through_the_configured_server(self, monkeypatch):
        from app.services import email_service

        sent = []

        class FakeSMTP:
            def __init__(self, host, port, timeout=None):
                sent.append(("connect", host, port))

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def starttls(self):
                sent.append(("starttls",))

            def login(self, user, password):
                sent.append(("login", user))

            def send_message(self, message):
                sent.append(("send", message["To"], message["Subject"]))

        monkeypatch.setenv("SMTP_HOST", "mail.test")
        monkeypatch.setenv("SMTP_PORT", "2525")
        monkeypatch.setenv("SMTP_STARTTLS", "0")
        monkeypatch.delenv("SMTP_USER", raising=False)
        monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)

        assert email_service.send_email("stu@b.edu", "Your code", "123456") is True
        assert ("connect", "mail.test", 2525) in sent
        assert ("send", "stu@b.edu", "Your code") in sent
        assert ("starttls",) not in sent, "STARTTLS off for a local catcher like Mailpit"
        assert not any(step[0] == "login" for step in sent), "no login without SMTP_USER"

    def test_a_mail_server_failure_returns_false_not_an_exception(self, monkeypatch):
        """Email is best-effort: a dead mail server must not turn
        POST /auth/forgot-password into a 500."""
        from app.services import email_service

        class Refusing:
            def __init__(self, *a, **kw):
                raise ConnectionRefusedError("no mail server")

        monkeypatch.setenv("SMTP_HOST", "mail.test")
        monkeypatch.setattr(email_service.smtplib, "SMTP", Refusing)
        assert email_service.send_email("stu@b.edu", "s", "b") is False