"""Retrying a failed check-in, end to end.

Attempts are unlimited and paced only by an exponential cooldown; they do not
raise the risk score. These tests pin the properties that make that workable:
a plain duplicate is still refused, an ordinary automatic failure CAN be
retried, an outcome a person settled cannot be re-rolled, and pacing comes
from the published 10-per-minute budget rather than a growing cooldown.
"""
import pytest

pytestmark = pytest.mark.flow


@pytest.fixture
def flagged_payload(at_venue):
    """A submission that flags: no image, so liveness cannot be established
    and the session's require_liveness_check is unmet.

    Note that at_venue itself now flags too, because a single image is the
    passive path and no longer satisfies require_liveness_check -- see
    API-NOTES.md section 6. This payload is still the clearer fixture for
    "nothing to verify at all", and keeps these tests independent of that gate.
    """
    return {k: v for k, v in at_venue.items() if k != "image"}


def submit(client, actor, session_id, payload, **extra):
    return client.post("/checkins/",
                       json={"session_id": session_id, **payload, **extra},
                       headers=actor.headers)


class TestDuplicateVersusRetry:
    def test_a_plain_duplicate_is_still_refused(
            self, client, enrolled_student, make_session, at_venue):
        """The retry path must not weaken the documented contract: without the
        flag, a second POST fails exactly as it always did."""
        session = make_session()
        assert submit(client, enrolled_student, session["id"], at_venue).status_code == 201
        duplicate = submit(client, enrolled_student, session["id"], at_venue)
        assert duplicate.status_code == 400
        assert duplicate.json()["code"] == "business_rule_violation"

    def test_the_flag_on_a_first_attempt_is_harmless(
            self, client, enrolled_student, make_session, at_venue):
        """A client that always sets retry should still be able to check in."""
        session = make_session()
        first = submit(client, enrolled_student, session["id"], at_venue, retry=True)
        assert first.status_code == 201


class TestRetriesArePacedOnlyByTheRateLimit:
    """The growing per-attempt cooldown is gone.

    It was not in docs/API-SPECIFICATION.md. The published budget is 10
    check-in attempts per minute per user, enforced in checkin_controller, so
    inside that budget a retry is allowed immediately and the client is told
    to wait only once the budget is spent.
    """

    def test_an_immediate_retry_is_allowed(
            self, client, enrolled_student, make_session, flagged_payload):
        """Previously a 429 with a 30s Retry-After. Now it just works."""
        session = make_session()
        first = submit(client, enrolled_student, session["id"], flagged_payload)
        assert first.status_code == 201
        assert first.json()["status"] == "flagged"

        again = submit(client, enrolled_student, session["id"], flagged_payload, retry=True)
        assert again.status_code == 201, again.text

    def test_the_budget_is_ten_per_minute_per_user(
            self, client, enrolled_student, make_session, flagged_payload):
        session = make_session()
        codes = [submit(client, enrolled_student, session["id"], flagged_payload,
                        retry=True).status_code for _ in range(11)]
        assert codes[:10] == [201] * 10, codes
        assert codes[10] == 429, codes

    def test_a_breach_says_how_long_to_wait(
            self, client, enrolled_student, make_session, flagged_payload):
        """RFC 9110: a 429 should carry Retry-After so a client backs off on
        instruction rather than polling."""
        session = make_session()
        for _ in range(10):
            submit(client, enrolled_student, session["id"], flagged_payload, retry=True)
        over = submit(client, enrolled_student, session["id"], flagged_payload, retry=True)

        assert over.status_code == 429
        assert over.json()["code"] == "rate_limited"
        assert "retry-after" in {k.lower() for k in over.headers}
        assert 1 <= int(over.headers["retry-after"]) <= 60
        assert over.json()["details"]["limit"] == 10
        assert over.json()["details"]["window_seconds"] == 60

    def test_one_students_budget_does_not_touch_another(
            self, client, admin, course, make_actor, make_session, flagged_payload,
            enrolled_student):
        """Keyed per user, so a student who exhausts their attempts cannot
        stop the rest of the class checking in."""
        session = make_session()
        for _ in range(11):
            submit(client, enrolled_student, session["id"], flagged_payload, retry=True)

        other = make_actor("student")
        assert client.post("/admin/enrollments/",
                           json={"student_id": other.id, "course_id": course["id"]},
                           headers=admin.headers).status_code == 201
        assert submit(client, other, session["id"],
                      flagged_payload).status_code == 201


class TestOneOutcomePerSession:
    def test_a_retry_updates_the_same_record(
            self, client, enrolled_student, make_session, flagged_payload):
        """One attendance outcome per student per session stays the invariant;
        uq_checkins_session_student still holds."""
        session = make_session()
        first = submit(client, enrolled_student, session["id"], flagged_payload)
        again = submit(client, enrolled_student, session["id"], flagged_payload, retry=True)
        assert again.json()["id"] == first.json()["id"]

    def test_the_first_attempt_time_is_preserved(
            self, client, enrolled_student, make_session, flagged_payload):
        """Retrying must not extend the appeal window or defer PII deletion."""
        session = make_session()
        first = submit(client, enrolled_student, session["id"], flagged_payload)
        again = submit(client, enrolled_student, session["id"], flagged_payload, retry=True)
        assert again.json()["checked_in_at"] == first.json()["checked_in_at"]
        assert again.json()["appeal_deadline"] == first.json()["appeal_deadline"]

    def test_a_passing_retry_is_allowed_to_pass(
            self, client, enrolled_student, make_session, flagged_payload,
            passing_submission):
        """The point of the feature: an honest student who failed on a bad
        capture gets their attendance."""
        session = make_session()
        assert submit(client, enrolled_student, session["id"],
                      flagged_payload).json()["status"] == "flagged"
        fixed = submit(client, enrolled_student, session["id"],
                       passing_submission(enrolled_student, session["id"]),
                       retry=True)
        assert fixed.status_code == 201
        assert fixed.json()["status"] == "approved"


class TestSettledOutcomesAreNotRetryable:
    def test_an_approved_check_in_cannot_be_retried(
            self, client, enrolled_student, make_session, at_venue,
            passing_submission):
        session = make_session()
        assert submit(client, enrolled_student, session["id"],
                      passing_submission(enrolled_student, session["id"])
                      ).json()["status"] == "approved"
        # at_venue, not another challenge: the retry is refused by the gate
        # before any biometrics are looked at, and asking for a challenge on a
        # settled check-in is refused too (same rule, tested separately).
        again = submit(client, enrolled_student, session["id"], at_venue, retry=True)
        assert again.status_code == 400
        assert "nothing to retry" in again.json()["message"].lower()

    def test_a_reviewed_check_in_cannot_be_retried(
            self, client, enrolled_student, instructor, make_session,
            flagged_payload):
        """A student must not be able to overwrite an instructor's decision by
        submitting again."""
        session = make_session()
        first = submit(client, enrolled_student, session["id"], flagged_payload)
        rejected = client.patch(f"/checkins/{first.json()['id']}/review",
                                json={"approve": False, "notes": "not you"},
                                headers=instructor.headers)
        assert rejected.status_code == 200, rejected.text

        again = submit(client, enrolled_student, session["id"], flagged_payload, retry=True)
        assert again.status_code == 400
        assert "appeal" in again.json()["message"].lower()


class TestAttemptsAreRecordedNotPunished:
    """Each attempt is judged on its own merits; the history is kept for the
    dashboard rather than folded into the decision."""

    def test_the_risk_score_does_not_climb_with_attempts(
            self, client, enrolled_student, make_session, flagged_payload):
        session = make_session()
        scores = [submit(client, enrolled_student, session["id"],
                         flagged_payload).json()["risk_score"]]
        for _ in range(5):
            response = submit(client, enrolled_student, session["id"],
                              flagged_payload, retry=True)
            assert response.status_code == 201, response.text
            scores.append(response.json()["risk_score"])

        assert len(set(scores)) == 1, scores

    def test_a_late_passing_attempt_is_approved(
            self, client, enrolled_student, make_session, flagged_payload,
            passing_submission):
        """If it finally passes, it passes - however many goes it took."""
        session = make_session()
        submit(client, enrolled_student, session["id"], flagged_payload)
        for _ in range(6):
            submit(client, enrolled_student, session["id"], flagged_payload, retry=True)
        late = submit(client, enrolled_student, session["id"],
                      passing_submission(enrolled_student, session["id"]),
                      retry=True)
        assert late.status_code == 201
        assert late.json()["status"] == "approved"

    def test_the_attempt_history_is_kept_as_risk_signals(
            self, client, enrolled_student, instructor, make_session,
            flagged_payload):
        """A reviewer has to be able to see that this took several goes."""
        session = make_session()
        first = submit(client, enrolled_student, session["id"], flagged_payload)
        for _ in range(3):
            submit(client, enrolled_student, session["id"], flagged_payload, retry=True)

        detail = client.get(f"/checkins/{first.json()['id']}", headers=instructor.headers)
        assert detail.status_code == 200, detail.text
        signals = detail.json().get("risk_signals") or []
        retries = [s for s in signals if s["signal_type"] == "rapid_succession"]
        assert len(retries) == 3, signals


FAR_AWAY = {"latitude": 1.3521, "longitude": 103.8198}   # ~8 km from the venue


class TestAutomaticFailuresCanBeRetried:
    """The case the whole feature exists for.

    The risk engine rejects automatically on a failed face match, failed
    liveness or a location far outside the fence. Those are the ordinary
    failures - a dark room, the wrong room, a missed blink - and they are not
    grounds for an appeal, so the student has to be able to simply try again.
    """

    def test_an_automatically_rejected_check_in_can_be_retried(
            self, client, enrolled_student, make_session, at_venue,
            passing_submission):
        session = make_session()
        first = submit(client, enrolled_student, session["id"],
                       {**at_venue, **FAR_AWAY})
        assert first.json()["status"] == "rejected"
        # Nobody reviewed it; the engine decided.
        assert first.json()["reviewed_by_id"] is None

        again = submit(client, enrolled_student, session["id"],
                       passing_submission(enrolled_student, session["id"]),
                       retry=True)
        assert again.status_code == 201, again.text
        assert again.json()["status"] == "approved"
        # Updated in place, not duplicated.
        assert again.json()["id"] == first.json()["id"]

    def test_an_automatic_rejection_still_leaves_one_record(
            self, client, enrolled_student, make_session, at_venue):
        session = make_session()
        submit(client, enrolled_student, session["id"], {**at_venue, **FAR_AWAY})
        submit(client, enrolled_student, session["id"], at_venue, retry=True)
        mine = client.get("/checkins/me", headers=enrolled_student.headers).json()
        for_session = [row for row in mine["items"] if row["session_id"] == session["id"]]
        assert len(for_session) == 1

    def test_retrying_does_not_reopen_the_appeal_window(
            self, client, enrolled_student, make_session, at_venue):
        """Otherwise a student could keep the appeal window open indefinitely by
        resubmitting."""
        session = make_session()
        first = submit(client, enrolled_student, session["id"],
                       {**at_venue, **FAR_AWAY}).json()
        again = submit(client, enrolled_student, session["id"],
                       {**at_venue, **FAR_AWAY}, retry=True).json()
        assert again["appeal_deadline"] == first["appeal_deadline"]
        assert again["checked_in_at"] == first["checked_in_at"]

    def test_a_failed_retry_is_still_appealable(
            self, client, enrolled_student, make_session, at_venue):
        """Retrying must not cost the student their appeal."""
        session = make_session()
        first = submit(client, enrolled_student, session["id"],
                       {**at_venue, **FAR_AWAY}).json()
        submit(client, enrolled_student, session["id"],
               {**at_venue, **FAR_AWAY}, retry=True)
        appeal = client.post(f"/checkins/{first['id']}/appeal",
                             json={"reason": "I was in the room, GPS was wrong"},
                             headers=enrolled_student.headers)
        assert appeal.status_code == 200, appeal.text

    def test_an_appealed_check_in_cannot_be_retried(
            self, client, enrolled_student, make_session, at_venue):
        """An appeal has asked for human review; re-rolling would undercut it."""
        session = make_session()
        first = submit(client, enrolled_student, session["id"],
                       {**at_venue, **FAR_AWAY}).json()
        client.post(f"/checkins/{first['id']}/appeal", json={"reason": "GPS was wrong"},
                    headers=enrolled_student.headers)
        again = submit(client, enrolled_student, session["id"], at_venue, retry=True)
        assert again.status_code == 400


class TestEveryAttemptIsRecorded:
    """The attempt trail that ``checkins`` cannot keep.

    ``uq_checkins_session_student`` means a retry UPDATES the one check-in row,
    so the superseded attempt's evidence -- where the student was, what module
    3 scored, which factors fired -- used to be overwritten and lost. The only
    surviving trace was a ``rapid_succession`` signal saying "there was another
    attempt", with nothing about what it looked like.

    ``checkin_attempts`` holds one immutable row per submission instead. These
    tests pin that the trail is complete and that it is genuinely append-only.
    """

    @staticmethod
    def attempts(db, checkin_id):
        from app.models import CheckInAttempt

        return (db.query(CheckInAttempt)
                  .filter(CheckInAttempt.checkin_id == checkin_id)
                  .order_by(CheckInAttempt.attempt_number)
                  .all())

    def test_a_first_attempt_is_recorded_too(
            self, client, enrolled_student, make_session, passing_submission, db):
        """A trail missing the attempt that counted is not a trail."""
        session = make_session()
        first = submit(client, enrolled_student, session["id"],
                       passing_submission(enrolled_student, session["id"]))
        assert first.status_code == 201

        rows = self.attempts(db, first.json()["id"])
        assert [row.attempt_number for row in rows] == [1]
        assert rows[0].status.value == "approved"

    def test_a_retry_appends_rather_than_replaces(
            self, client, enrolled_student, make_session, flagged_payload,
            passing_submission, db):
        session = make_session()
        first = submit(client, enrolled_student, session["id"], flagged_payload)
        assert first.json()["status"] == "flagged"
        fixed = submit(client, enrolled_student, session["id"],
                       passing_submission(enrolled_student, session["id"]),
                       retry=True)
        assert fixed.json()["status"] == "approved"

        rows = self.attempts(db, first.json()["id"])
        assert [row.attempt_number for row in rows] == [1, 2]
        # The failed attempt's own verdict survives the approval that replaced
        # it. This is the whole point: `checkins.status` now says "approved".
        assert rows[0].status.value == "flagged"
        assert rows[1].status.value == "approved"

    def test_the_superseded_attempts_evidence_survives(
            self, client, enrolled_student, make_session, at_venue, db):
        """A retry from a different place must not erase where the student was
        on the attempt that failed -- that is exactly what a reviewer needs."""
        far_away = {**at_venue, "latitude": at_venue["latitude"] + 0.05,
                    "longitude": at_venue["longitude"] + 0.05}
        session = make_session()
        first = submit(client, enrolled_student, session["id"], far_away)
        submit(client, enrolled_student, session["id"], at_venue, retry=True)

        rows = self.attempts(db, first.json()["id"])
        assert len(rows) == 2
        assert rows[0].latitude == pytest.approx(far_away["latitude"])
        assert rows[1].latitude == pytest.approx(at_venue["latitude"])
        # Distance was computed per attempt, so the far one is recorded as far.
        assert rows[0].distance_from_venue_meters > rows[1].distance_from_venue_meters

    def test_each_attempt_keeps_its_own_risk_verdict(
            self, client, enrolled_student, make_session, flagged_payload, at_venue,
            db):
        session = make_session()
        first = submit(client, enrolled_student, session["id"], flagged_payload)
        submit(client, enrolled_student, session["id"], at_venue, retry=True)

        rows = self.attempts(db, first.json()["id"])
        # The failed attempt had no image, so liveness could not be
        # established; the passing one did.
        assert rows[0].liveness_passed is not True
        assert rows[1].liveness_passed is True
        assert rows[0].risk_score >= rows[1].risk_score
        assert rows[0].risk_factors is not None

    def test_attempt_numbers_are_contiguous_across_retries(
            self, client, enrolled_student, make_session, flagged_payload, db):
        """The trail numbers rows from the rapid_succession signal count. A
        gap or a repeat would show a reviewer a different attempt count from
        the one the student actually made."""
        session = make_session()
        first = submit(client, enrolled_student, session["id"], flagged_payload)
        for _ in range(3):
            submit(client, enrolled_student, session["id"], flagged_payload, retry=True)

        rows = self.attempts(db, first.json()["id"])
        assert [row.attempt_number for row in rows] == [1, 2, 3, 4]

    def test_attempts_share_the_check_ins_retention_deadline(
            self, client, enrolled_student, make_session, flagged_payload, at_venue,
            db):
        """They duplicate location and biometric scores, so they must be swept
        with the check-in rather than outliving it."""
        from app.models import CheckIn

        session = make_session()
        first = submit(client, enrolled_student, session["id"], flagged_payload)
        submit(client, enrolled_student, session["id"], at_venue, retry=True)

        checkin = db.get(CheckIn, first.json()["id"])
        rows = self.attempts(db, checkin.id)
        assert rows
        assert all(row.scheduled_deletion_at == checkin.scheduled_deletion_at
                   for row in rows)

    def test_deleting_the_check_in_takes_its_attempts(
            self, client, enrolled_student, make_session, at_venue, db):
        """ON DELETE CASCADE: the trail is evidence about a check-in, so it
        must not be left orphaned when the check-in is purged."""
        from app.models import CheckIn, CheckInAttempt

        session = make_session()
        first = submit(client, enrolled_student, session["id"], at_venue)
        checkin_id = first.json()["id"]
        assert self.attempts(db, checkin_id)

        db.delete(db.get(CheckIn, checkin_id))
        db.commit()
        assert db.query(CheckInAttempt).filter(
            CheckInAttempt.checkin_id == checkin_id).count() == 0
