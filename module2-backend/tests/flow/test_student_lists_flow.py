"""Staff can see who is expected at a session and who is enrolled on a course.

GET /sessions/{id}/students lists everyone expected at a session, absentees
included, for admins, the course's instructors and the session's TAs.
GET /enrollments/course/{id} is the course list for admins and instructors.
"""

import pytest

pytestmark = pytest.mark.flow


@pytest.fixture
def classmate(client, admin, make_actor, course):
    student = make_actor("student").grant_consent()
    response = client.post("/admin/enrollments/",
                           json={"student_id": student.id, "course_id": course["id"]},
                           headers=admin.headers)
    assert response.status_code == 201, response.text
    return student


@pytest.fixture
def ta(make_actor):
    return make_actor("ta")


class TestSessionStudents:
    def test_lists_attendees_and_absentees(self, client, instructor, enrolled_student, classmate,
                                           make_session, at_venue):
        session = make_session()
        client.post("/checkins/", json={"session_id": session["id"], **at_venue},
                    headers=enrolled_student.headers)
        rows = client.get(f"/sessions/{session['id']}/students", headers=instructor.headers).json()
        by_id = {row["student_id"]: row for row in rows}
        assert set(by_id) == {enrolled_student.id, classmate.id}
        assert by_id[classmate.id]["status"] == "absent"
        assert by_id[enrolled_student.id]["status"] != "absent"
        assert by_id[classmate.id]["student_email"] == classmate.email

    def test_admin_can_view(self, client, admin, enrolled_student, make_session):
        session = make_session()
        assert client.get(f"/sessions/{session['id']}/students",
                          headers=admin.headers).status_code == 200

    def test_an_assigned_ta_can_view(self, client, instructor, ta, enrolled_student, make_session):
        session = make_session()
        client.post("/admin/assignments/ta", json={"session_id": session["id"], "ta_id": ta.id},
                    headers=instructor.headers)
        response = client.get(f"/sessions/{session['id']}/students", headers=ta.headers)
        assert response.status_code == 200
        assert enrolled_student.id in {row["student_id"] for row in response.json()}

    def test_an_unassigned_ta_cannot(self, client, ta, enrolled_student, make_session):
        session = make_session()
        assert client.get(f"/sessions/{session['id']}/students",
                          headers=ta.headers).status_code == 403

    def test_students_cannot(self, client, enrolled_student, make_session):
        session = make_session()
        assert client.get(f"/sessions/{session['id']}/students",
                          headers=enrolled_student.headers).status_code == 403

    def test_another_courses_instructor_cannot(self, client, admin, instructor, make_actor,
                                               enrolled_student, make_session, course):
        session = make_session()
        client.post("/admin/assignments/instructor",
                    json={"course_id": course["id"], "instructor_id": instructor.id},
                    headers=admin.headers)
        stranger = make_actor("instructor")
        assert client.get(f"/sessions/{session['id']}/students",
                          headers=stranger.headers).status_code == 403

    def test_viewing_is_not_logged_as_an_export(self, client, admin, instructor, enrolled_student,
                                                make_session):
        session = make_session()
        client.get(f"/sessions/{session['id']}/students", headers=instructor.headers)
        logs = client.get("/audit/logs", params={"action": "data_exported"},
                          headers=admin.headers).json()
        assert not [e for e in logs["items"] if e.get("resource_id") == session["id"]]

    def test_unknown_session_is_404(self, client, admin):
        assert client.get("/sessions/00000000-0000-4000-8000-000000000000/students",
                          headers=admin.headers).status_code == 404


class TestCourseStudents:
    def test_admin_and_instructor_see_names_and_emails(self, client, admin, instructor,
                                                       enrolled_student, classmate, course):
        for actor in (admin, instructor):
            body = client.get(f"/enrollments/course/{course['id']}", headers=actor.headers).json()
            emails = {row["email"] for row in body["students"]}
            assert {enrolled_student.email, classmate.email} <= emails
            assert body["total_enrolled"] == 2

    def test_students_cannot(self, client, enrolled_student, course):
        assert client.get(f"/enrollments/course/{course['id']}",
                          headers=enrolled_student.headers).status_code == 403