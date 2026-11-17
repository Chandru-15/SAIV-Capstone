"""The active liveness challenge flow, end to end over HTTP."""

import json

import pytest

from tests.conftest import FAKE_FRAMES, FAKE_HASH, FAKE_IMAGE

pytestmark = pytest.mark.flow


@pytest.fixture
def enrolled(client_with_face, admin, make_actor, course):
    student = make_actor("student").grant_consent()
    client_with_face.post("/admin/enrollments/",
                          json={"student_id": student.id, "course_id": course["id"]},
                          headers=admin.headers)
    client_with_face.post("/users/me/face/enroll", json={"image": FAKE_IMAGE},
                          headers=student.headers)
    return student


@pytest.fixture
def only_gesture_challenges(monkeypatch):
    """Pin the rotation to blink and head-turn.

    The server picks a type at random, which is the point, but a test about
    how a submission of eight frames is handled cannot also be a test about
    which type turned up. The colour challenge has its own frame contract and
    its own class below.
    """
    from app.services import liveness_challenge as lc
    monkeypatch.setattr(lc, "ACTIVE_CHALLENGE_TYPES", ("blink", "head_turn"))


@pytest.fixture
def only_colour_challenges(monkeypatch):
    from app.services import liveness_challenge as lc
    monkeypatch.setattr(lc, "ACTIVE_CHALLENGE_TYPES", ("colour_reflection",))


def get_challenge(client, student, session_id):
    r = client.post(f"/checkins/liveness-challenge?session_id={session_id}",
                    headers=student.headers)
    assert r.status_code == 201, r.text
    return r.json()


def colour_frames(challenge):
    """Exactly as many frames as the challenge asked for."""
    return [FAKE_IMAGE] * (len(challenge["colour_sequence"])
                           * challenge["frames_per_step"])


class TestIssuingAChallenge:
    def test_returns_a_usable_challenge(self, client_with_face, enrolled, make_session):
        body = get_challenge(client_with_face, enrolled, make_session()["id"])
        assert body["challenge_type"] in ("blink", "head_turn", "colour_reflection")
        assert body["min_frames"] >= 2
        assert body["max_frames"] == 30
        assert body["instruction"]
        assert body["challenge_id"]

    def test_each_request_is_a_fresh_challenge(self, client_with_face, enrolled, make_session):
        sid = make_session()["id"]
        a = get_challenge(client_with_face, enrolled, sid)
        b = get_challenge(client_with_face, enrolled, sid)
        assert a["challenge_id"] != b["challenge_id"]

    def test_requires_camera_consent(self, client_with_face, admin, make_actor,
                                     course, make_session):
        """Told before the camera turns on, not after."""
        student = make_actor("student").grant_consent(camera=False, geolocation=True)
        client_with_face.post("/admin/enrollments/",
                              json={"student_id": student.id, "course_id": course["id"]},
                              headers=admin.headers)
        r = client_with_face.post(
            f"/checkins/liveness-challenge?session_id={make_session()['id']}",
            headers=student.headers)
        assert r.status_code == 400
        assert "consent" in r.json()["message"].lower()

    def test_requires_enrolment(self, client_with_face, make_actor, make_session):
        student = make_actor("student").grant_consent()
        r = client_with_face.post(
            f"/checkins/liveness-challenge?session_id={make_session()['id']}",
            headers=student.headers)
        assert r.status_code == 403

    def test_refused_when_the_window_is_closed(self, client_with_face, enrolled, make_session):
        r = client_with_face.post(
            f"/checkins/liveness-challenge?session_id={make_session(open_window=False)['id']}",
            headers=enrolled.headers)
        assert r.status_code == 400

    def test_anonymous_request_refused(self, client_with_face, make_session):
        assert client_with_face.post(
            f"/checkins/liveness-challenge?session_id={make_session()['id']}"
        ).status_code == 401

    def test_issuance_is_audited(self, client_with_face, admin, enrolled, make_session):
        get_challenge(client_with_face, enrolled, make_session()["id"])
        logs = client_with_face.get(f"/audit/logs?user_id={enrolled.id}",
                                    headers=admin.headers).json()
        assert "liveness_challenge_issued" in {e["action"] for e in logs["items"]}


class TestSubmittingFrames:
    def test_completed_challenge_is_approved(self, client_with_face, enrolled, only_gesture_challenges,
                                             make_session, at_venue, face_stub):
        session = make_session()
        ch = get_challenge(client_with_face, enrolled, session["id"])
        r = client_with_face.post("/checkins/", json={
            "session_id": session["id"],
            "challenge_id": ch["challenge_id"],
            "challenge_frames": FAKE_FRAMES,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "approved"
        assert r.json()["liveness_score"] == 0.93

    def test_frames_are_forwarded_as_challenge_frames(self, client_with_face, enrolled, only_gesture_challenges,
                                                      make_session, at_venue, face_stub):
        """Module 3 reads the blink sequence from the ordering, so it must get
        the list, not a single image."""
        session = make_session()
        ch = get_challenge(client_with_face, enrolled, session["id"])
        face_stub["liveness_requests"].clear()
        client_with_face.post("/checkins/", json={
            "session_id": session["id"], "challenge_id": ch["challenge_id"],
            "challenge_frames": FAKE_FRAMES,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        sent = face_stub["liveness_requests"][-1]
        assert len(sent["challenge_frames"]) == len(FAKE_FRAMES)
        assert "challenge_response" not in sent or sent.get("challenge_response") is None

    def test_the_servers_challenge_type_is_used_not_the_clients(
        self, client_with_face, enrolled, only_gesture_challenges, make_session,
        at_venue, face_stub
    ):
        """The core anti-replay property. A client that could choose the type
        would pre-record that one challenge and replay it forever."""
        session = make_session()
        ch = get_challenge(client_with_face, enrolled, session["id"])
        lying = "head_turn" if ch["challenge_type"] == "blink" else "blink"
        face_stub["liveness_requests"].clear()
        client_with_face.post("/checkins/", json={
            "session_id": session["id"], "challenge_id": ch["challenge_id"],
            "challenge_frames": FAKE_FRAMES,
            "liveness_challenge_type": lying,          # the lie
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert face_stub["liveness_requests"][-1]["challenge_type"] == ch["challenge_type"]

    def test_a_challenge_cannot_be_reused(self, client_with_face, enrolled, only_gesture_challenges,
                                          make_session, at_venue):
        """Replaying the same captured frames must fail even inside the window."""
        session = make_session()
        ch = get_challenge(client_with_face, enrolled, session["id"])
        body = {"session_id": session["id"], "challenge_id": ch["challenge_id"],
                "challenge_frames": FAKE_FRAMES,
                "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
                "location_accuracy_meters": 5.0}
        assert client_with_face.post("/checkins/", json=body,
                                     headers=enrolled.headers).status_code == 201

        other = make_session()
        ch2 = get_challenge(client_with_face, enrolled, other["id"])
        replay = {**body, "session_id": other["id"], "challenge_id": ch["challenge_id"]}
        r = client_with_face.post("/checkins/", json=replay, headers=enrolled.headers)
        assert r.status_code == 400
        assert "invalid, expired or already used" in r.json()["message"]
        assert ch2  # unused, proving the failure was the stale id not the session

    def test_frames_without_a_challenge_id_are_refused(self, client_with_face, enrolled,
                                                       make_session, at_venue):
        r = client_with_face.post("/checkins/", json={
            "session_id": make_session()["id"], "challenge_frames": FAKE_FRAMES,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert r.status_code == 400
        assert "challenge_id is required" in r.json()["message"]

    def test_unknown_challenge_id_is_refused(self, client_with_face, enrolled,
                                             make_session, at_venue):
        r = client_with_face.post("/checkins/", json={
            "session_id": make_session()["id"], "challenge_id": "fabricated",
            "challenge_frames": FAKE_FRAMES,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert r.status_code == 400

    def test_another_students_challenge_is_refused(self, client_with_face, admin,
                                                   make_actor, course, enrolled,
                                                   make_session, at_venue):
        """A challenge is bound to one student; borrowing one must not work."""
        session = make_session()
        ch = get_challenge(client_with_face, enrolled, session["id"])

        thief = make_actor("student").grant_consent()
        client_with_face.post("/admin/enrollments/",
                              json={"student_id": thief.id, "course_id": course["id"]},
                              headers=admin.headers)
        r = client_with_face.post("/checkins/", json={
            "session_id": session["id"], "challenge_id": ch["challenge_id"],
            "challenge_frames": FAKE_FRAMES,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=thief.headers)
        assert r.status_code == 403

    def test_too_few_frames_is_refused(self, client_with_face, enrolled, only_gesture_challenges,
                                       make_session, at_venue):
        session = make_session()
        ch = get_challenge(client_with_face, enrolled, session["id"])
        r = client_with_face.post("/checkins/", json={
            "session_id": session["id"], "challenge_id": ch["challenge_id"],
            "challenge_frames": [FAKE_IMAGE],          # one frame shows no change
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert r.status_code == 422
        assert "frames" in r.json()["message"]

    def test_over_thirty_frames_is_refused_by_validation(self, client_with_face,
                                                         enrolled, make_session, at_venue):
        session = make_session()
        ch = get_challenge(client_with_face, enrolled, session["id"])
        r = client_with_face.post("/checkins/", json={
            "session_id": session["id"], "challenge_id": ch["challenge_id"],
            "challenge_frames": [FAKE_IMAGE] * 31,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert r.status_code == 422


class TestChallengeFailure:
    def test_an_uncompleted_challenge_is_a_hard_liveness_failure(
        self, client_with_face, enrolled, only_gesture_challenges, make_session,
        at_venue, face_stub
    ):
        """Module 3 can return a decent quality score while reporting the blink
        never happened. The challenge verdict must win, or 'didn't blink' would
        pass on a well-lit photo."""
        face_stub["liveness_body"] = {
            "liveness_passed": True, "liveness_score": 0.91,
            "liveness_threshold": 0.60, "face_embedding_hash": FAKE_HASH,
            "details": {"challenge_passed": False, "blink_detected": False},
        }
        session = make_session()
        ch = get_challenge(client_with_face, enrolled, session["id"])
        r = client_with_face.post("/checkins/", json={
            "session_id": session["id"], "challenge_id": ch["challenge_id"],
            "challenge_frames": FAKE_FRAMES,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert r.status_code == 201
        body = r.json()
        assert body["liveness_passed"] is False
        assert body["liveness_score"] == 0.0
        assert body["status"] == "rejected"

    def test_module3_outage_flags_rather_than_blocking(self, client_with_face, enrolled, only_gesture_challenges,
                                                       make_session, at_venue, face_stub):
        session = make_session()
        ch = get_challenge(client_with_face, enrolled, session["id"])
        face_stub["down"] = True
        r = client_with_face.post("/checkins/", json={
            "session_id": session["id"], "challenge_id": ch["challenge_id"],
            "challenge_frames": FAKE_FRAMES,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert r.status_code == 201
        assert r.json()["status"] == "flagged"


class TestPreflightEndpoint:
    def test_frontend_can_preflight_its_capture_loop(self, client_with_face, enrolled):
        r = client_with_face.post("/users/me/face/liveness-frames", json={
            "frames": FAKE_FRAMES, "challenge_type": "blink"},
            headers=enrolled.headers)
        assert r.status_code == 200
        body = r.json()
        assert body["challenge_type"] == "blink"
        assert body["frames_received"] == len(FAKE_FRAMES)
        assert body["challenge_passed"] is True

    def test_preflight_surfaces_an_uncompleted_challenge(self, client_with_face,
                                                         enrolled, face_stub):
        face_stub["liveness_body"] = {
            "liveness_passed": False, "liveness_score": 0.4,
            "liveness_threshold": 0.60, "face_embedding_hash": FAKE_HASH,
            "details": {"challenge_passed": False}}
        r = client_with_face.post("/users/me/face/liveness-frames", json={
            "frames": FAKE_FRAMES, "challenge_type": "head_turn"},
            headers=enrolled.headers)
        assert r.json()["challenge_passed"] is False


class TestThePassiveImagePathCannotSatisfyARequirement:
    """`require_liveness_check` binds the backend, not a cooperative client.

    The flag used to be read only in the browser: CheckInModal reads
    `session.require_liveness_check`, asks POST /checkins/liveness-challenge
    for a challenge and submits frames. Nothing on the server required any of
    that. A caller that skipped the challenge call and posted a single `image`
    got a passing passive verdict from module 3 and an APPROVED attendance
    record -- reproduced end to end before this gate was written, with
    status "approved", liveness_passed true and liveness_score 0.93.

    That matters because the passive path has no anti-spoofing to speak of:
    module 3 leaves challenge_passed as None and scores detection confidence,
    image quality and an inferred nose-depth value. A sharp printed photo
    scores HIGHER on the first two than a real face in poor light.
    """

    def test_a_still_image_no_longer_approves_a_liveness_session(
        self, client_with_face, enrolled, make_session, at_venue, face_stub
    ):
        """The exact submission that used to come back approved."""
        session = make_session()
        assert session["require_liveness_check"] is True
        r = client_with_face.post("/checkins/", json={
            "session_id": session["id"],
            "image": FAKE_IMAGE,                      # no challenge requested
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "flagged"
        assert "replay_suspected" in json.loads(body["risk_factors"])

    def test_the_attempt_is_still_recorded_for_review(
        self, client_with_face, admin, enrolled, make_session, at_venue, face_stub
    ):
        """Flagged rather than 422 precisely so the evidence survives: a
        refused request would leave a reviewer nothing to look at."""
        session = make_session()
        r = client_with_face.post("/checkins/", json={
            "session_id": session["id"], "image": FAKE_IMAGE,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        stored = client_with_face.get(f"/checkins/{r.json()['id']}",
                                      headers=admin.headers).json()
        assert stored["status"] == "flagged"
        assert stored["liveness_challenge_type"] == "passive"
        assert any(sig["signal_type"] == "replay_suspected"
                   for sig in stored["risk_signals"])

    def test_module_3_is_still_called_so_a_real_spoof_is_not_hidden(
        self, client_with_face, enrolled, make_session, at_venue, face_stub
    ):
        """The gate is about admissibility, not about skipping the check. A
        submission that is BOTH unchallenged and a module 3 failure must show
        both reasons, or a reviewer cannot tell a lazy client from a spoof."""
        face_stub["liveness_body"] = {
            "liveness_passed": False, "liveness_score": 0.2,
            "liveness_threshold": 0.60, "face_embedding_hash": FAKE_HASH,
            "details": {}}
        session = make_session()
        r = client_with_face.post("/checkins/", json={
            "session_id": session["id"], "image": FAKE_IMAGE,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        factors = json.loads(r.json()["risk_factors"])
        assert "replay_suspected" in factors
        assert "liveness_failed" in factors

    def test_the_active_path_still_approves(self, client_with_face, enrolled, only_gesture_challenges,
                                            make_session, at_venue, face_stub):
        """The frontend already does this, so nothing it sends today breaks."""
        session = make_session()
        ch = get_challenge(client_with_face, enrolled, session["id"])
        r = client_with_face.post("/checkins/", json={
            "session_id": session["id"], "challenge_id": ch["challenge_id"],
            "challenge_frames": FAKE_FRAMES,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert r.json()["status"] == "approved"
        assert "replay_suspected" not in json.loads(r.json()["risk_factors"] or "[]")

    def test_a_session_without_the_requirement_still_accepts_an_image(
        self, client_with_face, enrolled, make_session, at_venue, face_stub
    ):
        """Face-match-only sessions legitimately use the passive image path:
        module 1's deriveFacePolicy sets imageRequired for exactly that case,
        so narrowing the image path would have broken them."""
        session = make_session(require_liveness_check=False,
                               require_face_match=True)
        r = client_with_face.post("/checkins/", json={
            "session_id": session["id"], "image": FAKE_IMAGE,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "approved"
        assert "replay_suspected" not in json.loads(r.json()["risk_factors"] or "[]")

    def test_a_lenient_risk_threshold_does_not_reopen_the_path(
        self, client_with_face, enrolled, make_session, at_venue, face_stub
    ):
        """An instructor relaxing risk_threshold is relaxing a score budget.
        It must not also switch off a verification requirement -- which is how
        the original reproduction got from flagged to approved."""
        session = make_session(risk_threshold=0.95)
        r = client_with_face.post("/checkins/", json={
            "session_id": session["id"], "image": FAKE_IMAGE,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert r.json()["status"] == "flagged"


class TestTheColourReflectionChallenge:
    """The colour challenge over HTTP, end to end.

    What is being pinned here is the division of knowledge. The client is told
    which colours to show, because it has to show them. It is NOT told, and
    cannot tell us, which frame goes with which colour -- that pairing is the
    evidence, and the server keeps it.
    """

    def test_a_challenge_arrives_with_a_sequence_and_its_timing(
        self, client_with_face, enrolled, only_colour_challenges, make_session
    ):
        body = get_challenge(client_with_face, enrolled, make_session()["id"])
        assert body["challenge_type"] == "colour_reflection"
        assert len(body["colour_sequence"]) >= 3
        assert all(colour.startswith("#") for colour in body["colour_sequence"])
        assert body["hold_ms"] > 0 and body["settle_ms"] > 0
        assert body["frames_per_step"] >= 1
        assert body["min_frames"] == len(body["colour_sequence"]) * body["frames_per_step"]
        assert "still" in body["instruction"].lower()

    def test_each_challenge_gets_a_fresh_sequence(
        self, client_with_face, enrolled, only_colour_challenges, make_session
    ):
        session = make_session()["id"]
        sequences = {tuple(get_challenge(client_with_face, enrolled, session)
                           ["colour_sequence"]) for _ in range(12)}
        assert len(sequences) > 1, "a fixed sequence could be pre-recorded"

    def test_the_server_tells_module_3_which_colour_each_frame_had(
        self, client_with_face, enrolled, only_colour_challenges, make_session,
        at_venue, face_stub
    ):
        session = make_session()
        challenge = get_challenge(client_with_face, enrolled, session["id"])
        response = client_with_face.post("/checkins/", json={
            "session_id": session["id"], "challenge_id": challenge["challenge_id"],
            "challenge_frames": colour_frames(challenge),
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert response.status_code == 201, response.text

        sent = face_stub["liveness_requests"][-1]
        assert sent["challenge_type"] == "colour_reflection"
        # One colour per frame, in order, each repeated frames_per_step times.
        expected = [colour for colour in challenge["colour_sequence"]
                    for _ in range(challenge["frames_per_step"])]
        assert sent["colour_sequence"] == expected
        assert len(sent["colour_sequence"]) == len(sent["challenge_frames"])

    def test_a_sequence_in_the_request_body_is_ignored(
        self, client_with_face, enrolled, only_colour_challenges, make_session,
        at_venue, face_stub
    ):
        """The attack this closes.

        If the client could state the sequence, it would state whatever its
        captured frames happen to match, and the challenge would degrade to
        "did the screen change colour at all" -- which a video of a screen
        changing colour satisfies.
        """
        session = make_session()
        challenge = get_challenge(client_with_face, enrolled, session["id"])
        client_with_face.post("/checkins/", json={
            "session_id": session["id"], "challenge_id": challenge["challenge_id"],
            "challenge_frames": colour_frames(challenge),
            "colour_sequence": ["#000000"] * 12,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)

        sent = face_stub["liveness_requests"][-1]
        assert "#000000" not in sent["colour_sequence"]
        assert sent["colour_sequence"] == [
            colour for colour in challenge["colour_sequence"]
            for _ in range(challenge["frames_per_step"])]

    def test_the_frame_count_must_be_exact(
        self, client_with_face, enrolled, only_colour_challenges, make_session, at_venue
    ):
        """Not a minimum. The frames carry no labels, so the colour each one
        belongs to is read off its position; a short list would mean guessing
        which steps were dropped and scoring frames against colours they were
        never lit by."""
        session = make_session()
        challenge = get_challenge(client_with_face, enrolled, session["id"])
        too_many = colour_frames(challenge) + [FAKE_IMAGE]
        response = client_with_face.post("/checkins/", json={
            "session_id": session["id"], "challenge_id": challenge["challenge_id"],
            "challenge_frames": too_many,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        assert response.status_code == 422
        assert "exactly" in response.json()["message"]

    def test_a_refused_colour_verdict_is_a_hard_liveness_failure(
        self, client_with_face, enrolled, only_colour_challenges, make_session,
        at_venue, face_stub
    ):
        face_stub["liveness_body"] = {
            "liveness_passed": False, "liveness_score": 0.2,
            "liveness_threshold": 0.60, "face_embedding_hash": FAKE_HASH,
            "details": {"challenge_passed": False, "frames_with_face": 12,
                        "colour_reason": "The whole image brightened evenly."}}
        session = make_session()
        challenge = get_challenge(client_with_face, enrolled, session["id"])
        body = client_with_face.post("/checkins/", json={
            "session_id": session["id"], "challenge_id": challenge["challenge_id"],
            "challenge_frames": colour_frames(challenge),
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers).json()
        assert body["status"] != "approved"
        assert body["liveness_score"] == 0.0

    def test_no_verdict_is_neither_a_pass_nor_an_accusation(
        self, client_with_face, enrolled, only_colour_challenges, make_session,
        at_venue, face_stub
    ):
        """The case that needs the most care.

        Module 3 can decline to judge: the screen was too dim, the room too
        bright, the face too far away. Note that liveness_passed is True here
        and the score is high -- it is a clean, sharp image. Recording that as
        a pass would admit a tablet held far enough back to wash out the
        reflection. Recording it as a failure would accuse a student of
        spoofing over their lighting. So neither: the capture is unusable, the
        student is told what to change, and the spent challenge is gone.
        """
        face_stub["liveness_body"] = {
            "liveness_passed": True, "liveness_score": 0.91,
            "liveness_threshold": 0.60, "face_embedding_hash": FAKE_HASH,
            "details": {"challenge_passed": None, "frames_with_face": 12,
                        "colour_reason": "Raise your screen brightness and "
                                         "hold the device closer."}}
        session = make_session()
        challenge = get_challenge(client_with_face, enrolled, session["id"])
        response = client_with_face.post("/checkins/", json={
            "session_id": session["id"], "challenge_id": challenge["challenge_id"],
            "challenge_frames": colour_frames(challenge),
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)

        assert response.status_code == 400
        message = response.json()["message"]
        assert "brightness" in message
        # Actionable, and not an accusation.
        for word in ("spoof", "fake", "photograph", "suspect"):
            assert word not in message.lower()

    def test_an_undecided_capture_does_not_leave_an_approved_checkin(
        self, client_with_face, enrolled, only_colour_challenges, make_session,
        at_venue, face_stub
    ):
        face_stub["liveness_body"] = {
            "liveness_passed": True, "liveness_score": 0.95,
            "liveness_threshold": 0.60, "face_embedding_hash": FAKE_HASH,
            "details": {"challenge_passed": None, "frames_with_face": 12,
                        "colour_reason": "Raise your screen brightness."}}
        session = make_session()
        challenge = get_challenge(client_with_face, enrolled, session["id"])
        client_with_face.post("/checkins/", json={
            "session_id": session["id"], "challenge_id": challenge["challenge_id"],
            "challenge_frames": colour_frames(challenge),
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers)
        listing = client_with_face.get("/checkins/my-checkins",
                                       headers=enrolled.headers).json()
        items = listing["items"] if isinstance(listing, dict) else listing
        assert not [c for c in items if c["session_id"] == session["id"]]

    def test_the_challenge_type_is_recorded_on_the_checkin(
        self, client_with_face, enrolled, only_colour_challenges, make_session,
        at_venue, face_stub
    ):
        """Also the test that proves the CHECK constraint on
        checkins.liveness_challenge_type was widened: without the migration
        this INSERT fails and the student sees an opaque 500 after doing the
        whole capture."""
        session = make_session()
        challenge = get_challenge(client_with_face, enrolled, session["id"])
        body = client_with_face.post("/checkins/", json={
            "session_id": session["id"], "challenge_id": challenge["challenge_id"],
            "challenge_frames": colour_frames(challenge),
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}, headers=enrolled.headers).json()
        assert body["liveness_challenge_type"] == "colour_reflection"

    def test_a_colour_challenge_is_still_single_use(
        self, client_with_face, enrolled, only_colour_challenges, make_session,
        at_venue, face_stub
    ):
        """Replayed against a DIFFERENT session, so the refusal is the spent
        challenge rather than the duplicate-check-in guard.

        This matters more for colour than for a gesture: the captured frames
        really were lit by a real sequence, so they would satisfy module 3
        again. Single use is what stops one honest capture being spent on
        every session for the rest of term.
        """
        session = make_session()
        challenge = get_challenge(client_with_face, enrolled, session["id"])
        frames = colour_frames(challenge)
        payload = {
            "session_id": session["id"], "challenge_id": challenge["challenge_id"],
            "challenge_frames": frames,
            "latitude": at_venue["latitude"], "longitude": at_venue["longitude"],
            "location_accuracy_meters": 5.0}
        assert client_with_face.post("/checkins/", json=payload,
                                     headers=enrolled.headers).status_code == 201

        other = make_session()
        replay = {**payload, "session_id": other["id"]}
        again = client_with_face.post("/checkins/", json=replay,
                                      headers=enrolled.headers)
        assert again.status_code == 400
        assert "invalid, expired or already used" in again.json()["message"]
