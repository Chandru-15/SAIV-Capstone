"""Appeal rules: one appeal per check-in, within the appeal window.

test_checkin_flow.py covers the basic appeal round trip; these pin the limits
added with the Module 4 dashboard.
"""

from datetime import timedelta

import pytest

pytestmark = pytest.mark.flow


@pytest.fixture
def flagged(client, enrolled_student, make_session, at_venue, face_stub) -> dict:
    face_stub["liveness_body"] = {**face_stub["liveness_body"],
                                  "liveness_passed": True, "liveness_score": 0.25}
    body = client.post("/checkins/", json={"session_id": make_session()["id"], **at_venue},
                       headers=enrolled_student.headers).json()
    assert body["status"] == "flagged", body
    return body


def appeal(client, student, checkin_id, reason="I was in the room"):
    return client.post(f"/checkins/{checkin_id}/appeal", json={"reason": reason},
                       headers=student.headers)


def my_row(client, student, checkin_id) -> dict:
    items = client.get("/checkins/me", headers=student.headers).json()["items"]
    return next(item for item in items if item["id"] == checkin_id)


class TestOneAppealPerCheckIn:
    def test_a_decided_appeal_is_final(self, client, instructor, enrolled_student, flagged):
        """Without this, a rejected appeal could be re-filed forever: a rejected
        check-in is itself appealable."""
        assert appeal(client, enrolled_student, flagged["id"]).status_code == 200
        client.patch(f"/checkins/{flagged['id']}/review",
                     json={"approve": False, "notes": "Location was 400 m away"},
                     headers=instructor.headers)
        again = appeal(client, enrolled_student, flagged["id"], "please look again")
        assert again.status_code == 400
        assert "already been appealed" in again.json()["message"]

    def test_the_decision_and_note_reach_the_student(self, client, instructor,
                                                     enrolled_student, flagged):
        appeal(client, enrolled_student, flagged["id"])
        client.patch(f"/checkins/{flagged['id']}/review",
                     json={"approve": False, "notes": "Location was 400 m away"},
                     headers=instructor.headers)
        row = my_row(client, enrolled_student, flagged["id"])
        assert row["status"] == "rejected"
        assert row["review_notes"] == "Location was 400 m away"
        assert row["appeal_reason"] == "I was in the room"


class TestAppealWindow:
    def test_appeal_refused_after_the_window(self, client, enrolled_student, flagged, db):
        from app.models import CheckIn
        from app.models.checkin import APPEAL_WINDOW_DAYS

        row = db.get(CheckIn, flagged["id"])
        row.checked_in_at = row.checked_in_at - timedelta(days=APPEAL_WINDOW_DAYS + 1)
        db.commit()

        refused = appeal(client, enrolled_student, flagged["id"])
        assert refused.status_code == 400
        assert "window" in refused.json()["message"]

    def test_a_blank_reason_is_refused(self, client, enrolled_student, flagged):
        refused = appeal(client, enrolled_student, flagged["id"], reason="   ")
        assert refused.status_code == 400


class TestCanAppealField:
    """Clients can show or hide their Appeal button from the response alone."""

    def test_flagged_check_in_can_be_appealed(self, client, enrolled_student, flagged):
        row = my_row(client, enrolled_student, flagged["id"])
        assert row["can_appeal"] is True
        assert row["appeal_deadline"] is not None

    def test_not_once_appealed(self, client, enrolled_student, flagged):
        appeal(client, enrolled_student, flagged["id"])
        assert my_row(client, enrolled_student, flagged["id"])["can_appeal"] is False

    def test_an_approved_check_in_cannot_be_appealed(self, client, enrolled_student,
                                                     make_session, passing_submission):
        created = client.post(
            "/checkins/",
            json=passing_submission(enrolled_student, make_session()["id"]),
            headers=enrolled_student.headers).json()
        assert created["status"] == "approved"
        assert my_row(client, enrolled_student, created["id"])["can_appeal"] is False