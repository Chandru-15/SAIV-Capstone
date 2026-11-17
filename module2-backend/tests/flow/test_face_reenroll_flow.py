"""Face re-enrolment is asked for by a student and granted by an admin.

The password step-up this replaced is covered by
``TestReEnrolmentNeedsAnApproval`` in test_face_flow.py. This file is about the
request record itself: who may ask, who may decide, what each role can see, and
why a decision cannot be taken twice.
"""

import pytest

from tests.conftest import FAKE_IMAGE

pytestmark = pytest.mark.flow

REQUESTS = "/users/me/face/reenroll-requests"
REVIEW = "/face-reenroll-requests"


@pytest.fixture
def enrolled_with_face(client_with_face, enrolled_student):
    """A student enrolled on the course who has a template to replace."""
    assert client_with_face.post("/users/me/face/enroll", json={"image": FAKE_IMAGE},
                                 headers=enrolled_student.headers).status_code == 200
    return enrolled_student


@pytest.fixture
def assigned_instructor(client, admin, instructor, course):
    """The course's instructor, actually assigned to it.

    ``can_manage_course`` lets any instructor manage a course nobody has been
    assigned to, so that course setup is never blocked. ``visible_student_ids``
    deliberately does NOT inherit that: authorising a template replacement
    should not widen just because the admin has not finished assigning staff.
    An instructor therefore has to be assigned here to see anybody.
    """
    assert client.post("/admin/assignments/instructor",
                       json={"course_id": course["id"], "instructor_id": instructor.id},
                       headers=admin.headers).status_code == 201
    return instructor


def ask(client, student, reason=None):
    response = client.post(REQUESTS,
                           json={} if reason is None else {"reason": reason},
                           headers=student.headers)
    assert response.status_code == 201, response.text
    return response.json()


class TestAskingForOne:
    def test_a_student_with_a_template_may_ask(self, client_with_face,
                                               enrolled_with_face):
        body = ask(client_with_face, enrolled_with_face, "New glasses")
        assert body["status"] == "pending"
        assert body["user_id"] == enrolled_with_face.id
        assert body["reason"] == "New glasses"
        # Nothing has been decided or spent yet.
        assert body["reviewed_by_id"] is None
        assert body["reviewed_at"] is None
        assert body["used_at"] is None

    def test_the_request_carries_no_photo(self, client_with_face,
                                          enrolled_with_face, face_stub):
        """A pending row holding a capture would be biometric data at rest
        that ``unenroll`` could never erase. Module 3 is not called at all."""
        face_stub["calls"].clear()
        body = ask(client_with_face, enrolled_with_face)
        assert face_stub["calls"] == []
        assert "image" not in body

    def test_asking_without_a_template_is_refused(self, client_with_face, student):
        """There would be nothing to replace, and a first enrolment needs no
        approval, so this is the wrong door."""
        response = client_with_face.post(REQUESTS, json={}, headers=student.headers)
        assert response.status_code == 400, response.text
        assert "enrol" in response.json()["message"].lower()

    def test_only_one_request_may_be_open(self, client_with_face,
                                          enrolled_with_face):
        """Otherwise one student can fill a reviewer's queue with the same ask."""
        ask(client_with_face, enrolled_with_face)
        again = client_with_face.post(REQUESTS, json={},
                                      headers=enrolled_with_face.headers)
        assert again.status_code == 400, again.text
        assert "awaiting" in again.json()["message"].lower()

    def test_cannot_ask_again_while_an_approval_is_unspent(
            self, client_with_face, enrolled_with_face, admin):
        first = ask(client_with_face, enrolled_with_face)
        client_with_face.patch(f"{REVIEW}/{first['id']}", json={"approve": True},
                               headers=admin.headers)
        again = client_with_face.post(REQUESTS, json={},
                                      headers=enrolled_with_face.headers)
        assert again.status_code == 400, again.text
        assert "already approved" in again.json()["message"].lower()

    def test_a_spent_approval_frees_the_student_to_ask_again(
            self, client_with_face, enrolled_with_face, admin):
        """A second genuine change of appearance must not be locked out by the
        first one."""
        first = ask(client_with_face, enrolled_with_face)
        client_with_face.patch(f"{REVIEW}/{first['id']}", json={"approve": True},
                               headers=admin.headers)
        assert client_with_face.put("/users/me/face", json={"image": FAKE_IMAGE},
                                    headers=enrolled_with_face.headers
                                    ).status_code == 200
        ask(client_with_face, enrolled_with_face, "Changed again")

    def test_a_denial_frees_the_student_to_ask_again(self, client_with_face,
                                                     enrolled_with_face, admin):
        first = ask(client_with_face, enrolled_with_face)
        client_with_face.patch(f"{REVIEW}/{first['id']}", json={"approve": False},
                               headers=admin.headers)
        ask(client_with_face, enrolled_with_face, "Asking again")

    def test_a_student_sees_their_own_history(self, client_with_face,
                                              enrolled_with_face, admin):
        first = ask(client_with_face, enrolled_with_face)
        client_with_face.patch(f"{REVIEW}/{first['id']}",
                               json={"approve": False, "notes": "See me in person"},
                               headers=admin.headers)
        mine = client_with_face.get(REQUESTS, headers=enrolled_with_face.headers)
        assert mine.status_code == 200, mine.text
        items = mine.json()["items"]
        assert [r["id"] for r in items] == [first["id"]]
        # The student is told why, so a denial is not a silent dead end.
        assert items[0]["status"] == "denied"
        assert items[0]["review_notes"] == "See me in person"

    def test_a_student_sees_only_their_own(self, client_with_face,
                                           enrolled_with_face, make_actor):
        ask(client_with_face, enrolled_with_face)
        other = make_actor("student").grant_consent()
        mine = client_with_face.get(REQUESTS, headers=other.headers)
        assert mine.json()["items"] == []

    def test_free_text_is_escaped_on_the_way_out(self, client_with_face,
                                                 enrolled_with_face):
        """SECURITY-REQUIREMENTS.md: HTML escape user-provided content in
        responses. The reason is rendered in both the portal and the
        dashboard."""
        body = ask(client_with_face, enrolled_with_face, "<script>alert(1)</script>")
        assert "<script>" not in body["reason"]
        assert "&lt;script&gt;" in body["reason"]

    def test_anonymous_requests_are_refused(self, client_with_face):
        assert client_with_face.post(REQUESTS, json={}).status_code == 401


class TestWhoMayDecide:
    def test_even_the_course_instructor_may_not_approve(self, client_with_face,
                                                        enrolled_with_face,
                                                        assigned_instructor):
        """Admins only: a student on no course has no instructor at all, so an
        instructor-decided request could wait forever."""
        row = ask(client_with_face, enrolled_with_face)
        response = client_with_face.patch(f"{REVIEW}/{row['id']}",
                                          json={"approve": True},
                                          headers=assigned_instructor.headers)
        assert response.status_code == 403, response.text

    def test_an_admin_may_approve_anyone(self, client_with_face,
                                         enrolled_with_face, admin):
        row = ask(client_with_face, enrolled_with_face)
        response = client_with_face.patch(f"{REVIEW}/{row['id']}",
                                          json={"approve": True},
                                          headers=admin.headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "approved"
        assert body["reviewed_by_id"] == admin.id
        assert body["reviewed_at"] is not None
        assert body["used_at"] is None

    def test_a_student_on_no_course_can_still_be_approved(self, client_with_face,
                                                          make_actor, admin):
        """The case that motivated admin-only review: nobody teaches them."""
        loner = make_actor("student").grant_consent()
        assert client_with_face.post("/users/me/face/enroll",
                                     json={"image": FAKE_IMAGE},
                                     headers=loner.headers).status_code == 200
        row = ask(client_with_face, loner, "Lost my old phone")
        queue = client_with_face.get(REVIEW, headers=admin.headers).json()["items"]
        assert row["id"] in [r["id"] for r in queue]
        response = client_with_face.patch(f"{REVIEW}/{row['id']}",
                                          json={"approve": True},
                                          headers=admin.headers)
        assert response.status_code == 200, response.text

    def test_an_instructor_who_teaches_this_student_nothing_is_refused(
            self, client_with_face, enrolled_with_face, make_actor):
        """A hidden button is not a secure endpoint: the scope is enforced on
        the server, so an instructor cannot authorise a template replacement
        for a student who is not theirs by calling the API directly."""
        row = ask(client_with_face, enrolled_with_face)
        outsider = make_actor("instructor")
        response = client_with_face.patch(f"{REVIEW}/{row['id']}",
                                          json={"approve": True},
                                          headers=outsider.headers)
        assert response.status_code == 403, response.text

    def test_a_ta_may_not_decide(self, client_with_face, enrolled_with_face,
                                 make_actor):
        """A TA can see flagged check-ins but not resolve them, and the same
        line is drawn here: this replaces the biometric reference every later
        check-in is matched against."""
        row = ask(client_with_face, enrolled_with_face)
        ta = make_actor("ta")
        response = client_with_face.patch(f"{REVIEW}/{row['id']}",
                                          json={"approve": True},
                                          headers=ta.headers)
        assert response.status_code == 403, response.text

    def test_a_student_may_not_decide(self, client_with_face, enrolled_with_face):
        row = ask(client_with_face, enrolled_with_face)
        response = client_with_face.patch(f"{REVIEW}/{row['id']}",
                                          json={"approve": True},
                                          headers=enrolled_with_face.headers)
        assert response.status_code == 403, response.text

    def test_nobody_decides_their_own(self, client_with_face, make_actor):
        """An admin is in scope for every student, including themselves, so
        without this check an admin with a template could authorise replacing
        it and the approval would mean nothing.

        An instructor cannot reach this state by another route: a student
        enrolment is refused for a non-student account, so
        ``visible_student_ids`` never contains an instructor and the scope
        check refuses them one line earlier.
        """
        boss = make_actor("admin").grant_consent()
        assert client_with_face.post("/users/me/face/enroll",
                                     json={"image": FAKE_IMAGE},
                                     headers=boss.headers).status_code == 200

        row = ask(client_with_face, boss)
        response = client_with_face.patch(f"{REVIEW}/{row['id']}",
                                          json={"approve": True},
                                          headers=boss.headers)
        assert response.status_code == 403, response.text
        assert "your own" in response.json()["message"].lower()

    def test_an_instructor_is_out_of_scope_for_another_instructor(
            self, client_with_face, make_actor):
        """Nobody is "one of yours" unless they are an enrolled student of a
        course you teach, so staff cannot approve each other."""
        one = make_actor("instructor").grant_consent()
        assert client_with_face.post("/users/me/face/enroll",
                                     json={"image": FAKE_IMAGE},
                                     headers=one.headers).status_code == 200
        row = ask(client_with_face, one)

        two = make_actor("instructor")
        response = client_with_face.patch(f"{REVIEW}/{row['id']}",
                                          json={"approve": True},
                                          headers=two.headers)
        assert response.status_code == 403, response.text

    def test_an_unknown_request_is_a_404(self, client_with_face, admin):
        response = client_with_face.patch(f"{REVIEW}/does-not-exist",
                                          json={"approve": True},
                                          headers=admin.headers)
        assert response.status_code == 404, response.text


class TestTheQueue:
    def test_the_course_instructor_may_not_read_it(self, client_with_face,
                                                   enrolled_with_face,
                                                   assigned_instructor):
        ask(client_with_face, enrolled_with_face)
        queue = client_with_face.get(REVIEW, headers=assigned_instructor.headers)
        assert queue.status_code == 403, queue.text

    def test_any_instructor_may_not_read_it(self, client_with_face,
                                            enrolled_with_face, instructor):
        ask(client_with_face, enrolled_with_face)
        queue = client_with_face.get(REVIEW, headers=instructor.headers)
        assert queue.status_code == 403, queue.text

    def test_an_admin_sees_everything(self, client_with_face, enrolled_with_face,
                                      admin):
        row = ask(client_with_face, enrolled_with_face)
        queue = client_with_face.get(REVIEW, headers=admin.headers)
        assert [r["id"] for r in queue.json()["items"]] == [row["id"]]

    def test_a_ta_may_not_read_it(self, client_with_face,
                                  enrolled_with_face, make_actor):
        ask(client_with_face, enrolled_with_face)
        ta = make_actor("ta")
        queue = client_with_face.get(REVIEW, headers=ta.headers)
        assert queue.status_code == 403, queue.text

    def test_a_student_may_not_read_it(self, client_with_face,
                                       enrolled_with_face):
        response = client_with_face.get(REVIEW, headers=enrolled_with_face.headers)
        assert response.status_code == 403, response.text

    def test_the_status_filter_narrows_it(self, client_with_face,
                                          enrolled_with_face, admin):
        row = ask(client_with_face, enrolled_with_face)
        assert len(client_with_face.get(f"{REVIEW}?status=pending",
                                        headers=admin.headers).json()["items"]) == 1
        assert client_with_face.get(f"{REVIEW}?status=approved",
                                    headers=admin.headers).json()["items"] == []

        client_with_face.patch(f"{REVIEW}/{row['id']}", json={"approve": True},
                               headers=admin.headers)
        assert client_with_face.get(f"{REVIEW}?status=pending",
                                    headers=admin.headers).json()["items"] == []
        assert len(client_with_face.get(f"{REVIEW}?status=approved",
                                        headers=admin.headers).json()["items"]) == 1

    def test_a_nonsense_status_is_a_422(self, client_with_face, admin):
        assert client_with_face.get(f"{REVIEW}?status=banana",
                                    headers=admin.headers).status_code == 422


class TestDecisionsAreFinal:
    def test_an_approval_cannot_be_approved_twice(self, client_with_face,
                                                  enrolled_with_face, admin):
        row = ask(client_with_face, enrolled_with_face)
        assert client_with_face.patch(f"{REVIEW}/{row['id']}", json={"approve": True},
                                      headers=admin.headers).status_code == 200
        again = client_with_face.patch(f"{REVIEW}/{row['id']}", json={"approve": True},
                                       headers=admin.headers)
        assert again.status_code == 400, again.text
        assert "already approved" in again.json()["message"].lower()

    def test_an_approval_cannot_be_withdrawn(self, client_with_face,
                                             enrolled_with_face, admin):
        """Otherwise an approval could be revoked after the student had
        already spent it, and the record would no longer explain the
        replacement that happened."""
        row = ask(client_with_face, enrolled_with_face)
        client_with_face.patch(f"{REVIEW}/{row['id']}", json={"approve": True},
                               headers=admin.headers)
        reversal = client_with_face.patch(f"{REVIEW}/{row['id']}",
                                          json={"approve": False},
                                          headers=admin.headers)
        assert reversal.status_code == 400, reversal.text

    def test_a_denial_cannot_be_reopened(self, client_with_face,
                                         enrolled_with_face, admin):
        """The student asks again instead, which leaves a new record."""
        row = ask(client_with_face, enrolled_with_face)
        client_with_face.patch(f"{REVIEW}/{row['id']}", json={"approve": False},
                               headers=admin.headers)
        reversal = client_with_face.patch(f"{REVIEW}/{row['id']}",
                                          json={"approve": True},
                                          headers=admin.headers)
        assert reversal.status_code == 400, reversal.text

    def test_spending_an_approval_marks_it_used_without_changing_its_status(
            self, client_with_face, enrolled_with_face, admin):
        """``used_at`` records the consumption; the status stays ``approved``,
        because it was."""
        row = ask(client_with_face, enrolled_with_face)
        client_with_face.patch(f"{REVIEW}/{row['id']}", json={"approve": True},
                               headers=admin.headers)
        assert client_with_face.put("/users/me/face", json={"image": FAKE_IMAGE},
                                    headers=enrolled_with_face.headers
                                    ).status_code == 200

        mine = client_with_face.get(REQUESTS,
                                    headers=enrolled_with_face.headers).json()
        spent = next(r for r in mine["items"] if r["id"] == row["id"])
        assert spent["status"] == "approved"
        assert spent["used_at"] is not None