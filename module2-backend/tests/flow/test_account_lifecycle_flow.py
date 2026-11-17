"""Account lifecycle in the audit trail, and auto-closing finished sessions.

Activate, deactivate, delete and restore each have their own action, and each
entry names both the admin who acted (user_id) and the account it was done to
(resource_id plus the email in details).
"""

import json
from datetime import timedelta

import pytest

pytestmark = pytest.mark.flow


def latest(client, admin, action: str) -> dict:
    items = client.get(f"/audit/logs?action={action}", headers=admin.headers).json()["items"]
    assert items, f"no {action} entry"
    return items[0]


class TestAccountActions:
    def test_deactivation_is_logged_with_the_target(self, client, admin, student):
        client.patch(f"/admin/users/{student.id}/deactivate", headers=admin.headers)
        entry = latest(client, admin, "user_deactivated")
        assert entry["user_id"] == admin.id
        assert entry["resource_id"] == student.id
        assert json.loads(entry["details"])["email"] == student.email

    def test_activation_is_logged(self, client, admin, student):
        client.patch(f"/admin/users/{student.id}/deactivate", headers=admin.headers)
        client.patch(f"/admin/users/{student.id}/activate", headers=admin.headers)
        assert latest(client, admin, "user_activated")["resource_id"] == student.id

    def test_deletion_is_logged_as_a_deletion(self, client, admin, student):
        client.delete(f"/users/{student.id}", headers=admin.headers)
        entry = latest(client, admin, "user_deleted")
        assert entry["resource_id"] == student.id
        assert "scheduled_deletion_at" in json.loads(entry["details"])

    def test_activating_a_deleted_account_restores_it(self, client, admin, student):
        """Restoring cancels the pending erasure, so an account in use again is
        never purged by the retention sweep."""
        client.delete(f"/users/{student.id}", headers=admin.headers)
        restored = client.patch(f"/admin/users/{student.id}/activate", headers=admin.headers)
        assert restored.status_code == 200
        assert restored.json()["scheduled_deletion_at"] is None
        assert latest(client, admin, "user_restored")["resource_id"] == student.id

    def test_deleted_accounts_report_when_they_will_be_erased(self, client, admin, student):
        client.delete(f"/users/{student.id}", headers=admin.headers)
        body = client.get(f"/users/{student.id}", headers=admin.headers).json()
        assert body["is_active"] is False
        assert body["scheduled_deletion_at"] is not None

    def test_an_admin_cannot_delete_themselves(self, client, admin):
        """Same reason as deactivation: nobody would be left to undo it."""
        assert client.delete(f"/users/{admin.id}", headers=admin.headers).status_code == 403


class TestAutoClose:
    """main.close_finished_sessions closes ACTIVE sessions whose scheduled end
    and check-in window have both passed. Opening a session stays manual."""

    @pytest.fixture(autouse=True)
    def same_database(self):
        from app import database
        from tests.conftest import TEST_DATABASE_URL

        if database.DATABASE_URL != TEST_DATABASE_URL:
            pytest.skip("close_finished_sessions uses the app's own DATABASE_URL")

    def test_a_finished_session_is_closed_and_audited(self, client, admin, make_session):
        from app.main import close_finished_sessions
        from tests.conftest import utcnow

        now = utcnow()
        finished = make_session(
            scheduled_start=(now - timedelta(hours=3)).isoformat(),
            scheduled_end=(now - timedelta(hours=1)).isoformat(),
            checkin_opens_at=(now - timedelta(hours=3, minutes=15)).isoformat(),
            checkin_closes_at=(now - timedelta(hours=2, minutes=45)).isoformat(),
        )
        running = make_session()

        close_finished_sessions()
        assert client.get(f"/sessions/{finished['id']}",
                          headers=admin.headers).json()["status"] == "closed"
        assert client.get(f"/sessions/{running['id']}",
                          headers=admin.headers).json()["status"] == "active"
        assert latest(client, admin, "session_auto_closed")["resource_id"] == finished["id"]

    def test_scheduled_sessions_are_never_opened_or_closed_automatically(
        self, client, admin, make_session
    ):
        from app.main import close_finished_sessions
        from tests.conftest import utcnow

        now = utcnow()
        never_opened = make_session(
            active=False,
            scheduled_start=(now - timedelta(hours=3)).isoformat(),
            scheduled_end=(now - timedelta(hours=1)).isoformat(),
            checkin_opens_at=(now - timedelta(hours=3, minutes=15)).isoformat(),
            checkin_closes_at=(now - timedelta(hours=2, minutes=45)).isoformat(),
        )
        close_finished_sessions()
        assert client.get(f"/sessions/{never_opened['id']}",
                          headers=admin.headers).json()["status"] == "scheduled"