"""The audit trail and the transaction guarantees around it."""

import pytest

pytestmark = pytest.mark.flow


def actions(client, admin, **params) -> list:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    body = client.get(f"/audit/logs?{query}", headers=admin.headers).json()
    return [entry["action"] for entry in body["items"]]


class TestTrailIsWritten:
    def test_registration_is_recorded(self, client, admin):
        client.post("/auth/register", json={
            "email": "audited@example.edu", "full_name": "A", "password": "password123"})
        assert "user_created" in actions(client, admin)

    def test_successful_login_is_recorded(self, client, admin, student):
        assert "login_success" in actions(client, admin, user_id=student.id)

    def test_approved_check_in_is_recorded(self, client, admin, enrolled_student,
                                           make_session, passing_submission):
        client.post("/checkins/",
                    json=passing_submission(enrolled_student, make_session()["id"]),
                    headers=enrolled_student.headers)
        assert "checkin_approved" in actions(client, admin, user_id=enrolled_student.id)

    def test_entries_carry_the_resource_they_refer_to(self, client, admin,
                                                      enrolled_student, make_session, at_venue):
        created = client.post("/checkins/",
                              json={"session_id": make_session()["id"], **at_venue},
                              headers=enrolled_student.headers).json()
        entries = client.get(
            f"/audit/logs?resource_type=checkin&resource_id={created['id']}",
            headers=admin.headers).json()
        assert entries["total"] >= 1

    def test_client_reported_events_are_attributed_to_the_caller(self, client, student):
        """user_id and ip_address come from the connection, never the body —
        otherwise anyone could forge entries attributed to someone else."""
        created = client.post("/audit/logs", json={
            "action": "custom_client_event", "details": {"k": "v"}},
            headers=student.headers)
        assert created.status_code == 201
        assert created.json()["user_id"] == student.id

    def test_free_form_actions_are_accepted(self, client, student):
        """The spec's action list ends with 'etc.', so the column has no CHECK
        and new actions must not require a migration."""
        assert client.post("/audit/logs", json={"action": "brand_new_action_type"},
                           headers=student.headers).status_code == 201


class TestFailuresSurviveRollback:
    def test_failed_login_is_recorded_despite_the_rollback(self, client, admin, student):
        """authenticate() records the failure and then raises, which rolls the
        transaction back. Without a second connection the entry would vanish —
        and brute-force detection would be impossible to build.
        """
        client.post("/auth/login", json={"email": student.email, "password": "wrongpassword"})
        recorded = client.get(
            f"/audit/logs?action=login_failed&success=false", headers=admin.headers).json()
        assert recorded["total"] >= 1

    def test_repeated_failures_all_recorded(self, client, admin, student):
        for _ in range(3):
            client.post("/auth/login", json={"email": student.email, "password": "nope12345"})
        recorded = client.get("/audit/logs?action=login_failed&success=false",
                              headers=admin.headers).json()
        assert recorded["total"] >= 3

    def test_login_attempt_for_unknown_user_is_recorded(self, client, admin):
        client.post("/auth/login", json={"email": "ghost@example.edu", "password": "password123"})
        assert client.get("/audit/logs?action=login_failed&success=false",
                          headers=admin.headers).json()["total"] >= 1


class TestRolledBackWorkLeavesNoTrace:
    def test_rejected_duplicate_registration_writes_nothing(self, client, admin, student):
        """A conflict rolls back, so there must be no user_created entry for the
        attempt — an audit trail describing events that never happened is worse
        than none."""
        before = client.get("/audit/logs?action=user_created", headers=admin.headers).json()["total"]
        assert client.post("/auth/register", json={
            "email": student.email, "full_name": "Dup", "password": "password123"
        }).status_code == 400
        after = client.get("/audit/logs?action=user_created", headers=admin.headers).json()["total"]
        assert after == before

    def test_duplicate_check_in_writes_no_second_entry(self, client, admin, enrolled_student,
                                                       make_session, at_venue):
        body = {"session_id": make_session()["id"], **at_venue}
        client.post("/checkins/", json=body, headers=enrolled_student.headers)
        before = client.get(f"/audit/logs?user_id={enrolled_student.id}",
                            headers=admin.headers).json()["total"]
        assert client.post("/checkins/", json=body,
                           headers=enrolled_student.headers).status_code == 400
        after = client.get(f"/audit/logs?user_id={enrolled_student.id}",
                           headers=admin.headers).json()["total"]
        assert after == before


class TestImmutability:
    def test_repository_refuses_to_delete_entries(self, db, client, student):
        """The rule lives in the repository, because a database trigger would
        also fire on the ON DELETE SET NULL that anonymises the trail."""
        from app.repositories import AuditLogRepository
        from app.repositories.audit_log_repository import AuditLogIsAppendOnlyError

        repo = AuditLogRepository(db)
        entry = repo.record("test_action", user_id=student.id)
        db.commit()

        with pytest.raises(AuditLogIsAppendOnlyError):
            repo.delete(entry)
        with pytest.raises(AuditLogIsAppendOnlyError):
            repo.delete_all()

    def test_deleting_a_user_anonymises_rather_than_erases_the_trail(self, db, client,
                                                                     admin, make_actor):
        """Cascades remove the user's PII; the audit rows survive with a null
        user_id so the history of what happened is preserved."""
        from app.models import AuditLog, User
        from sqlalchemy import select, func

        victim = make_actor("student")
        before = db.scalar(select(func.count()).select_from(AuditLog)
                           .where(AuditLog.user_id == victim.id))
        assert before >= 1

        db.delete(db.get(User, victim.id))
        db.commit()

        assert db.get(User, victim.id) is None
        survived = db.scalar(select(func.count()).select_from(AuditLog)
                             .where(AuditLog.user_id.is_(None)))
        assert survived >= before
