"""Server-issued liveness challenges — the anti-replay layer."""

from datetime import timedelta

import pytest

from app.services import liveness_challenge as lc

pytestmark = pytest.mark.unit


class TestIssue:
    def test_type_is_one_of_the_active_challenges(self):
        for _ in range(20):
            assert lc.issue("s", "x").challenge_type in lc.ACTIVE_CHALLENGE_TYPES

    def test_passive_is_never_issued(self):
        """A passive check needs no challenge, so issuing one would imply a
        guarantee we are not making."""
        assert "passive" not in lc.ACTIVE_CHALLENGE_TYPES
        assert ({lc.issue("s", "x").challenge_type for _ in range(60)}
                <= {"blink", "head_turn", "colour_reflection"})

    def test_the_server_chooses_unpredictably(self):
        """If the client could pick, it would pre-record the answer."""
        assert len({lc.issue("s", "x").challenge_type for _ in range(40)}) > 1

    def test_ids_are_unique_and_long_enough_to_be_unguessable(self):
        ids = {lc.issue("s", "x").challenge_id for _ in range(200)}
        assert len(ids) == 200
        assert all(len(i) >= 24 for i in ids)

    def test_bound_to_student_and_session(self):
        c = lc.issue("stu-7", "sess-9")
        assert c.student_id == "stu-7" and c.session_id == "sess-9"

    def test_blink_needs_at_least_three_frames(self):
        """open -> closed -> open cannot be shown in fewer."""
        assert lc.MIN_FRAMES["blink"] >= 3

    def test_head_turn_needs_at_least_two_frames(self):
        assert lc.MIN_FRAMES["head_turn"] >= 2

    def test_carries_a_human_instruction(self):
        assert lc.issue("s", "x").instruction

    def test_expires_within_the_ttl(self):
        c = lc.issue("s", "x")
        from datetime import datetime

        window = datetime.fromisoformat(c.expires_at) - datetime.fromisoformat(c.issued_at)
        assert window == timedelta(seconds=lc.CHALLENGE_TTL_SECONDS)

    def test_ttl_is_short(self):
        """Long enough to blink, short enough that captured frames go stale."""
        assert 15 <= lc.CHALLENGE_TTL_SECONDS <= 180


class TestStore:
    @pytest.fixture
    def store(self):
        return lc.InMemoryChallengeStore()

    def test_round_trip(self, store):
        c = lc.issue("s", "x")
        store.put(c, 60)
        assert store.take(c.challenge_id).challenge_id == c.challenge_id

    def test_single_use(self, store):
        """The anti-replay property: captured frames cannot be resubmitted."""
        c = lc.issue("s", "x")
        store.put(c, 60)
        assert store.take(c.challenge_id) is not None
        assert store.take(c.challenge_id) is None

    def test_unknown_id(self, store):
        assert store.take("never-issued") is None

    def test_expired_challenge_is_refused(self, store, monkeypatch):
        c = lc.issue("s", "x")
        store.put(c, 60)
        from app.models import base as model_base

        later = model_base.utcnow() + timedelta(seconds=lc.CHALLENGE_TTL_SECONDS + 5)
        monkeypatch.setattr(lc, "utcnow", lambda: later)
        assert store.take(c.challenge_id) is None

    def test_is_expired_predicate(self):
        c = lc.issue("s", "x")
        from app.models.base import utcnow

        assert not c.is_expired(utcnow())
        assert c.is_expired(utcnow() + timedelta(seconds=lc.CHALLENGE_TTL_SECONDS + 1))


class TestStoreSelection:
    def test_falls_back_to_memory_without_redis(self, monkeypatch):
        """A Redis outage must not take liveness down with it."""
        import builtins

        real_import = builtins.__import__

        def no_redis(name, *a, **kw):
            if name == "redis":
                raise ImportError("no redis")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", no_redis)
        assert isinstance(lc.build_store(), lc.InMemoryChallengeStore)

    def test_redis_store_consumes_atomically(self):
        """GETDEL, not GET then DEL: two concurrent submissions of the same
        challenge must not both succeed."""
        import json
        from dataclasses import asdict

        calls = []

        class FakeRedis:
            def __init__(self): self.data = {}
            def setex(self, k, ttl, v): self.data[k] = v
            def getdel(self, k):
                calls.append(k)
                return self.data.pop(k, None)

        store = lc.RedisChallengeStore(FakeRedis())
        c = lc.issue("s", "x")
        store.put(c, 60)
        assert store.take(c.challenge_id) is not None
        assert store.take(c.challenge_id) is None
        assert len(calls) == 2


class TestTheColourSequence:
    """What `build_colour_sequence` has to guarantee, and why.

    The sequence is the only part of this challenge the client is told, so it
    has to be unpredictable. But it also has to be *answerable*: module 3
    refuses to judge a face's shape unless the sequence spans a wide enough
    luminance ratio, and a sequence it will not judge is a challenge nobody
    can pass.
    """

    def test_the_palette_luminance_table_is_correct(self):
        """The table is hard-coded for clarity; this is what keeps it honest.

        Rec. 709 luminance of the LINEAR values, which for these fully
        saturated colours is just the sum of the coefficients for the channels
        that are on.
        """
        coefficients = {"r": 0.2126, "g": 0.7152, "b": 0.0722}
        for colour, stated in lc._COLOUR_LUMINANCE.items():
            channels = colour.lstrip("#")
            expected = sum(
                weight for weight, pair in zip(coefficients.values(),
                                               (channels[0:2], channels[2:4], channels[4:6]))
                if pair.lower() == "ff")
            assert stated == pytest.approx(expected, abs=1e-4), colour

    def test_every_sequence_clears_the_ratio_module_3_needs(self):
        for _ in range(500):
            sequence = lc.build_colour_sequence()
            assert lc.luminance_ratio(sequence) >= lc.MIN_LUMINANCE_RATIO

    def test_a_uniform_random_draw_would_not_have(self):
        """Why the construction seeds the dark and bright ends deliberately.

        Drawing six colours uniformly from the palette falls short of the
        ratio often enough to matter -- it is not a rare edge case -- so
        leaving it to chance would hand out challenges that module 3 declines
        to judge, which the student experiences as "try again" forever.
        """
        import secrets
        shortfalls = sum(
            lc.luminance_ratio([secrets.choice(lc.COLOUR_PALETTE)
                                for _ in range(lc.COLOUR_STEPS)])
            < lc.MIN_LUMINANCE_RATIO
            for _ in range(500))
        assert shortfalls > 25, (
            f"only {shortfalls}/500 uniform draws fell short; if this is now "
            "rare the seeding may no longer be needed")

    def test_no_colour_repeats_back_to_back(self):
        """A repeated step buys no evidence -- nothing changes during it --
        and halves the rate of change the student sees."""
        for _ in range(500):
            sequence = lc.build_colour_sequence()
            assert all(a != b for a, b in zip(sequence, sequence[1:])), sequence

    def test_sequences_are_not_predictable(self):
        sequences = {lc.build_colour_sequence() for _ in range(600)}
        assert len(sequences) > 400, f"only {len(sequences)} distinct in 600"

    def test_the_flash_rate_stays_under_the_accessibility_limit(self):
        """WCAG 2.3.1 treats large-area flashing above three per second as a
        photosensitive-seizure risk. Raising COLOUR_STEPS without lengthening
        COLOUR_HOLD_MS would cross it, so the arithmetic is pinned here."""
        rate = lc.COLOUR_STEPS / (lc.COLOUR_STEPS * lc.COLOUR_HOLD_MS / 1000)
        assert rate < lc._MAX_CHANGES_PER_SECOND

    def test_the_palette_contains_no_blackout(self):
        assert all(colour.lstrip("#").lower() != "000000"
                   for colour in lc.COLOUR_PALETTE)

    def test_a_sequence_needs_at_least_three_steps(self):
        with pytest.raises(ValueError):
            lc.build_colour_sequence(2)


class TestTheColourChallenge:
    def test_issue_fills_in_the_colour_fields(self):
        challenge = lc.issue("student", "session", "colour_reflection")
        assert len(challenge.colour_sequence) == lc.COLOUR_STEPS
        assert challenge.hold_ms == lc.COLOUR_HOLD_MS
        assert challenge.settle_ms == lc.COLOUR_SETTLE_MS
        assert challenge.frames_per_step == lc.COLOUR_FRAMES_PER_STEP

    def test_the_other_types_carry_no_colour_fields(self):
        for challenge_type in ("blink", "head_turn"):
            challenge = lc.issue("student", "session", challenge_type)
            assert challenge.colour_sequence == ()
            assert challenge.hold_ms == 0
            assert challenge.frames_per_step == 1

    def test_the_expected_frame_count_is_exact(self):
        challenge = lc.issue("student", "session", "colour_reflection")
        assert challenge.expected_frames == (
            lc.COLOUR_STEPS * lc.COLOUR_FRAMES_PER_STEP)

    def test_each_frame_is_paired_with_its_own_colour(self):
        """The pairing the server keeps to itself.

        Two frames per step means the per-frame list repeats each colour
        twice, in order. Module 3 is given this list; the client is not asked
        for it, because a client that labelled its own frames could relabel
        them to match whatever light it actually had.
        """
        challenge = lc.issue("student", "session", "colour_reflection")
        per_frame = challenge.colours_per_frame
        assert len(per_frame) == challenge.expected_frames
        for index, colour in enumerate(challenge.colour_sequence):
            step = per_frame[index * challenge.frames_per_step:
                             (index + 1) * challenge.frames_per_step]
            assert set(step) == {colour}

    def test_a_gesture_challenge_has_no_per_frame_colours(self):
        assert lc.issue("s", "x", "blink").colours_per_frame == ()

    def test_it_survives_the_redis_round_trip(self):
        """JSON has no tuple, so a naive round trip returns a list and the
        frozen dataclass stops being hashable."""
        import json
        from dataclasses import asdict

        original = lc.issue("student", "session", "colour_reflection")
        restored = lc.LivenessChallenge(**{
            **json.loads(json.dumps(asdict(original))),
            "colour_sequence": tuple(json.loads(json.dumps(
                asdict(original)))["colour_sequence"]),
        })
        assert restored == original
        assert isinstance(restored.colour_sequence, tuple)
        assert hash(restored) == hash(original)
