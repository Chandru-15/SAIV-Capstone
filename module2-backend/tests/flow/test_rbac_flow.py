"""Role-based access control, exercised as a matrix.

The permission table in API-SPECIFICATION.md is the specification these
assertions encode. A regression in one guard shows up as one failing row.
"""

import pytest

pytestmark = pytest.mark.flow


@pytest.fixture
def actors(make_actor, student):
    return {
        "student": student,
        "ta": make_actor("ta"),
        "instructor": make_actor("instructor"),
        "admin": make_actor("admin"),
    }


class TestUserEndpoints:
    @pytest.mark.parametrize("role,expected", [
        ("student", 403), ("ta", 403), ("instructor", 403), ("admin", 200),
    ])
    def test_list_users_is_admin_only(self, client, actors, role, expected):
        """API-SPECIFICATION.md line 139: "Requires auth (admin only)."
        An instructor who needs their class list uses
        GET /enrollments/course/{id}, which is scoped to their course rather
        than exposing every account in the institution."""
        assert client.get("/users/", headers=actors[role].headers).status_code == expected

    def test_student_may_read_their_own_record(self, client, student):
        assert client.get(f"/users/{student.id}", headers=student.headers).status_code == 200

    def test_student_may_not_read_another_record(self, client, actors):
        assert client.get(f"/users/{actors['admin'].id}",
                          headers=actors["student"].headers).status_code == 403

    @pytest.mark.parametrize("role,expected", [
        ("student", 403), ("ta", 403), ("instructor", 403), ("admin", 204),
    ])
    def test_only_admin_may_delete_users(self, client, actors, make_actor, role, expected):
        victim = make_actor("student")
        assert client.delete(f"/users/{victim.id}",
                             headers=actors[role].headers).status_code == expected


class TestCourseEndpoints:
    @pytest.mark.parametrize("role,expected", [
        ("student", 403), ("ta", 403), ("instructor", 403), ("admin", 201),
    ])
    def test_course_creation_is_admin_only(self, client, actors, role, expected):
        """API-SPECIFICATION.md line 295: "Create a new course. Requires auth
        (admin only)." Instructors manage sessions and enrolments within a
        course, not the course catalogue itself."""
        response = client.post("/courses/", json={
            "code": f"C{role[:3].upper()}", "name": "N", "semester": "AY2024-25 Sem 1"},
            headers=actors[role].headers)
        assert response.status_code == expected

    def test_any_authenticated_user_may_list_courses(self, client, actors):
        for actor in actors.values():
            assert client.get("/courses/", headers=actor.headers).status_code == 200

    def test_anonymous_callers_get_a_redacted_catalogue(self, client, course):
        """/courses/ is readable without a token so the public timetable works,
        but the security-relevant fields are withheld: an unauthenticated
        caller must not learn where to place a spoofed GPS fix or how much risk
        budget a course allows."""
        response = client.get("/courses/")
        assert response.status_code == 200
        item = next(c for c in response.json()["items"] if c["code"] == course["code"])
        for leaked in ("venue_latitude", "venue_longitude",
                       "geofence_radius_meters", "risk_threshold"):
            assert leaked not in item, f"{leaked} must not be public"
        assert item["code"] == course["code"]

    def test_authenticated_callers_see_the_full_record(self, client, student, course):
        item = next(c for c in client.get("/courses/", headers=student.headers)
                    .json()["items"] if c["code"] == course["code"])
        assert item["geofence_radius_meters"] is not None
        assert item["risk_threshold"] is not None


class TestSessionOwnership:
    def test_instructor_may_not_modify_another_instructors_session(
        self, client, make_actor, make_session
    ):
        """Owning the course is not enough; sessions are owned individually."""
        session = make_session()
        intruder = make_actor("instructor")
        assert client.patch(f"/sessions/{session['id']}", json={"name": "Hijacked"},
                            headers=intruder.headers).status_code == 403

    def test_owner_may_modify_their_own_session(self, client, instructor, make_session):
        session = make_session()
        assert client.patch(f"/sessions/{session['id']}", json={"name": "Renamed"},
                            headers=instructor.headers).status_code == 200

    def test_admin_may_modify_any_session(self, client, admin, make_session):
        session = make_session()
        assert client.patch(f"/sessions/{session['id']}", json={"name": "Admin edit"},
                            headers=admin.headers).status_code == 200

    def test_student_may_not_create_a_session(self, client, student, course):
        from tests.conftest import utcnow
        from datetime import timedelta

        now = utcnow()
        assert client.post("/sessions/", json={
            "course_id": course["id"], "name": "Fake",
            "scheduled_start": now.isoformat(),
            "scheduled_end": (now + timedelta(hours=1)).isoformat(),
            "checkin_opens_at": now.isoformat(),
            "checkin_closes_at": (now + timedelta(minutes=10)).isoformat()},
            headers=student.headers).status_code == 403

    def test_only_the_owner_can_mint_a_qr_code(self, client, make_actor, make_session):
        """The QR secret is a check-in credential; leaking it to another
        instructor would let their students check in remotely."""
        session = make_session()
        intruder = make_actor("instructor")
        assert client.post(f"/sessions/{session['id']}/qr-code",
                           headers=intruder.headers).status_code == 403


class TestCheckInEndpoints:
    @pytest.mark.parametrize("role,expected", [
        ("student", 403), ("ta", 200), ("instructor", 200), ("admin", 200),
    ])
    def test_listing_all_check_ins_requires_staff(self, client, actors, role, expected):
        assert client.get("/checkins/", headers=actors[role].headers).status_code == expected

    @pytest.mark.parametrize("role,expected", [
        ("student", 403), ("ta", 200), ("instructor", 200), ("admin", 200),
    ])
    def test_review_queue_requires_staff(self, client, actors, role, expected):
        assert client.get("/checkins/review-queue",
                          headers=actors[role].headers).status_code == expected

    @pytest.fixture
    def flagged(self, client, enrolled_student, make_session, at_venue, face_stub) -> dict:
        face_stub["liveness_body"] = {**face_stub["liveness_body"],
                                      "liveness_passed": True, "liveness_score": 0.25}
        body = client.post("/checkins/",
                           json={"session_id": make_session()["id"], **at_venue},
                           headers=enrolled_student.headers).json()
        assert body["status"] == "flagged"
        return body

    def test_ta_may_not_review_flagged_check_ins(self, client, actors, flagged):
        """TAs see flagged check-ins and appeals for their sessions but do not
        decide them. Approving or rejecting changes a student's attendance
        record (and, for an appeal, is final), so it rests with the course's
        instructors and admins. Changed deliberately with the Module 4
        dashboard work; previously TAs could review."""
        assert client.patch(f"/checkins/{flagged['id']}/review", json={"approve": True},
                            headers=actors["ta"].headers).status_code == 403

    def test_the_session_instructor_may_review(self, client, instructor, flagged):
        assert client.patch(f"/checkins/{flagged['id']}/review", json={"approve": True},
                            headers=instructor.headers).status_code == 200

    def test_an_unrelated_instructor_may_not_review(self, client, make_actor, flagged):
        """Scoped to the instructor's own courses and sessions, not any
        instructor in the institution."""
        stranger = make_actor("instructor")
        assert client.patch(f"/checkins/{flagged['id']}/review", json={"approve": True},
                            headers=stranger.headers).status_code == 403


class TestAdminEndpoints:
    @pytest.mark.parametrize("role,expected", [
        ("student", 403), ("ta", 403), ("instructor", 403), ("admin", 200),
    ])
    def test_deactivation_is_admin_only(self, client, actors, make_actor, role, expected):
        victim = make_actor("student")
        assert client.patch(f"/admin/users/{victim.id}/deactivate",
                            headers=actors[role].headers).status_code == expected

    @pytest.mark.parametrize("role,expected", [
        ("student", 403), ("ta", 403), ("instructor", 403), ("admin", 201),
    ])
    def test_bulk_user_creation_is_admin_only(self, client, actors, role, expected):
        assert client.post("/admin/users/bulk", json={
            "users": [{"email": f"bulk-{role}@example.edu"}],
            "default_password": "password123"},
            headers=actors[role].headers).status_code == expected

    def test_admin_cannot_deactivate_themselves(self, client, admin):
        """There would be no way back: the account that could undo it is the
        one being disabled."""
        assert client.patch(f"/admin/users/{admin.id}/deactivate",
                            headers=admin.headers).status_code == 403


class TestAuditEndpoint:
    @pytest.mark.parametrize("role,expected", [
        ("student", 403), ("ta", 403), ("instructor", 403), ("admin", 200),
    ])
    def test_reading_the_audit_trail_is_admin_only(self, client, actors, role, expected):
        """The trail records who did what to whom, so reading it is itself
        privileged."""
        assert client.get("/audit/logs", headers=actors[role].headers).status_code == expected