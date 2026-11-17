"""Staff assignments and permission scoping, enforced on the server.

Instructors are assigned to courses by an admin; TAs to sessions by an admin or
the course's instructor. Once that is set up, every staff read and action is
limited to the caller's own courses and sessions - a hidden dashboard button
is not a secure endpoint.
"""

from datetime import timedelta

import pytest

from tests.conftest import utcnow

pytestmark = pytest.mark.flow


def session_body(course_id: str) -> dict:
    now = utcnow()
    return {
        "course_id": course_id, "name": "Tutorial 3",
        "scheduled_start": now.isoformat(),
        "scheduled_end": (now + timedelta(hours=1)).isoformat(),
        "checkin_opens_at": (now - timedelta(minutes=15)).isoformat(),
        "checkin_closes_at": (now + timedelta(minutes=15)).isoformat(),
    }


def assign_instructor(client, admin, course_id: str, instructor_id: str):
    return client.post("/admin/assignments/instructor",
                       json={"course_id": course_id, "instructor_id": instructor_id},
                       headers=admin.headers)


@pytest.fixture
def other_instructor(make_actor):
    return make_actor("instructor")


@pytest.fixture
def ta(make_actor):
    return make_actor("ta")


class TestInstructorAssignment:
    def test_admin_assigns_and_the_course_lists_them(self, client, admin, instructor, course):
        assert assign_instructor(client, admin, course["id"], instructor.id).status_code == 201
        detail = client.get(f"/courses/{course['id']}", headers=admin.headers).json()
        assert instructor.id in detail["instructor_ids"]

    def test_a_course_can_be_co_taught(self, client, admin, instructor, other_instructor, course):
        assign_instructor(client, admin, course["id"], instructor.id)
        assign_instructor(client, admin, course["id"], other_instructor.id)
        detail = client.get(f"/courses/{course['id']}", headers=admin.headers).json()
        assert {instructor.id, other_instructor.id} <= set(detail["instructor_ids"])

    def test_assigning_twice_is_idempotent(self, client, admin, instructor, course):
        assign_instructor(client, admin, course["id"], instructor.id)
        assert assign_instructor(client, admin, course["id"], instructor.id).status_code == 201
        listed = client.get(f"/admin/assignments/instructor/{course['id']}",
                            headers=admin.headers).json()
        assert [p["id"] for p in listed].count(instructor.id) == 1

    def test_only_admins_assign_instructors(self, client, instructor, course):
        assert assign_instructor(client, instructor, course["id"], instructor.id).status_code == 403

    def test_only_instructor_accounts_can_be_assigned(self, client, admin, student, course):
        assert assign_instructor(client, admin, course["id"], student.id).status_code == 422


class TestSessionCreation:
    def test_unassigned_course_stays_open_to_any_instructor(self, client, other_instructor,
                                                            course):
        """Nothing breaks before an admin sets up assignments."""
        assert client.post("/sessions/", json=session_body(course["id"]),
                           headers=other_instructor.headers).status_code == 201

    def test_once_assigned_only_assigned_instructors_create_sessions(
        self, client, admin, instructor, other_instructor, course
    ):
        assign_instructor(client, admin, course["id"], instructor.id)
        assert client.post("/sessions/", json=session_body(course["id"]),
                           headers=other_instructor.headers).status_code == 403
        assert client.post("/sessions/", json=session_body(course["id"]),
                           headers=instructor.headers).status_code == 201


class TestInstructorScope:
    @pytest.fixture
    def checked_in(self, client, enrolled_student, make_session, at_venue) -> dict:
        session = make_session()
        client.post("/checkins/", json={"session_id": session["id"], **at_venue},
                    headers=enrolled_student.headers)
        return session

    def test_an_instructor_sees_their_own_sessions_check_ins(self, client, instructor,
                                                             checked_in):
        assert client.get("/checkins/", headers=instructor.headers).json()["total"] == 1

    def test_another_instructor_sees_none_of_them(self, client, other_instructor, checked_in):
        assert client.get("/checkins/", headers=other_instructor.headers).json()["total"] == 0

    @pytest.mark.parametrize("path", [
        "/checkins/session/{id}", "/stats/sessions/{id}", "/export/session/{id}",
        "/sessions/{id}/statistics",
    ])
    def test_another_instructor_cannot_read_the_register_or_figures(
        self, client, instructor, other_instructor, checked_in, path
    ):
        url = path.format(id=checked_in["id"])
        assert client.get(url, headers=other_instructor.headers).status_code == 403
        assert client.get(url, headers=instructor.headers).status_code == 200

    def test_an_assigned_co_instructor_sees_every_session_of_the_course(
        self, client, admin, other_instructor, course, checked_in
    ):
        """A co-instructor can cover for a colleague."""
        assign_instructor(client, admin, course["id"], other_instructor.id)
        assert client.get("/checkins/", headers=other_instructor.headers).json()["total"] == 1

    def test_admin_sees_everything(self, client, admin, checked_in):
        assert client.get("/checkins/", headers=admin.headers).json()["total"] == 1


class TestTAScope:
    def test_a_ta_sees_nothing_until_assigned(self, client, ta, enrolled_student,
                                              make_session, at_venue):
        session = make_session()
        client.post("/checkins/", json={"session_id": session["id"], **at_venue},
                    headers=enrolled_student.headers)
        assert client.get("/checkins/", headers=ta.headers).json()["total"] == 0

    def test_the_session_instructor_assigns_a_ta(self, client, instructor, ta,
                                                 enrolled_student, make_session, at_venue):
        session = make_session()
        client.post("/checkins/", json={"session_id": session["id"], **at_venue},
                    headers=enrolled_student.headers)
        assert client.post("/admin/assignments/ta",
                           json={"session_id": session["id"], "ta_id": ta.id},
                           headers=instructor.headers).status_code == 201
        assert client.get("/checkins/", headers=ta.headers).json()["total"] == 1
        mine = client.get("/sessions/my-sessions", headers=ta.headers).json()
        assert session["id"] in {s["id"] for s in mine}

    def test_an_unrelated_instructor_cannot_assign_tas(self, client, admin, instructor,
                                                       other_instructor, ta, course,
                                                       make_session):
        """Only once the course has an assigned instructor is it closed to
        everyone else - an unassigned course stays open to any instructor."""
        session = make_session()
        assert assign_instructor(client, admin, course["id"], instructor.id).status_code == 201
        assert client.post("/admin/assignments/ta",
                           json={"session_id": session["id"], "ta_id": ta.id},
                           headers=other_instructor.headers).status_code == 403

    def test_an_assigned_ta_may_open_and_close_but_not_cancel(self, client, instructor, ta,
                                                              make_session):
        """Opening and closing is the job of whoever is in the room; cancelling
        is a scheduling decision for the instructor."""
        session = make_session(active=False)
        client.post("/admin/assignments/ta",
                    json={"session_id": session["id"], "ta_id": ta.id},
                    headers=instructor.headers)
        url = f"/admin/sessions/{session['id']}/status"
        assert client.patch(url, json={"status": "active"}, headers=ta.headers).status_code == 200
        assert client.patch(url, json={"status": "cancelled"},
                            headers=ta.headers).status_code == 403
        assert client.patch(url, json={"status": "closed"}, headers=ta.headers).status_code == 200

    def test_an_unassigned_ta_cannot_open_a_session(self, client, ta, make_session):
        session = make_session(active=False)
        assert client.patch(f"/admin/sessions/{session['id']}/status",
                            json={"status": "active"}, headers=ta.headers).status_code == 403