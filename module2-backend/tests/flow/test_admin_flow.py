"""Admin operations: bulk creation, enrolment, session lifecycle."""

import pytest

pytestmark = pytest.mark.flow


class TestBulkUserCreation:
    def test_creates_all_new_users(self, client, admin):
        response = client.post("/admin/users/bulk", json={
            "users": [{"email": "b1@example.edu"}, {"email": "b2@example.edu"}],
            "default_password": "password123"}, headers=admin.headers)
        assert response.status_code == 201
        assert response.json()["created"] == 2

    def test_existing_addresses_are_skipped_not_rejected(self, client, admin, student):
        """One duplicate in a batch of two hundred must not discard the other
        199."""
        response = client.post("/admin/users/bulk", json={
            "users": [{"email": student.email}, {"email": "fresh@example.edu"}],
            "default_password": "password123"}, headers=admin.headers)
        assert response.status_code == 201
        assert response.json()["requested"] == 2
        assert response.json()["created"] == 1

    def test_duplicates_within_one_batch_are_collapsed(self, client, admin):
        response = client.post("/admin/users/bulk", json={
            "users": [{"email": "same@example.edu"}, {"email": "same@example.edu"}],
            "default_password": "password123"}, headers=admin.headers)
        assert response.json()["created"] == 1

    def test_created_users_can_log_in(self, client, admin):
        client.post("/admin/users/bulk", json={
            "users": [{"email": "loginme@example.edu"}],
            "default_password": "password123"}, headers=admin.headers)
        assert client.post("/auth/login", json={
            "email": "loginme@example.edu", "password": "password123"}).status_code == 200

    def test_weak_default_password_rejected(self, client, admin):
        assert client.post("/admin/users/bulk", json={
            "users": [{"email": "x@example.edu"}], "default_password": "short"},
            headers=admin.headers).status_code == 422


class TestActivation:
    def test_deactivate_then_reactivate(self, client, admin, student):
        assert client.patch(f"/admin/users/{student.id}/deactivate",
                            headers=admin.headers).json()["is_active"] is False
        assert client.patch(f"/admin/users/{student.id}/activate",
                            headers=admin.headers).json()["is_active"] is True

    def test_reactivated_user_can_log_in_again(self, client, admin, student):
        client.patch(f"/admin/users/{student.id}/deactivate", headers=admin.headers)
        client.patch(f"/admin/users/{student.id}/activate", headers=admin.headers)
        assert client.post("/auth/login", json={
            "email": student.email, "password": "password123"}).status_code == 200

    def test_deleting_a_user_is_a_soft_delete(self, client, admin, student, db):
        """The row is kept and scheduled for erasure, so an accidental deletion
        is recoverable and in-flight attendance records survive."""
        from app.models import User

        assert client.delete(f"/users/{student.id}", headers=admin.headers).status_code == 204
        row = db.get(User, student.id)
        assert row is not None
        assert row.is_active is False
        assert row.scheduled_deletion_at is not None


class TestEnrollment:
    def test_enroll_and_appear_on_the_roster(self, client, admin, instructor, student, course):
        assert client.post("/admin/enrollments/", json={
            "student_id": student.id, "course_id": course["id"]},
            headers=admin.headers).status_code == 201
        roster = client.get(f"/courses/{course['id']}/roster", headers=instructor.headers)
        assert roster.json()["total"] == 1

    def test_enrolling_twice_does_not_duplicate(self, client, admin, instructor,
                                                student, course):
        body = {"student_id": student.id, "course_id": course["id"]}
        client.post("/admin/enrollments/", json=body, headers=admin.headers)
        client.post("/admin/enrollments/", json=body, headers=admin.headers)
        assert client.get(f"/courses/{course['id']}/roster",
                          headers=instructor.headers).json()["total"] == 1

    def test_instructors_cannot_be_enrolled_as_students(self, client, admin,
                                                        instructor, course):
        assert client.post("/admin/enrollments/", json={
            "student_id": instructor.id, "course_id": course["id"]},
            headers=admin.headers).status_code == 422

    def test_unknown_course_is_404(self, client, admin, student):
        assert client.post("/admin/enrollments/", json={
            "student_id": student.id, "course_id": "nope"},
            headers=admin.headers).status_code == 404

    def test_bulk_enrolment_reports_per_address_outcomes(self, client, admin,
                                                         student, course):
        client.post("/admin/enrollments/",
                    json={"student_id": student.id, "course_id": course["id"]},
                    headers=admin.headers)
        client.post("/admin/users/bulk", json={
            "users": [{"email": "newbie@example.edu"}],
            "default_password": "password123"}, headers=admin.headers)

        response = client.post("/admin/enrollments/bulk", json={
            "course_id": course["id"],
            "emails": [student.email, "newbie@example.edu", "ghost@example.edu"]},
            headers=admin.headers)
        assert response.status_code == 200
        assert [r["status"] for r in response.json()["results"]] == [
            "already_enrolled", "enrolled", "not_found"]

    def test_enrolled_student_sees_the_course_in_their_list(self, client, enrolled_student,
                                                            course):
        codes = [c["code"] for c in client.get("/courses/me",
                                               headers=enrolled_student.headers).json()]
        assert course["code"] in codes


class TestSessionLifecycle:
    def test_legal_transition_scheduled_to_active(self, client, admin, make_session):
        session = make_session(active=False)
        response = client.patch(f"/admin/sessions/{session['id']}/status",
                                json={"status": "active"}, headers=admin.headers)
        assert response.status_code == 200
        assert response.json()["actual_start"] is not None

    def test_illegal_transition_scheduled_to_closed(self, client, admin, make_session):
        session = make_session(active=False)
        response = client.patch(f"/admin/sessions/{session['id']}/status",
                                json={"status": "closed"}, headers=admin.headers)
        assert response.status_code == 400
        assert response.json()["code"] == "business_rule_violation"

    def test_active_to_closed_stamps_the_end_time(self, client, admin, make_session):
        session = make_session()
        response = client.patch(f"/admin/sessions/{session['id']}/status",
                                json={"status": "closed"}, headers=admin.headers)
        assert response.json()["actual_end"] is not None

    def test_cancelled_is_terminal(self, client, admin, make_session):
        session = make_session(active=False)
        client.patch(f"/admin/sessions/{session['id']}/status",
                     json={"status": "cancelled"}, headers=admin.headers)
        assert client.patch(f"/admin/sessions/{session['id']}/status",
                            json={"status": "active"}, headers=admin.headers).status_code == 400

    def test_scheduled_session_can_be_deleted(self, client, instructor, make_session):
        session = make_session(active=False)
        assert client.delete(f"/sessions/{session['id']}",
                             headers=instructor.headers).status_code == 204

    def test_active_session_cannot_be_deleted(self, client, instructor, make_session):
        """Anything with attendance history must be cancelled, so the records
        survive."""
        session = make_session()
        assert client.delete(f"/sessions/{session['id']}",
                             headers=instructor.headers).status_code == 400

    def test_statistics_report_attendance(self, client, instructor, enrolled_student,
                                          make_session, passing_submission):
        session = make_session()
        client.post("/checkins/",
                    json=passing_submission(enrolled_student, session["id"]),
                    headers=enrolled_student.headers)
        stats = client.get(f"/sessions/{session['id']}/statistics",
                           headers=instructor.headers)
        assert stats.status_code == 200
        assert stats.json()["by_status"] == {"approved": 1}
        assert stats.json()["enrolled"] == 1
        assert stats.json()["attendance_rate"] == 1.0
