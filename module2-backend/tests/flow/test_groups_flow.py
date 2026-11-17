"""Tutorial / lab groups: course structure, allocation, and who sees which session.

A tutorial or lab session belongs to one group. Only that group's students can
see it, check in to it, or count towards its attendance.
"""

import pytest

pytestmark = pytest.mark.flow


@pytest.fixture
def tutorial_course(client, admin, course) -> dict:
    response = client.put(f"/admin/courses/{course['id']}/structure",
                          json={"has_tutorials": True, "has_labs": False},
                          headers=admin.headers)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def enrol(client, admin, make_actor):
    def _enrol(course_id: str, n: int) -> list:
        students = []
        for _ in range(n):
            student = make_actor("student").grant_consent()
            response = client.post("/admin/enrollments/",
                                   json={"student_id": student.id, "course_id": course_id},
                                   headers=admin.headers)
            assert response.status_code == 201, response.text
            students.append(student)
        return students
    return _enrol


def allocate(client, admin, course_id: str, **body):
    return client.post(f"/admin/courses/{course_id}/groups/allocate",
                       json={"kind": "tutorial", **body}, headers=admin.headers)


class TestStructure:
    def test_structure_is_set_and_returned(self, tutorial_course):
        assert tutorial_course["has_tutorials"] is True
        assert tutorial_course["has_labs"] is False

    def test_new_courses_are_lecture_only_by_default(self, course):
        assert course["has_tutorials"] is False and course["has_labs"] is False

    def test_structure_can_be_chosen_at_creation(self, client, admin):
        created = client.post("/courses/", json={
            "code": "SC3099", "name": "Capstone", "semester": "AY2026-27 Sem 1",
            "has_tutorials": True, "has_labs": True}, headers=admin.headers)
        assert created.status_code == 201
        assert created.json()["has_tutorials"] is True and created.json()["has_labs"] is True

    def test_only_admins_change_structure(self, client, instructor, course):
        assert client.put(f"/admin/courses/{course['id']}/structure",
                          json={"has_tutorials": True, "has_labs": False},
                          headers=instructor.headers).status_code == 403


class TestAllocation:
    def test_by_number_of_groups(self, client, admin, tutorial_course, enrol):
        students = enrol(tutorial_course["id"], 5)
        response = allocate(client, admin, tutorial_course["id"], group_count=2)
        assert response.status_code == 200, response.text
        groups = response.json()["tutorial_groups"]
        assert sorted(g["size"] for g in groups) == [2, 3]
        assert [g["name"] for g in groups] == ["T01", "T02"]
        placed = {m["id"] for g in groups for m in g["members"]}
        assert placed == {s.id for s in students}, "everyone placed, exactly once"

    def test_by_maximum_per_group(self, client, admin, tutorial_course, enrol):
        enrol(tutorial_course["id"], 5)
        groups = allocate(client, admin, tutorial_course["id"],
                          max_per_group=2).json()["tutorial_groups"]
        assert len(groups) == 3
        assert max(g["size"] for g in groups) <= 2

    def test_add_new_places_only_late_enrolments(self, client, admin, tutorial_course, enrol):
        first = enrol(tutorial_course["id"], 4)
        before = allocate(client, admin, tutorial_course["id"],
                          group_count=2).json()["tutorial_groups"]
        where = {m["id"]: g["name"] for g in before for m in g["members"]}

        late = enrol(tutorial_course["id"], 1)[0]
        after = allocate(client, admin, tutorial_course["id"],
                         mode="add_new").json()["tutorial_groups"]
        now_where = {m["id"]: g["name"] for g in after for m in g["members"]}
        assert late.id in now_where
        assert all(now_where[s.id] == where[s.id] for s in first), "nobody else moved"

    def test_exactly_one_way_to_size_a_reshuffle(self, client, admin, tutorial_course, enrol):
        enrol(tutorial_course["id"], 3)
        assert allocate(client, admin, tutorial_course["id"]).status_code == 422
        assert allocate(client, admin, tutorial_course["id"],
                        group_count=2, max_per_group=2).status_code == 422

    def test_refused_for_a_course_without_tutorials(self, client, admin, course, enrol):
        enrol(course["id"], 2)
        assert allocate(client, admin, course["id"], group_count=2).status_code == 422

    def test_only_admins_allocate(self, client, instructor, tutorial_course, enrol):
        enrol(tutorial_course["id"], 2)
        assert client.post(f"/admin/courses/{tutorial_course['id']}/groups/allocate",
                           json={"kind": "tutorial", "group_count": 1},
                           headers=instructor.headers).status_code == 403

    def test_moving_a_student(self, client, admin, tutorial_course, enrol):
        enrol(tutorial_course["id"], 4)
        groups = allocate(client, admin, tutorial_course["id"],
                          group_count=2).json()["tutorial_groups"]
        mover = groups[0]["members"][0]["id"]
        target = groups[1]["id"]
        moved = client.post(f"/admin/courses/{tutorial_course['id']}/groups/move",
                            json={"student_id": mover, "group_id": target},
                            headers=admin.headers)
        assert moved.status_code == 200
        target_group = next(g for g in moved.json()["tutorial_groups"] if g["id"] == target)
        assert mover in {m["id"] for m in target_group["members"]}


class TestGroupSessions:
    @pytest.fixture
    def setup(self, client, admin, tutorial_course, enrol, make_session):
        enrol(tutorial_course["id"], 4)
        groups = allocate(client, admin, tutorial_course["id"],
                          group_count=2).json()["tutorial_groups"]
        mine, other = groups
        session = make_session(session_type="tutorial", group_id=mine["id"])
        return {"session": session, "mine": mine, "other": other}

    def test_session_reports_its_group(self, setup):
        assert setup["session"]["group_id"] == setup["mine"]["id"]
        assert setup["session"]["group_name"] == "T01"
        assert setup["session"]["group_size"] == setup["mine"]["size"]

    def test_lecture_sessions_cannot_take_a_group(self, client, instructor, setup):
        from datetime import timedelta

        from tests.conftest import utcnow

        now = utcnow()
        response = client.post("/sessions/", json={
            "course_id": setup["session"]["course_id"], "name": "Lecture",
            "session_type": "lecture", "group_id": setup["mine"]["id"],
            "scheduled_start": now.isoformat(),
            "scheduled_end": (now + timedelta(hours=1)).isoformat()},
            headers=instructor.headers)
        assert response.status_code == 422

    def test_statistics_count_the_group_not_the_whole_course(self, client, instructor, setup):
        stats = client.get(f"/sessions/{setup['session']['id']}/statistics",
                           headers=instructor.headers).json()
        assert stats["enrolled"] == setup["mine"]["size"]


class TestStudentVisibility:
    """Uses the members' emails to log in as them, since allocation is random."""

    @pytest.fixture
    def scene(self, client, admin, tutorial_course, make_actor, make_session):
        students = []
        for _ in range(4):
            student = make_actor("student").grant_consent()
            client.post("/admin/enrollments/",
                        json={"student_id": student.id, "course_id": tutorial_course["id"]},
                        headers=admin.headers)
            students.append(student)
        groups = allocate(client, admin, tutorial_course["id"],
                          group_count=2).json()["tutorial_groups"]
        by_id = {s.id: s for s in students}
        member = by_id[groups[0]["members"][0]["id"]]
        outsider = by_id[groups[1]["members"][0]["id"]]
        tutorial = make_session(session_type="tutorial", group_id=groups[0]["id"])
        lecture = make_session()
        return {"member": member, "outsider": outsider,
                "tutorial": tutorial, "lecture": lecture}

    def test_another_groups_tutorial_is_hidden(self, client, scene):
        """404, not 403: its existence is not the student's to know."""
        assert client.get(f"/sessions/{scene['tutorial']['id']}",
                          headers=scene["outsider"].headers).status_code == 404
        active = client.get("/sessions/active", headers=scene["outsider"].headers).json()
        assert scene["tutorial"]["id"] not in {s["id"] for s in active}

    def test_own_groups_tutorial_is_visible(self, client, scene):
        assert client.get(f"/sessions/{scene['tutorial']['id']}",
                          headers=scene["member"].headers).status_code == 200
        active = client.get("/sessions/active", headers=scene["member"].headers).json()
        assert scene["tutorial"]["id"] in {s["id"] for s in active}

    def test_lectures_stay_visible_to_every_student(self, client, scene):
        assert client.get(f"/sessions/{scene['lecture']['id']}",
                          headers=scene["outsider"].headers).status_code == 200

    def test_only_the_group_can_check_in(self, client, scene, at_venue):
        body = {"session_id": scene["tutorial"]["id"], **at_venue}
        refused = client.post("/checkins/", json=body, headers=scene["outsider"].headers)
        assert refused.status_code == 403
        assert "different tutorial or lab group" in refused.json()["message"]
        assert client.post("/checkins/", json=body,
                           headers=scene["member"].headers).status_code == 201