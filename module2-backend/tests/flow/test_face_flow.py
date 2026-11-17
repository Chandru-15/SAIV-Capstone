"""Face enrolment and the module 3 integration, end to end.

These run against a stubbed module 3 (see the ``face_stub`` fixture) so they
are deterministic and need no MediaPipe container — but every line of module
2's own client, service and controller code is real.
"""

import pytest

from tests.conftest import DEFAULT_PASSWORD, FAKE_HASH, FAKE_IMAGE

pytestmark = pytest.mark.flow


def actor_with_consent(client, make_actor, camera=True, geolocation=True):
    return make_actor("student").grant_consent(camera=camera, geolocation=geolocation)


class TestEnrolment:
    def test_enrol_stores_only_the_hash(self, client_with_face, make_actor, db):
        """The image must not survive the request. Only the SHA-256 template
        hash module 3 returns is persisted."""
        from app.models import User

        student = actor_with_consent(client_with_face, make_actor)
        response = client_with_face.post("/users/me/face/enroll",
                                         json={"image": FAKE_IMAGE},
                                         headers=student.headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body == {"success": True, "message": "Face enrolled successfully",
                        "face_enrolled": True, "quality_score": 0.88}

        row = db.get(User, student.id)
        assert row.face_enrolled is True
        assert row.face_embedding_hash == FAKE_HASH
        assert FAKE_IMAGE not in str(row.__dict__)

    def test_enrolment_is_reflected_on_the_user(self, client_with_face, make_actor):
        student = actor_with_consent(client_with_face, make_actor)
        client_with_face.post("/users/me/face/enroll", json={"image": FAKE_IMAGE},
                              headers=student.headers)
        me = client_with_face.get("/auth/me", headers=student.headers).json()
        assert me["face_enrolled"] is True
        # The hash is stored but never returned.
        assert "face_embedding_hash" not in me

    def test_enrolment_requires_camera_consent(self, client_with_face, make_actor):
        """The image should not even leave this process without consent."""
        student = make_actor("student")  # no consent granted
        response = client_with_face.post("/users/me/face/enroll",
                                         json={"image": FAKE_IMAGE},
                                         headers=student.headers)
        assert response.status_code == 400
        assert "consent" in response.json()["message"].lower()

    def test_no_consent_means_module3_is_never_called(self, client_with_face,
                                                      make_actor, face_stub):
        student = make_actor("student")
        client_with_face.post("/users/me/face/enroll", json={"image": FAKE_IMAGE},
                              headers=student.headers)
        assert "/face/enroll" not in face_stub["calls"]

    def test_data_url_prefix_is_accepted(self, client_with_face, make_actor):
        student = actor_with_consent(client_with_face, make_actor)
        response = client_with_face.post(
            "/users/me/face/enroll",
            json={"image": f"data:image/png;base64,{FAKE_IMAGE}"},
            headers=student.headers)
        assert response.status_code == 200

    def test_no_face_detected_is_a_400(self, client_with_face, make_actor, face_stub):
        face_stub["enroll_status"] = 400
        face_stub["enroll_body"] = {"detail": "No face detected"}
        student = actor_with_consent(client_with_face, make_actor)
        response = client_with_face.post("/users/me/face/enroll",
                                         json={"image": FAKE_IMAGE},
                                         headers=student.headers)
        assert response.status_code == 422
        assert response.json()["message"] == "No face detected"

    def test_module3_outage_is_a_503(self, client_with_face, make_actor, face_stub):
        """The API spec requires 503 when the face service is unavailable —
        not a 500, because the backend is fine and a retry may work."""
        face_stub["down"] = True
        student = actor_with_consent(client_with_face, make_actor)
        response = client_with_face.post("/users/me/face/enroll",
                                         json={"image": FAKE_IMAGE},
                                         headers=student.headers)
        assert response.status_code == 503
        assert response.json()["code"] == "service_unavailable"

    def test_malformed_hash_from_module3_is_refused(self, client_with_face,
                                                    make_actor, face_stub, db):
        """The column has CHECK (char_length = 64). A short hash must fail
        cleanly rather than as an IntegrityError."""
        from app.models import User

        face_stub["enroll_body"] = {"enrollment_successful": True,
                                    "face_template_hash": "tooshort",
                                    "quality_score": 0.9}
        student = actor_with_consent(client_with_face, make_actor)
        response = client_with_face.post("/users/me/face/enroll",
                                         json={"image": FAKE_IMAGE},
                                         headers=student.headers)
        assert response.status_code == 503
        assert db.get(User, student.id).face_enrolled is False

    def test_enrolment_is_audited(self, client_with_face, admin, make_actor):
        student = actor_with_consent(client_with_face, make_actor)
        client_with_face.post("/users/me/face/enroll", json={"image": FAKE_IMAGE},
                              headers=student.headers)
        logs = client_with_face.get(f"/audit/logs?user_id={student.id}",
                                    headers=admin.headers).json()
        assert "face_enrolled" in {entry["action"] for entry in logs["items"]}

    def test_anonymous_enrolment_is_refused(self, client_with_face):
        assert client_with_face.post("/users/me/face/enroll",
                                     json={"image": FAKE_IMAGE}).status_code == 401


class TestWithdrawal:
    def test_unenrol_clears_the_template(self, client_with_face, make_actor, db):
        from app.models import User

        student = actor_with_consent(client_with_face, make_actor)
        client_with_face.post("/users/me/face/enroll", json={"image": FAKE_IMAGE},
                              headers=student.headers)
        response = client_with_face.delete("/users/me/face/", headers=student.headers)
        assert response.status_code == 200
        row = db.get(User, student.id)
        assert row.face_enrolled is False and row.face_embedding_hash is None

    def test_withdrawing_camera_consent_also_clears_enrolment(self, client_with_face,
                                                              make_actor, db):
        """Retaining a biometric template after consent is withdrawn is exactly
        what the privacy requirements forbid."""
        from app.models import User

        student = actor_with_consent(client_with_face, make_actor)
        client_with_face.post("/users/me/face/enroll", json={"image": FAKE_IMAGE},
                              headers=student.headers)
        client_with_face.patch("/auth/me", json={"camera_consent": False},
                               headers=student.headers)
        row = db.get(User, student.id)
        assert row.face_enrolled is False and row.face_embedding_hash is None


class TestVerificationEndpoints:
    def test_verify_against_the_enrolled_template(self, client_with_face, make_actor):
        student = actor_with_consent(client_with_face, make_actor)
        client_with_face.post("/users/me/face/enroll", json={"image": FAKE_IMAGE},
                              headers=student.headers)
        response = client_with_face.post("/users/me/face/verify",
                                         json={"image": FAKE_IMAGE},
                                         headers=student.headers)
        assert response.status_code == 200
        assert response.json() == {"match_passed": True, "match_score": 0.84,
                                   "match_threshold": 0.70, "face_detected": True}

    def test_verify_before_enrolling_is_a_400(self, client_with_face, make_actor):
        student = actor_with_consent(client_with_face, make_actor)
        response = client_with_face.post("/users/me/face/verify",
                                         json={"image": FAKE_IMAGE},
                                         headers=student.headers)
        assert response.status_code == 400
        assert "not enrolled" in response.json()["message"]

    def test_standalone_liveness_check(self, client_with_face, make_actor):
        student = actor_with_consent(client_with_face, make_actor)
        response = client_with_face.post("/users/me/face/liveness",
                                         json={"image": FAKE_IMAGE,
                                               "challenge_type": "passive"},
                                         headers=student.headers)
        assert response.status_code == 200
        assert response.json()["liveness_passed"] is True

    def test_status_reports_module3_reachability(self, client_with_face,
                                                 make_actor, face_stub):
        student = actor_with_consent(client_with_face, make_actor)
        assert client_with_face.get("/users/me/face/status",
                                    headers=student.headers).json()["available"] is True
        face_stub["down"] = True
        assert client_with_face.get("/users/me/face/status",
                                    headers=student.headers).json()["available"] is False


class TestCheckInUsesModule3:
    """The integration that matters: the backend derives the biometric scores
    itself instead of believing the client."""

    @pytest.fixture
    def enrolled(self, client_with_face, admin, make_actor, course):
        student = actor_with_consent(client_with_face, make_actor)
        client_with_face.post("/admin/enrollments/",
                              json={"student_id": student.id, "course_id": course["id"]},
                              headers=admin.headers)
        client_with_face.post("/users/me/face/enroll", json={"image": FAKE_IMAGE},
                              headers=student.headers)
        return student

    def test_image_triggers_a_liveness_call(self, client_with_face, enrolled,
                                            make_session, at_venue, face_stub):
        session = make_session()
        face_stub["calls"].clear()
        response = client_with_face.post("/checkins/", json={
            "session_id": session["id"], "image": FAKE_IMAGE,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert response.status_code == 201, response.text
        assert "/liveness/check" in face_stub["calls"]
        assert response.json()["liveness_score"] == 0.93

    def test_client_supplied_scores_are_discarded_when_an_image_is_sent(
        self, client_with_face, enrolled, make_session, at_venue, face_stub
    ):
        """The core trust fix. A student claiming liveness_score 1.0 must not
        be believed — the score comes from module 3 or not at all."""
        face_stub["liveness_body"] = {**face_stub["liveness_body"],
                                      "liveness_passed": False,
                                      "liveness_score": 0.05}
        response = client_with_face.post("/checkins/", json={
            "session_id": make_session()["id"], "image": FAKE_IMAGE,
            "liveness_passed": True, "liveness_score": 1.0,   # the lie
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert response.status_code == 201
        body = response.json()
        assert body["liveness_score"] == 0.05, "client's claim must be ignored"
        assert body["status"] == "rejected"

    def test_face_match_runs_when_the_session_requires_it(
        self, client_with_face, enrolled, make_session, at_venue, face_stub
    ):
        session = make_session(require_face_match=True)
        face_stub["calls"].clear()
        response = client_with_face.post("/checkins/", json={
            "session_id": session["id"], "image": FAKE_IMAGE,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert response.status_code == 201
        assert "/face/verify" in face_stub["calls"]
        assert response.json()["face_match_score"] == 0.84

    def test_face_match_is_skipped_when_not_required(
        self, client_with_face, enrolled, make_session, at_venue, face_stub
    ):
        """Avoids a second MediaPipe round trip for every check-in in a course
        that does not use face matching."""
        make_session()
        face_stub["calls"].clear()
        client_with_face.post("/checkins/", json={
            "session_id": make_session()["id"], "image": FAKE_IMAGE,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert "/face/verify" not in face_stub["calls"]

    def test_the_stored_hash_comes_from_module3(self, client_with_face, enrolled,
                                                make_session, at_venue, db):
        from app.models import CheckIn

        created = client_with_face.post("/checkins/", json={
            "session_id": make_session()["id"], "image": FAKE_IMAGE,
            "face_embedding_hash": "9" * 64,          # client-supplied, must lose
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers).json()
        row = db.get(CheckIn, created["id"])
        assert row.face_embedding_hash == FAKE_HASH

    def test_a_poor_liveness_score_flags_the_check_in(
        self, client_with_face, enrolled, make_session, at_venue, face_stub
    ):
        # Module 3 is above its own 0.60 threshold only in `liveness_passed`;
        # the backend's risk fusion weighs the *score*, so a weak pass still
        # earns human review. 0.20 puts the fused score at 0.53, over the
        # course threshold of 0.50.
        face_stub["liveness_body"] = {**face_stub["liveness_body"],
                                      "liveness_passed": True,
                                      "liveness_score": 0.20}
        body = client_with_face.post("/checkins/", json={
            "session_id": make_session()["id"], "image": FAKE_IMAGE,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers).json()
        assert body["liveness_score"] == 0.20
        assert body["status"] == "flagged"

    def test_module3_outage_flags_rather_than_blocking_attendance(
        self, client_with_face, enrolled, make_session, at_venue, face_stub
    ):
        """Failing closed would lock a whole lecture theatre out of attendance
        because one container died. Failing open would wave through exactly the
        submissions the service exists to catch. Flag, and let a human decide.
        """
        face_stub["down"] = True
        response = client_with_face.post("/checkins/", json={
            "session_id": make_session()["id"], "image": FAKE_IMAGE,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert response.status_code == 201, "an outage must not block check-in"
        body = response.json()
        assert body["status"] == "flagged"
        assert body["liveness_score"] is None

    def test_no_camera_consent_means_no_module3_call(
        self, client_with_face, admin, make_actor, course, make_session,
        at_venue, face_stub
    ):
        student = actor_with_consent(client_with_face, make_actor,
                                     camera=False, geolocation=True)
        client_with_face.post("/admin/enrollments/",
                              json={"student_id": student.id, "course_id": course["id"]},
                              headers=admin.headers)
        face_stub["calls"].clear()
        response = client_with_face.post("/checkins/", json={
            "session_id": make_session()["id"], "image": FAKE_IMAGE,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=student.headers)
        assert response.status_code == 201
        assert face_stub["calls"] == [], "the image must not leave without consent"
        assert response.json()["liveness_score"] is None

    def test_submission_without_an_image_leaves_liveness_unverified(
        self, client_with_face, enrolled, make_session, face_stub
    ):
        """The client-supplied biometric path was removed.

        With no image there is nothing for module 3 to judge, so a session that
        requires liveness has an unmet requirement and the check-in is flagged
        rather than approved. Module 3 is not called at all.
        """
        face_stub["calls"].clear()
        response = client_with_face.post("/checkins/", json={
            "session_id": make_session()["id"],
            "latitude": 1.2966, "longitude": 103.7764,
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert response.status_code == 201
        assert face_stub["calls"] == [], "no image means no call to module 3"
        body = response.json()
        assert body["liveness_score"] is None
        assert body["status"] == "flagged"


OTHER_HASH = "a" * 64


def approve_reenrolment(client, student, admin, reason=None):
    """Student asks, admin approves. Returns the request id.

    An admin rather than an instructor on purpose: ``visible_student_ids``
    hands an admin every student, so these tests exercise the re-enrolment
    itself and leave the scoping rules to test_face_reenroll_flow.py.
    """
    created = client.post("/users/me/face/reenroll-requests",
                          json={} if reason is None else {"reason": reason},
                          headers=student.headers)
    assert created.status_code == 201, created.text
    decided = client.patch(f"/face-reenroll-requests/{created.json()['id']}",
                           json={"approve": True}, headers=admin.headers)
    assert decided.status_code == 200, decided.text
    return created.json()["id"]


class TestReEnrolment:
    """``PUT /users/me/face`` replaces a template that already exists.

    Kept apart from ``POST /enroll`` because discarding a working template is a
    different act from creating the first one, and because a bad enrolment is
    otherwise permanent: the student's own face stops matching and there is no
    way back.
    """

    def enrolled(self, client, make_actor):
        student = actor_with_consent(client, make_actor)
        client.post("/users/me/face/enroll", json={"image": FAKE_IMAGE},
                    headers=student.headers)
        return student

    def test_put_replaces_the_stored_template(self, client_with_face, make_actor,
                                              admin, db, face_stub):
        from app.models import User

        student = self.enrolled(client_with_face, make_actor)
        assert db.get(User, student.id).face_embedding_hash == FAKE_HASH
        approve_reenrolment(client_with_face, student, admin)

        face_stub["enroll_body"] = {**face_stub["enroll_body"],
                                    "face_template_hash": OTHER_HASH}
        response = client_with_face.put("/users/me/face",
                                        json={"image": FAKE_IMAGE},
                                        headers=student.headers)
        assert response.status_code == 200, response.text
        assert response.json() == {
            "success": True, "message": "Face re-enrolled successfully",
            "face_enrolled": True, "quality_score": 0.88,
            "previous_template_replaced": True,
        }
        db.expire_all()
        assert db.get(User, student.id).face_embedding_hash == OTHER_HASH

    def test_re_enrolment_requires_an_existing_template(self, client_with_face,
                                                        make_actor, face_stub):
        """Nothing to replace yet, so this is the wrong endpoint — and module 3
        should not be handed an image to prove it."""
        student = actor_with_consent(client_with_face, make_actor)
        response = client_with_face.put("/users/me/face",
                                        json={"image": FAKE_IMAGE},
                                        headers=student.headers)
        assert response.status_code == 400
        assert "not enrolled" in response.json()["message"].lower() \
            or "no face is enrolled" in response.json()["message"].lower()
        assert "/face/enroll" not in face_stub["calls"]

    def test_a_rejected_photo_leaves_the_old_template_in_place(
            self, client_with_face, make_actor, admin, db, face_stub):
        """A failed retry must not strand the student with no usable enrolment."""
        from app.models import User

        student = self.enrolled(client_with_face, make_actor)
        approve_reenrolment(client_with_face, student, admin)
        face_stub["enroll_status"] = 400
        face_stub["enroll_body"] = {"detail": "No face detected"}

        response = client_with_face.put("/users/me/face",
                                        json={"image": FAKE_IMAGE},
                                        headers=student.headers)
        assert response.status_code == 422
        db.expire_all()
        row = db.get(User, student.id)
        assert row.face_enrolled is True
        assert row.face_embedding_hash == FAKE_HASH

    def test_re_enrolment_has_its_own_audit_action(self, client_with_face, admin,
                                                   make_actor):
        """So that a re-enrolment just before a check-in is visible to review
        rather than indistinguishable from the original enrolment."""
        student = self.enrolled(client_with_face, make_actor)
        approve_reenrolment(client_with_face, student, admin)
        client_with_face.put("/users/me/face", json={"image": FAKE_IMAGE},
                             headers=student.headers)
        logs = client_with_face.get(f"/audit/logs?user_id={student.id}",
                                    headers=admin.headers).json()
        assert "face_re_enrolled" in {entry["action"] for entry in logs["items"]}

    def test_the_previous_template_hash_is_never_logged(self, client_with_face,
                                                        admin, make_actor):
        """A template hash is biometric data. Writing the old one into an
        append-only audit log would keep a copy ``unenroll`` cannot erase."""
        student = self.enrolled(client_with_face, make_actor)
        approve_reenrolment(client_with_face, student, admin)
        client_with_face.put("/users/me/face", json={"image": FAKE_IMAGE},
                             headers=student.headers)
        logs = client_with_face.get(f"/audit/logs?user_id={student.id}",
                                    headers=admin.headers).json()
        assert FAKE_HASH not in str(logs)

    def test_anonymous_re_enrolment_is_refused(self, client_with_face):
        assert client_with_face.put(
            "/users/me/face", json={"image": FAKE_IMAGE},
        ).status_code == 401

    def test_a_student_cannot_re_enrol_someone_else(self, client_with_face,
                                                    make_actor, admin, db):
        """The route is scoped to /users/me; there is deliberately no way to
        submit a photo on another person's behalf. Even an approval granted to
        the victim does nothing for the attacker: the server looks up the
        CALLER's own unspent approval and never takes an id from the client."""
        from app.models import User

        victim = self.enrolled(client_with_face, make_actor)
        approve_reenrolment(client_with_face, victim, admin)
        attacker = actor_with_consent(client_with_face, make_actor)
        client_with_face.put("/users/me/face", json={"image": FAKE_IMAGE},
                             headers=attacker.headers)
        db.expire_all()
        assert db.get(User, victim.id).face_embedding_hash == FAKE_HASH


class TestReEnrolmentNeedsAnApproval:
    """Re-enrolment is authorised by staff, not by the account holder.

    Replacing the template destroys the only biometric reference the system
    holds and installs the one every later check-in is matched against. The
    password this endpoint used to demand proved the account holder was
    present — which, for their own account, they always are — so it never
    proved that anybody else agreed. An instructor or admin now decides, and
    the decision is spent by exactly one replacement.
    """

    @staticmethod
    def enrolled(client, make_actor):
        student = actor_with_consent(client, make_actor)
        assert client.post("/users/me/face/enroll", json={"image": FAKE_IMAGE},
                           headers=student.headers).status_code == 200
        return student

    def test_without_an_approval_it_is_refused_and_changes_nothing(
            self, client_with_face, make_actor, db, face_stub):
        from app.models import User

        student = self.enrolled(client_with_face, make_actor)
        face_stub["calls"].clear()

        response = client_with_face.put("/users/me/face",
                                        json={"image": FAKE_IMAGE},
                                        headers=student.headers)

        assert response.status_code == 403, response.text
        db.expire_all()
        assert db.get(User, student.id).face_embedding_hash == FAKE_HASH
        # The photo must not reach module 3 either: authority is settled
        # first, so an unauthorised attempt costs nothing downstream.
        assert face_stub["calls"] == []

    def test_the_password_no_longer_buys_a_re_enrolment(self, client_with_face,
                                                        make_actor, db):
        """The old step-up check is gone. Knowing the password — which the
        student always does — must not be a way back in, and sending one must
        not be mistaken for authority."""
        from app.models import User

        student = self.enrolled(client_with_face, make_actor)
        response = client_with_face.put(
            "/users/me/face",
            json={"image": FAKE_IMAGE, "password": DEFAULT_PASSWORD},
            headers=student.headers)
        assert response.status_code == 403, response.text
        db.expire_all()
        assert db.get(User, student.id).face_embedding_hash == FAKE_HASH

    def test_a_pending_request_authorises_nothing(self, client_with_face,
                                                  make_actor):
        student = self.enrolled(client_with_face, make_actor)
        assert client_with_face.post("/users/me/face/reenroll-requests", json={},
                                     headers=student.headers).status_code == 201
        assert client_with_face.put("/users/me/face", json={"image": FAKE_IMAGE},
                                    headers=student.headers).status_code == 403

    def test_a_denied_request_authorises_nothing(self, client_with_face,
                                                 make_actor, admin, db):
        from app.models import User

        student = self.enrolled(client_with_face, make_actor)
        created = client_with_face.post("/users/me/face/reenroll-requests",
                                        json={}, headers=student.headers)
        denied = client_with_face.patch(
            f"/face-reenroll-requests/{created.json()['id']}",
            json={"approve": False, "notes": "Come and see me first"},
            headers=admin.headers)
        assert denied.status_code == 200, denied.text

        response = client_with_face.put("/users/me/face", json={"image": FAKE_IMAGE},
                                        headers=student.headers)
        assert response.status_code == 403, response.text
        db.expire_all()
        assert db.get(User, student.id).face_embedding_hash == FAKE_HASH

    def test_an_approval_is_single_use(self, client_with_face, make_actor, admin,
                                       db, face_stub):
        """One decision buys exactly one template."""
        from app.models import User

        student = self.enrolled(client_with_face, make_actor)
        approve_reenrolment(client_with_face, student, admin)
        face_stub["enroll_body"] = {**face_stub["enroll_body"],
                                    "face_template_hash": OTHER_HASH}

        first = client_with_face.put("/users/me/face", json={"image": FAKE_IMAGE},
                                     headers=student.headers)
        assert first.status_code == 200, first.text
        second = client_with_face.put("/users/me/face", json={"image": FAKE_IMAGE},
                                      headers=student.headers)
        assert second.status_code == 403, second.text
        db.expire_all()
        assert db.get(User, student.id).face_embedding_hash == OTHER_HASH

    def test_a_rejected_photo_does_not_spend_the_approval(
            self, client_with_face, make_actor, admin, db, face_stub):
        """A capture module 3 refuses must not cost the student the approval:
        they would have to go back to an instructor for a retry."""
        from app.models import User

        student = self.enrolled(client_with_face, make_actor)
        approve_reenrolment(client_with_face, student, admin)
        working_status, working_body = face_stub["enroll_status"], face_stub["enroll_body"]

        face_stub["enroll_status"] = 400
        face_stub["enroll_body"] = {"detail": "No face detected"}
        assert client_with_face.put("/users/me/face", json={"image": FAKE_IMAGE},
                                    headers=student.headers).status_code == 422

        face_stub["enroll_status"] = working_status
        face_stub["enroll_body"] = {**working_body, "face_template_hash": OTHER_HASH}
        retry = client_with_face.put("/users/me/face", json={"image": FAKE_IMAGE},
                                     headers=student.headers)
        assert retry.status_code == 200, retry.text
        db.expire_all()
        assert db.get(User, student.id).face_embedding_hash == OTHER_HASH

    def test_the_whole_trail_is_audited(self, client_with_face, make_actor, admin):
        """request -> approval -> replacement, so a template replaced just
        before a check-in can be traced to whoever allowed it."""
        student = self.enrolled(client_with_face, make_actor)
        approve_reenrolment(client_with_face, student, admin)
        client_with_face.put("/users/me/face", json={"image": FAKE_IMAGE},
                             headers=student.headers)

        student_logs = client_with_face.get(f"/audit/logs?user_id={student.id}",
                                            headers=admin.headers).json()
        assert {"face_re_enroll_requested", "face_re_enrolled"} <= {
            entry["action"] for entry in student_logs["items"]}
        # The decision is recorded against the REVIEWER, which is what makes
        # "who allowed this" answerable.
        admin_logs = client_with_face.get(f"/audit/logs?user_id={admin.id}",
                                          headers=admin.headers).json()
        assert "face_re_enroll_approved" in {
            entry["action"] for entry in admin_logs["items"]}


class TestEnrolmentPoseGate:
    """Module 2 re-checks the head pose module 3 reports.

    Module 3 refuses a turned head itself, but this module owns what reaches
    ``users.face_embedding_hash``: a face service that is older, misconfigured
    or swapped out must not be able to widen the gate silently. A template
    captured off-axis locks the student out rather than letting anyone in,
    because their own frontal face then scores below the match threshold.
    """

    def with_yaw(self, face_stub, yaw):
        face_stub["enroll_body"] = {
            **face_stub["enroll_body"],
            "details": {"face_detected": True, "yaw": yaw},
        }

    def test_a_turned_head_is_rejected(self, client_with_face, make_actor, db,
                                       face_stub):
        from app.models import User

        self.with_yaw(face_stub, 0.45)
        student = actor_with_consent(client_with_face, make_actor)
        response = client_with_face.post("/users/me/face/enroll",
                                         json={"image": FAKE_IMAGE},
                                         headers=student.headers)
        assert response.status_code == 422
        assert "turned" in response.json()["message"].lower()
        assert db.get(User, student.id).face_enrolled is False

    def test_the_sign_of_the_turn_does_not_matter(self, client_with_face,
                                                  make_actor, face_stub):
        self.with_yaw(face_stub, -0.45)
        student = actor_with_consent(client_with_face, make_actor)
        assert client_with_face.post("/users/me/face/enroll",
                                     json={"image": FAKE_IMAGE},
                                     headers=student.headers).status_code == 422

    def test_a_forward_facing_capture_is_accepted(self, client_with_face,
                                                  make_actor, face_stub):
        self.with_yaw(face_stub, 0.03)
        student = actor_with_consent(client_with_face, make_actor)
        assert client_with_face.post("/users/me/face/enroll",
                                     json={"image": FAKE_IMAGE},
                                     headers=student.headers).status_code == 200

    def test_the_boundary_itself_is_allowed(self, client_with_face, make_actor,
                                            face_stub):
        """0.10 is the documented limit, so it must not be off by one."""
        from app.services.face_service import MAX_ENROLL_YAW

        self.with_yaw(face_stub, MAX_ENROLL_YAW)
        student = actor_with_consent(client_with_face, make_actor)
        assert client_with_face.post("/users/me/face/enroll",
                                     json={"image": FAKE_IMAGE},
                                     headers=student.headers).status_code == 200

    def test_a_service_that_does_not_report_pose_is_left_to_its_own_gate(
            self, client_with_face, make_actor, face_stub):
        """Failing closed here would break enrolment entirely against a module 3
        that predates the pose check."""
        face_stub["enroll_body"] = {**face_stub["enroll_body"],
                                    "details": {"face_detected": True}}
        student = actor_with_consent(client_with_face, make_actor)
        assert client_with_face.post("/users/me/face/enroll",
                                     json={"image": FAKE_IMAGE},
                                     headers=student.headers).status_code == 200

    def test_an_unreadable_yaw_does_not_break_enrolment(self, client_with_face,
                                                        make_actor, face_stub):
        self.with_yaw(face_stub, "sideways")
        student = actor_with_consent(client_with_face, make_actor)
        assert client_with_face.post("/users/me/face/enroll",
                                     json={"image": FAKE_IMAGE},
                                     headers=student.headers).status_code == 200

    def test_the_refusal_is_audited_as_a_failure(self, client_with_face, admin,
                                                 make_actor, face_stub):
        self.with_yaw(face_stub, 0.45)
        student = actor_with_consent(client_with_face, make_actor)
        client_with_face.post("/users/me/face/enroll", json={"image": FAKE_IMAGE},
                              headers=student.headers)
        logs = client_with_face.get(
            f"/audit/logs?user_id={student.id}&action=face_enrolled",
            headers=admin.headers).json()
        assert logs["items"], "the refused attempt should still be recorded"
        assert all(entry["success"] is False for entry in logs["items"])

    def test_re_enrolment_is_gated_too(self, client_with_face, make_actor, admin,
                                       db, face_stub):
        """Otherwise the pose check is one PUT away from being bypassed — an
        approved request authorises a replacement, not a bad one."""
        from app.models import User

        student = actor_with_consent(client_with_face, make_actor)
        client_with_face.post("/users/me/face/enroll", json={"image": FAKE_IMAGE},
                              headers=student.headers)
        approve_reenrolment(client_with_face, student, admin)

        self.with_yaw(face_stub, 0.50)
        face_stub["enroll_body"] = {**face_stub["enroll_body"],
                                    "face_template_hash": OTHER_HASH}
        response = client_with_face.put("/users/me/face",
                                        json={"image": FAKE_IMAGE},
                                        headers=student.headers)
        assert response.status_code == 422
        db.expire_all()
        assert db.get(User, student.id).face_embedding_hash == FAKE_HASH


class TestRefusedEnrolmentIsTraceable:
    """A refused enrolment is recorded on a side session.

    The refusal is logged on the way out of a ``@transactional`` method that
    then raises, and the raise rolls the transaction back. Without an
    independent write, the attempts most worth tracing — someone retrying with
    photo after photo — would leave no trace at all.
    """

    def test_a_photo_module3_refuses_is_still_recorded(self, client_with_face,
                                                       admin, make_actor, face_stub):
        face_stub["enroll_status"] = 400
        face_stub["enroll_body"] = {"detail": "No face detected"}
        student = actor_with_consent(client_with_face, make_actor)
        assert client_with_face.post("/users/me/face/enroll",
                                     json={"image": FAKE_IMAGE},
                                     headers=student.headers).status_code == 422
        logs = client_with_face.get(
            f"/audit/logs?user_id={student.id}&action=face_enrolled",
            headers=admin.headers).json()
        assert logs["items"], "the refusal must outlive the rolled-back transaction"
        entry = logs["items"][0]
        assert entry["success"] is False
        assert "No face detected" in str(entry["details"])

    def test_a_refusal_does_not_leave_the_user_enrolled(self, client_with_face,
                                                        make_actor, db, face_stub):
        from app.models import User

        face_stub["enroll_status"] = 400
        face_stub["enroll_body"] = {"detail": "No face detected"}
        student = actor_with_consent(client_with_face, make_actor)
        client_with_face.post("/users/me/face/enroll", json={"image": FAKE_IMAGE},
                              headers=student.headers)
        db.expire_all()
        row = db.get(User, student.id)
        assert row.face_enrolled is False and row.face_embedding_hash is None
