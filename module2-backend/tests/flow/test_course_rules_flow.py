"""Course rules the dashboard relies on: only admins edit a course, and a
student, once enrolled, cannot be taken off it."""

import pytest

from app.models import User
from app.services.enrollment_service import EnrollmentService
from app.services.exceptions import BusinessRuleError

pytestmark = pytest.mark.flow


class TestCourseEditing:
    def test_admin_can_edit_a_course(self, client, admin, course):
        response = client.patch(f"/courses/{course['id']}", json={"name": "Deep Learning II"},
                                headers=admin.headers)
        assert response.status_code == 200
        assert response.json()["name"] == "Deep Learning II"

    def test_an_assigned_instructor_cannot_edit_it(self, client, admin, instructor, course):
        client.post("/admin/assignments/instructor",
                    json={"course_id": course["id"], "instructor_id": instructor.id},
                    headers=admin.headers)
        assert client.patch(f"/courses/{course['id']}", json={"name": "Renamed"},
                            headers=instructor.headers).status_code == 403

    def test_students_and_tas_cannot_edit_it(self, client, make_actor, course):
        for role in ("student", "ta"):
            actor = make_actor(role)
            assert client.patch(f"/courses/{course['id']}", json={"name": "Renamed"},
                                headers=actor.headers).status_code == 403


class TestEnrolmentIsPermanent:
    def test_removing_an_enrolled_student_is_refused(self, db, admin, enrolled_student, course):
        actor = db.get(User, admin.id)
        with pytest.raises(BusinessRuleError):
            EnrollmentService(db).drop(actor, enrolled_student.id, course["id"])

    def test_the_student_is_still_on_the_roster(self, client, db, admin, enrolled_student, course):
        actor = db.get(User, admin.id)
        with pytest.raises(BusinessRuleError):
            EnrollmentService(db).drop(actor, enrolled_student.id, course["id"])
        roster = client.get(f"/courses/{course['id']}/roster", headers=admin.headers).json()
        assert enrolled_student.id in {row["student_id"] for row in roster["items"]}

    def test_there_is_no_endpoint_for_removing_a_student(self, client, admin, enrolled_student, course):
        for method, path in (
            ("DELETE", f"/admin/enrollments/{enrolled_student.id}/{course['id']}"),
            ("DELETE", f"/courses/{course['id']}/enrollments/{enrolled_student.id}"),
            ("DELETE", f"/enrollments/{course['id']}/{enrolled_student.id}"),
        ):
            assert client.request(method, path, headers=admin.headers).status_code in (404, 405)


class TestCourseDeletion:
    def test_admin_deletes_a_course_nobody_has_checked_in_to(self, client, admin, enrolled_student,
                                                             make_session, course):
        make_session()
        assert client.delete(f"/courses/{course['id']}", headers=admin.headers).status_code == 204
        assert client.get(f"/courses/{course['id']}", headers=admin.headers).status_code == 404

    def test_only_admins_delete(self, client, admin, instructor, make_actor, course):
        client.post("/admin/assignments/instructor",
                    json={"course_id": course["id"], "instructor_id": instructor.id},
                    headers=admin.headers)
        for actor in (instructor, make_actor("ta"), make_actor("student")):
            assert client.delete(f"/courses/{course['id']}", headers=actor.headers).status_code == 403

    def test_a_course_with_attendance_cannot_be_deleted(self, client, admin, enrolled_student,
                                                        make_session, at_venue, course):
        session = make_session()
        checked = client.post("/checkins/", json={"session_id": session["id"], **at_venue},
                              headers=enrolled_student.headers)
        assert checked.status_code == 201, checked.text
        response = client.delete(f"/courses/{course['id']}", headers=admin.headers)
        assert response.status_code == 409
        assert client.get(f"/courses/{course['id']}", headers=admin.headers).status_code == 200

    def test_deletion_is_audited(self, client, admin, course):
        client.delete(f"/courses/{course['id']}", headers=admin.headers)
        logs = client.get("/audit/logs", params={"action": "course_deleted"}, headers=admin.headers).json()
        assert any(entry.get("resource_id") == course["id"] for entry in logs["items"])

    def test_unknown_course_is_404(self, client, admin):
        assert client.delete("/courses/00000000-0000-4000-8000-000000000000",
                             headers=admin.headers).status_code == 404