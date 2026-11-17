"""The check-in pipeline end to end: gates, risk verdicts, review and appeal."""

import json

import pytest

pytestmark = pytest.mark.flow

FAR_AWAY = {"latitude": 1.3521, "longitude": 103.8198}   # ~8 km from the venue


class TestSubmissionGates:
    """Cheap refusals that must happen before any risk work."""

    def test_not_enrolled_is_403(self, client, student, make_session, at_venue):
        session = make_session()
        response = client.post("/checkins/",
                               json={"session_id": session["id"], **at_venue},
                               headers=student.headers)
        assert response.status_code == 403
        assert response.json()["code"] == "permission_denied"

    def test_session_not_active_is_400(self, client, enrolled_student, make_session, at_venue):
        session = make_session(active=False)
        response = client.post("/checkins/",
                               json={"session_id": session["id"], **at_venue},
                               headers=enrolled_student.headers)
        assert response.status_code == 400
        assert "not active" in response.json()["message"]

    def test_window_closed_is_400(self, client, enrolled_student, make_session, at_venue):
        session = make_session(open_window=False)
        response = client.post("/checkins/",
                               json={"session_id": session["id"], **at_venue},
                               headers=enrolled_student.headers)
        assert response.status_code == 400
        assert "window" in response.json()["message"]

    def test_unknown_session_is_404(self, client, enrolled_student, at_venue):
        assert client.post("/checkins/", json={"session_id": "nope", **at_venue},
                           headers=enrolled_student.headers).status_code == 404

    def test_second_check_in_is_409(self, client, enrolled_student, make_session, at_venue):
        session = make_session()
        body = {"session_id": session["id"], **at_venue}
        assert client.post("/checkins/", json=body,
                           headers=enrolled_student.headers).status_code == 201
        duplicate = client.post("/checkins/", json=body, headers=enrolled_student.headers)
        # 400 per the spec: "already checked in" is listed under 400.
        assert duplicate.status_code == 400
        assert duplicate.json()["code"] == "business_rule_violation"

    def test_anonymous_check_in_is_401(self, client, make_session, at_venue):
        assert client.post("/checkins/",
                           json={"session_id": make_session()["id"], **at_venue}).status_code == 401


class TestVerdicts:
    def test_clean_submission_is_approved(self, client, enrolled_student,
                                          make_session, passing_submission):
        response = client.post(
            "/checkins/",
            json=passing_submission(enrolled_student, make_session()["id"]),
            headers=enrolled_student.headers)
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "approved"
        assert body["verified_at"] is not None
        assert body["risk_score"] < 0.5

    def test_a_still_image_alone_is_not_a_clean_submission(
        self, client, enrolled_student, make_session, at_venue
    ):
        """The same payload minus the challenge. `require_liveness_check`
        defaults to true, and a passive verdict does not satisfy it however
        clean everything else is."""
        body = client.post("/checkins/",
                           json={"session_id": make_session()["id"], **at_venue},
                           headers=enrolled_student.headers).json()
        assert body["status"] == "flagged"
        assert "replay_suspected" in json.loads(body["risk_factors"])

    def test_a_rejected_submission_is_still_201(self, client, enrolled_student,
                                                make_session, at_venue, face_stub):
        """approved / flagged / rejected are all successfully *recorded*
        submissions. The verdict lives in the body, not the status code; 4xx is
        reserved for submissions that never became a record."""
        face_stub["liveness_body"] = {**face_stub["liveness_body"],
                                      "liveness_passed": False, "liveness_score": 0.1}
        response = client.post("/checkins/",
                               json={"session_id": make_session()["id"], **at_venue},
                               headers=enrolled_student.headers)
        assert response.status_code == 201
        assert response.json()["status"] == "rejected"

    def test_liveness_failure_is_rejected(self, client, enrolled_student,
                                          make_session, at_venue, face_stub):
        face_stub["liveness_body"] = {**face_stub["liveness_body"],
                                      "liveness_passed": False, "liveness_score": 0.0}
        body = client.post("/checkins/",
                           json={"session_id": make_session()["id"], **at_venue},
                           headers=enrolled_student.headers).json()
        assert body["status"] == "rejected"

    def test_far_outside_the_geofence_is_rejected(self, client, enrolled_student,
                                                  make_session, at_venue):
        """Beyond 2x the fence is not a bad GPS reading, it is somewhere else."""
        payload = {**at_venue, **FAR_AWAY, "session_id": make_session()["id"]}
        body = client.post("/checkins/", json=payload,
                           headers=enrolled_student.headers).json()
        assert body["status"] == "rejected"
        assert body["distance_from_venue_meters"] > 200

    def test_weak_liveness_is_flagged_for_review(self, client, enrolled_student,
                                                 make_session, at_venue, face_stub):
        """Module 3 can pass its own 0.60 bar while still scoring low; the
        backend's weighted score then pushes it over the threshold."""
        face_stub["liveness_body"] = {**face_stub["liveness_body"],
                                      "liveness_passed": True, "liveness_score": 0.25}
        body = client.post("/checkins/",
                           json={"session_id": make_session()["id"], **at_venue},
                           headers=enrolled_student.headers).json()
        assert body["status"] == "flagged"
        assert body["risk_score"] >= 0.5

    def test_distance_is_computed_and_stored(self, client, enrolled_student,
                                             make_session, at_venue):
        body = client.post("/checkins/",
                           json={"session_id": make_session()["id"], **at_venue},
                           headers=enrolled_student.headers).json()
        assert body["distance_from_venue_meters"] == pytest.approx(0, abs=5)

    def test_risk_signals_are_recorded_and_returned_in_detail(self, client, enrolled_student,
                                                              make_session, at_venue, face_stub):
        face_stub["liveness_body"] = {**face_stub["liveness_body"],
                                      "liveness_passed": True, "liveness_score": 0.2}
        created = client.post("/checkins/",
                              json={"session_id": make_session()["id"], **at_venue},
                              headers=enrolled_student.headers).json()
        detail = client.get(f"/checkins/{created['id']}", headers=enrolled_student.headers)
        assert detail.status_code == 200
        types = {s["signal_type"] for s in detail.json()["risk_signals"]}
        # network flags are no longer client-settable, so assert on a
        # signal the server derives for itself.
        assert "device_unknown" in types or "liveness_low_confidence" in types

    def test_retention_deadline_is_set(self, client, enrolled_student, make_session,
                                       at_venue, db):
        """PII must auto-delete after 30 days."""
        from app.models import CheckIn

        created = client.post("/checkins/",
                              json={"session_id": make_session()["id"], **at_venue},
                              headers=enrolled_student.headers).json()
        row = db.get(CheckIn, created["id"])
        assert row.scheduled_deletion_at is not None
        assert 29 <= (row.scheduled_deletion_at - row.checked_in_at).days <= 30


class TestConsentGating:
    def test_without_geolocation_consent_coordinates_are_discarded(
        self, client, admin, make_actor, course, make_session, at_venue
    ):
        actor = make_actor("student").grant_consent(camera=True, geolocation=False)
        client.post("/admin/enrollments/",
                    json={"student_id": actor.id, "course_id": course["id"]},
                    headers=admin.headers)
        body = client.post("/checkins/",
                           json={"session_id": make_session()["id"], **at_venue},
                           headers=actor.headers).json()
        assert body["latitude"] is None
        assert body["longitude"] is None

    def test_withholding_consent_raises_risk_rather_than_lowering_it(
        self, client, admin, make_actor, course, make_session, at_venue, enrolled_student
    ):
        """If a missing signal scored as a pass, withholding consent would be
        the cheapest way to look clean."""
        consenting = client.post("/checkins/",
                                 json={"session_id": make_session()["id"], **at_venue},
                                 headers=enrolled_student.headers).json()

        withholding = make_actor("student").grant_consent(camera=True, geolocation=False)
        client.post("/admin/enrollments/",
                    json={"student_id": withholding.id, "course_id": course["id"]},
                    headers=admin.headers)
        other = client.post("/checkins/",
                            json={"session_id": make_session()["id"], **at_venue},
                            headers=withholding.headers).json()

        assert other["risk_score"] > consenting["risk_score"]


class TestReviewAndAppeal:
    @pytest.fixture
    def flagged(self, client, enrolled_student, make_session, at_venue, face_stub) -> dict:
        """A flagged check-in, produced the only way it can be now: by what
        module 3 returns. A liveness_score in the request body is an unknown
        key and is ignored."""
        face_stub["liveness_body"] = {**face_stub["liveness_body"],
                                      "liveness_passed": True, "liveness_score": 0.25}
        body = client.post("/checkins/",
                           json={"session_id": make_session()["id"], **at_venue},
                           headers=enrolled_student.headers).json()
        assert body["status"] == "flagged", f"fixture must produce a flagged check-in: {body}"
        return body

    def test_flagged_check_in_appears_in_the_review_queue(self, client, instructor, flagged):
        queue = client.get("/checkins/review-queue", headers=instructor.headers)
        assert queue.status_code == 200
        assert flagged["id"] in {item["id"] for item in queue.json()["items"]}

    def test_student_cannot_review(self, client, enrolled_student, flagged):
        assert client.patch(f"/checkins/{flagged['id']}/review",
                            json={"approve": True},
                            headers=enrolled_student.headers).status_code == 403

    def test_instructor_approval_records_the_reviewer(self, client, instructor, flagged):
        reviewed = client.patch(f"/checkins/{flagged['id']}/review",
                                json={"approve": True, "notes": "verified in person"},
                                headers=instructor.headers)
        assert reviewed.status_code == 200
        body = reviewed.json()
        assert body["status"] == "approved"
        assert body["reviewed_by_id"] == instructor.id
        assert body["review_notes"] == "verified in person"

    def test_review_clears_the_queue(self, client, instructor, flagged):
        client.patch(f"/checkins/{flagged['id']}/review", json={"approve": True},
                     headers=instructor.headers)
        queue = client.get("/checkins/review-queue", headers=instructor.headers)
        assert flagged["id"] not in {item["id"] for item in queue.json()["items"]}

    def test_student_can_appeal_a_flagged_check_in(self, client, enrolled_student, flagged):
        appealed = client.post(f"/checkins/{flagged['id']}/appeal",
                               json={"reason": "I was in the room"},
                               headers=enrolled_student.headers)
        assert appealed.status_code == 200
        assert appealed.json()["status"] == "appealed"

    def test_appealed_check_in_returns_to_the_queue(self, client, instructor,
                                                    enrolled_student, flagged):
        client.post(f"/checkins/{flagged['id']}/appeal", json={"reason": "unfair"},
                    headers=enrolled_student.headers)
        queue = client.get("/checkins/review-queue", headers=instructor.headers)
        assert flagged["id"] in {item["id"] for item in queue.json()["items"]}

    def test_approved_check_in_cannot_be_appealed(self, client, instructor,
                                                  enrolled_student, flagged):
        client.patch(f"/checkins/{flagged['id']}/review", json={"approve": True},
                     headers=instructor.headers)
        refused = client.post(f"/checkins/{flagged['id']}/appeal", json={"reason": "x"},
                              headers=enrolled_student.headers)
        assert refused.status_code == 400

    def test_students_cannot_appeal_each_others_check_ins(self, client, make_actor, flagged):
        other = make_actor("student")
        assert client.post(f"/checkins/{flagged['id']}/appeal", json={"reason": "x"},
                           headers=other.headers).status_code == 403


class TestVisibility:
    def test_my_checkins_returns_only_my_own(self, client, enrolled_student,
                                             make_session, at_venue, make_actor):
        client.post("/checkins/", json={"session_id": make_session()["id"], **at_venue},
                    headers=enrolled_student.headers)
        mine = client.get("/checkins/me", headers=enrolled_student.headers).json()
        assert mine["total"] == 1

        stranger = make_actor("student")
        assert client.get("/checkins/me", headers=stranger.headers).json()["total"] == 0

    def test_student_cannot_read_another_students_check_in(self, client, enrolled_student,
                                                           make_session, at_venue, make_actor):
        created = client.post("/checkins/",
                              json={"session_id": make_session()["id"], **at_venue},
                              headers=enrolled_student.headers).json()
        stranger = make_actor("student")
        assert client.get(f"/checkins/{created['id']}",
                          headers=stranger.headers).status_code == 403

    def test_instructor_can_filter_by_course(self, client, instructor, enrolled_student,
                                             make_session, at_venue, course):
        client.post("/checkins/", json={"session_id": make_session()["id"], **at_venue},
                    headers=enrolled_student.headers)
        listed = client.get(f"/checkins/?course_id={course['id']}",
                            headers=instructor.headers)
        assert listed.status_code == 200
        assert listed.json()["total"] == 1


class TestRequiredChecksMustPass:
    """A required verification that did not pass can never be APPROVED.

    Regression tests for a reported bug: a session with require_face_match=true
    approved a check-in whose face match explicitly failed, because a failed
    match is only 25% of the weighted score and the total stayed under the
    threshold. The requirement was decorative.
    """

    @pytest.fixture
    def face_session(self, make_session):
        return make_session(require_face_match=True)

    def test_failed_required_face_match_is_not_approved(
        self, client, enrolled_student, face_session, passing_submission
    ):
        """Without an enrolled template the match cannot pass, which is the
        same observable state as a failed match.

        Goes through a real liveness challenge so that the face match is the
        only unmet requirement -- otherwise this would pass for the wrong
        reason, flagged on liveness rather than on the match."""
        body = client.post(
            "/checkins/",
            json=passing_submission(enrolled_student, face_session["id"]),
            headers=enrolled_student.headers).json()
        assert body["status"] != "approved"
        assert body["status"] == "flagged"

    def test_it_is_flagged_not_rejected(self, client, enrolled_student, face_session, at_venue):
        """Flagged rather than rejected deliberately. Module 3's matcher scores
        0.0 for every live camera frame (it compares SHA-256 digests), so a
        hard rejection would lock every student out of a face-match session.
        Flagging routes it to a human instead."""
        body = client.post("/checkins/", json={
            "session_id": face_session["id"], **at_venue},
            headers=enrolled_student.headers).json()
        assert body["status"] == "flagged"

    def test_the_same_submission_is_approved_when_face_match_is_not_required(
        self, client, enrolled_student, make_session, passing_submission
    ):
        """Isolates the gate: identical payload, only the session policy differs."""
        session = make_session(require_face_match=False)
        body = client.post(
            "/checkins/",
            json=passing_submission(enrolled_student, session["id"]),
            headers=enrolled_student.headers).json()
        assert body["status"] == "approved"

    def test_risk_score_alone_would_have_approved_it(
        self, client, enrolled_student, face_session, passing_submission
    ):
        """Proves the GATE, not the score, changed the verdict.

        A registered device removes the device risk, so the only thing left is
        the unverified face match - and the weighted score lands under the
        0.5 threshold. Pre-fix this combination was approved.
        """
        reg = client.post("/devices/", json={"device_fingerprint": "f" * 40,
                                             "device_name": "L", "platform": "web"},
                          headers=enrolled_student.headers)
        assert reg.status_code == 201
        body = client.post(
            "/checkins/",
            json=passing_submission(enrolled_student, face_session["id"],
                                    device_fingerprint="f" * 40),
            headers=enrolled_student.headers).json()
        assert body["risk_score"] < 0.5, f"score should be under threshold, got {body['risk_score']}"
        assert body["status"] == "flagged", "but a required check did not pass"

    def test_client_cannot_fake_a_face_match_pass(
        self, client, enrolled_student, face_session, at_venue
    ):
        """face_match_passed is not a client-settable field; the value comes
        from module 3 or not at all."""
        body = client.post("/checkins/", json={
            "session_id": face_session["id"], **at_venue,
            "face_match_passed": True, "face_match_score": 1.0},
            headers=enrolled_student.headers).json()
        # Unknown keys are ignored by the model, so the claim never reaches the
        # risk engine and the requirement stays unmet.
        assert body["status"] == "flagged"


class TestClientCannotSetNetworkFlags:
    def test_vpn_flag_in_the_body_is_rejected_or_ignored(
        self, client, enrolled_student, make_session, passing_submission
    ):
        """The field was removed: a client's only incentive would be to omit
        it, so accepting it bought nothing and invited confusion."""
        response = client.post(
            "/checkins/",
            json=passing_submission(enrolled_student, make_session()["id"],
                                    vpn_detected=True),
            headers=enrolled_student.headers)
        assert response.status_code == 201
        # Extra keys are ignored by the model, so the submission still lands
        # clean rather than self-incriminating.
        assert response.json()["status"] == "approved"


class TestDeviceAttestation:
    """A browser has no platform attestation, and that is not evidence.

    Nothing in the system can produce a Play Integrity or App Attest verdict
    from a web client, so `devices.attestation_passed` sits at its default for
    every real device. The engine must read that as "no verdict", not as a
    failed one -- see CheckInContext.attestation_known.
    """

    def _register(self, client, actor, fingerprint):
        reg = client.post("/devices/", json={"device_fingerprint": fingerprint,
                                             "device_name": "L", "platform": "web"},
                          headers=actor.headers)
        assert reg.status_code == 201
        return reg.json()

    def _signals(self, client, actor, checkin_id):
        detail = client.get(f"/checkins/{checkin_id}", headers=actor.headers)
        assert detail.status_code == 200
        return {s["signal_type"] for s in detail.json()["risk_signals"]}

    def test_a_registered_browser_records_no_attestation_failure(
        self, client, enrolled_student, make_session, passing_submission
    ):
        self._register(client, enrolled_student, "a" * 40)
        created = client.post("/checkins/", json=passing_submission(
            enrolled_student, make_session()["id"], device_fingerprint="a" * 40),
            headers=enrolled_student.headers).json()
        assert "attestation_failed" not in self._signals(
            client, enrolled_student, created["id"])

    def test_registering_a_device_does_not_raise_the_score(
        self, client, enrolled_student, make_session, passing_submission
    ):
        """The regression. Scoring the absent verdict as a failure meant a
        student who registered their browser scored WORSE than one who never
        did, so the cheapest way to look clean was to defeat device binding.
        """
        without = client.post("/checkins/", json=passing_submission(
            enrolled_student, make_session()["id"]),
            headers=enrolled_student.headers).json()
        self._register(client, enrolled_student, "b" * 40)
        with_device = client.post("/checkins/", json=passing_submission(
            enrolled_student, make_session()["id"], device_fingerprint="b" * 40),
            headers=enrolled_student.headers).json()
        assert with_device["risk_score"] <= without["risk_score"]

    def test_a_ceremony_that_ran_and_failed_is_still_recorded(
        self, client, db, enrolled_student, make_session, passing_submission
    ):
        """The flag is not dead -- it is waiting for a producer. Once something
        stamps last_attestation_at with a failed verdict, the signal fires.
        """
        from app.models import Device
        from app.models.base import utcnow

        device = self._register(client, enrolled_student, "c" * 40)
        row = db.get(Device, device["id"])
        row.last_attestation_at = utcnow()
        row.attestation_passed = False
        db.commit()

        created = client.post("/checkins/", json=passing_submission(
            enrolled_student, make_session()["id"], device_fingerprint="c" * 40),
            headers=enrolled_student.headers).json()
        assert "attestation_failed" in self._signals(
            client, enrolled_student, created["id"])
