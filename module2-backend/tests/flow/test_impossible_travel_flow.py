"""The impossible-travel speed test, end to end.

Two sessions, two venues 40 km apart, one student, seconds between them. The
unit tests pin the arithmetic; these pin the two things only a round trip can
show -- that the baseline is read from the attempt trail at all, and that a
rejected attempt is deliberately NOT part of it.
"""
import pytest

pytestmark = pytest.mark.flow

VENUE_LAT, VENUE_LON = 1.2966, 103.7764
# +0.36 degrees of longitude at this latitude is 40 064.8 m, measured with
# haversine_meters. Far enough that no surface transport covers it in the
# seconds these two submissions are apart.
FAR_LAT, FAR_LON = 1.2966, 103.7764 + 0.36


def submit(client, actor, payload, **extra):
    return client.post("/checkins/", json={**payload, **extra},
                       headers=actor.headers)


def signals_on(client, actor, checkin_id):
    detail = client.get(f"/checkins/{checkin_id}", headers=actor.headers)
    assert detail.status_code == 200, detail.text
    return [s["signal_type"] for s in detail.json()["risk_signals"]]


class TestAnApprovedPositionBecomesTheBaseline:
    def test_two_venues_40km_apart_within_a_minute(
            self, client, enrolled_student, make_session, passing_submission):
        """The headline case, and the one the geofence cannot see.

        The second submission sits exactly on its OWN session's venue, so
        every distance check agrees with it. Only the student's own history
        contradicts it.
        """
        here = make_session(name="Lecture at the main campus")
        first = submit(client, enrolled_student,
                       passing_submission(enrolled_student, here["id"]))
        assert first.status_code == 201, first.text
        assert first.json()["status"] == "approved", "baseline must be approved"

        there = make_session(
            name="Lab at the other campus",
            venue_latitude=FAR_LAT, venue_longitude=FAR_LON,
            geofence_radius_meters=100.0,
        )
        second = submit(client, enrolled_student, passing_submission(
            enrolled_student, there["id"],
            latitude=FAR_LAT, longitude=FAR_LON,
        ))
        assert second.status_code == 201, second.text

        body = second.json()
        assert "impossible_travel" in signals_on(
            client, enrolled_student, body["id"])
        assert body["status"] == "flagged"

    def test_the_same_two_venues_hours_apart_are_fine(
            self, client, enrolled_student, make_session, passing_submission):
        """A control the other test needs to be worth anything.

        Nothing here is hours apart -- the whole run takes milliseconds -- so
        this instead removes the baseline the way an ordinary student does: by
        having no approved position on record yet. A first-ever check-in
        40 km from anywhere the student has been is simply a check-in.
        """
        there = make_session(
            name="Lab at the other campus",
            venue_latitude=FAR_LAT, venue_longitude=FAR_LON,
            geofence_radius_meters=100.0,
        )
        only = submit(client, enrolled_student, passing_submission(
            enrolled_student, there["id"],
            latitude=FAR_LAT, longitude=FAR_LON,
        ))
        assert only.status_code == 201, only.text
        assert "impossible_travel" not in signals_on(
            client, enrolled_student, only.json()["id"])
        assert only.json()["status"] == "approved"


class TestARejectedPositionIsNotABaseline:
    """A stale OS fix must not cost an honest student their attendance.

    The engine rejected the first position, so it is not something we believe
    happened; reasoning from it would turn the ordinary stale-fix retry -- the
    reason RiskService._retries gives for a second go existing at all -- into
    an accusation. See CheckInRepository.find_last_positioned_attempt.
    """

    def test_retrying_from_the_venue_after_a_rejection_still_approves(
            self, client, enrolled_student, make_session, passing_submission):
        session = make_session()
        rejected = submit(client, enrolled_student, {
            "session_id": session["id"],
            "latitude": FAR_LAT, "longitude": FAR_LON,
            "location_accuracy_meters": 5.0,
        })
        assert rejected.status_code == 201, rejected.text
        assert rejected.json()["status"] == "rejected", "beyond 2x the geofence"

        retried = submit(
            client, enrolled_student,
            passing_submission(enrolled_student, session["id"]),
            retry=True,
        )
        assert retried.status_code == 201, retried.text
        assert "impossible_travel" not in signals_on(
            client, enrolled_student, retried.json()["id"])
        assert retried.json()["status"] == "approved"
