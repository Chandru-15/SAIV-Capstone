"""Server-issued liveness challenges.

Why this exists at all: module 3 is stateless. It can tell you *whether* a
blink happened in a set of frames, but not whether that blink was the one you
asked for, just now, from this student. Without that binding, active liveness
is theatre — a client that picks its own challenge type can replay a
pre-recorded blink video forever.

So module 2 issues the challenge:

  * the **server** chooses the type, unpredictably, so the client cannot
    pre-record the answer;
  * it is bound to one student and one session;
  * it expires in seconds;
  * it is single-use — consumed on submission, so captured frames cannot be
    replayed even inside the window.

Storage is Redis when available (TTL expiry is exactly the semantics needed,
and it is already in docker-compose), falling back to an in-process dict so
tests and single-worker development need no Redis.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Protocol, Sequence, Tuple

from ..models.base import utcnow

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6380")

# Long enough to read the instruction and perform the action, short enough that
# a captured frame set is worthless by the time it could be replayed.
CHALLENGE_TTL_SECONDS = 60

# Active challenge types module 3 can verify across frames. `passive` is
# excluded deliberately: it needs no challenge, so issuing one would imply a
# guarantee we are not making.
ACTIVE_CHALLENGE_TYPES = ("blink", "head_turn", "colour_reflection")

# -- the colour-reflection challenge ------------------------------------------
#
# Blink and head-turn ask the subject to move, and a good video replay moves
# correctly. This one asks the subject's SURROUNDINGS to change: the client
# screen is driven through a sequence of saturated colours, and the face must
# be seen to be lit by them. A recording cannot comply, because the sequence
# did not exist when it was made.
#
# Everything security-relevant about it lives on this side of the wire. Module
# 3 measures how the face responded; only we know what it was supposed to
# respond to, so only we can say whether it is the answer we asked for. The
# client is never told it may choose the sequence, and the per-frame pairing is
# derived here (see `colours_per_frame`) rather than accepted from the
# submission -- a client that could label its own frames could relabel them to
# fit whatever it managed to capture, which would reduce the whole challenge to
# "did the screen change colour at all".

# Saturated primaries and secondaries. The palette matters more than it looks:
# module 3's shape test needs the screen's contribution to vary by a large
# FACTOR across the sequence, so what counts is the ratio of the brightest to
# the dimmest colour, not how spread out they look. Blue carries only 7% of
# white's luminance and yellow 93%, which is where the ratio comes from; six
# pretty pastels span the range by difference and still only reach about 6,
# at which a live face measures below module 3's threshold and is refused for
# being alive.
COLOUR_PALETTE = ("#ff0000", "#00ff00", "#0000ff", "#00ffff", "#ff00ff",
                  "#ffff00", "#ffffff")

# Rec. 709 luminance of each, in linear light. Hard-coded rather than computed:
# this is a property of the palette above, and a test asserts the two agree.
_COLOUR_LUMINANCE = {
    "#ff0000": 0.2126, "#00ff00": 0.7152, "#0000ff": 0.0722,
    "#00ffff": 0.7874, "#ff00ff": 0.2848, "#ffff00": 0.9278,
    "#ffffff": 1.0000,
}

# Module 3 refuses to judge shape below this ratio, so every sequence we issue
# must clear it. Kept slightly above module 3's own 8.0 so a rounding
# difference between the services cannot produce an unverifiable challenge.
MIN_LUMINANCE_RATIO = 9.0

# The dimmest colour in the palette and the bright ones that pair with it to
# clear the ratio on their own. Blue over green is 9.9, the worst of the four,
# so seeding every sequence with blue plus one of these makes the requirement
# structural rather than a matter of luck -- a uniform random draw of six from
# seven colours falls short of it surprisingly often.
_DARKEST = "#0000ff"
_BRIGHT_ENOUGH = ("#00ff00", "#00ffff", "#ffff00", "#ffffff")

# Nine, measured rather than chosen. Scoring 589 CASIA-FASD clips against
# sequences this server would actually issue -- the same footage re-scored at
# every length it can support, so the comparison is within-clip -- gives a
# per-attempt false-accept rate that falls monotonically with the number of
# steps:
#
#     steps     4       5       6       7       8       9      10     13+
#     FAR    2.56%   2.17%   1.24%   1.12%   0.71%   0.24%   0.14%   0.00%
#
# The cause is visible in the same table: the 99th-percentile chromaticity
# correlation of footage that was NOT lit by the sequence falls from 0.81 at
# four steps to 0.51 at nine, crossing below CORRELATION_MIN. A correlation
# above the threshold is easy to hit by luck over four samples and hard over
# nine, so sequence length -- not the threshold -- is the lever here.
#
# Holding the clip population fixed to measure the change itself: the same 104
# clips, 20 sequences each, 2080 scored sequences either way -- 31 false
# accepts (1.49%) at six steps against 10 (0.48%) at nine, a 3.1x reduction.
#
# Raising this is close to free, which is why it is the lever worth pulling:
#   * the flash rate is 1000/COLOUR_HOLD_MS and does not depend on the step
#     count, so the WCAG margin below is untouched;
#   * 9 x 2 = 18 frames, within module 3's 30-frame cap;
#   * a genuine response correlates near 1 at any length, so more steps raise
#     the attacker's difficulty without raising an honest student's -- unlike
#     a tighter threshold, which would trade false accepts for false rejects
#     that no public dataset can measure.
# The cost is 1.4 s more capture time (4.32 s, against a 60 s challenge TTL).
COLOUR_STEPS = 9
# Two frames per colour, so one unusable frame per step does not cost the step.
COLOUR_FRAMES_PER_STEP = 2

# How long each colour is held, and how long after the change the first frame
# is taken. The settle delay is not politeness: an LCD takes a few tens of
# milliseconds to finish a transition, and a camera's auto-exposure and
# auto-white-balance start chasing the new illuminant immediately. Capturing
# too early catches the previous colour; capturing too late catches the
# camera's compensation for this one.
COLOUR_HOLD_MS = 480
COLOUR_SETTLE_MS = 200

# A change every 480 ms is about 2.1 per second, deliberately under the
# three-per-second general flash threshold in WCAG 2.3.1, which is the rate at
# which large-area flashing becomes a photosensitive-seizure risk. The rate is
# set by this hold time alone and not by COLOUR_STEPS -- more steps make the
# capture longer, not faster -- so it is this value that must never drop below
# about 334 ms.
# The palette also contains no black, so no step is a full-contrast blackout.
_MAX_CHANGES_PER_SECOND = 3.0

# A blink needs open -> closed -> open, so three frames is the floor. Module 3
# caps the upper end at 30.
MIN_FRAMES = {"blink": 3, "head_turn": 2,
              # Exact, not a floor: see `colours_per_frame`. The frame count is
              # how the server knows which colour each frame belongs to.
              "colour_reflection": COLOUR_STEPS * COLOUR_FRAMES_PER_STEP}
RECOMMENDED_FRAMES = {"blink": 8, "head_turn": 8,
                      "colour_reflection": COLOUR_STEPS * COLOUR_FRAMES_PER_STEP}

INSTRUCTIONS = {
    "blink": "Look at the camera and blink once, slowly.",
    "head_turn": "Look at the camera, then slowly turn your head to one side.",
    "colour_reflection": (
        "Hold the camera at arm's length and look at the screen while it "
        "changes colour. Keep still; you do not need to do anything."),
}

_KEY_PREFIX = "saiv:liveness:challenge:"


@dataclass(frozen=True)
class LivenessChallenge:
    challenge_id: str
    challenge_type: str
    student_id: str
    session_id: str
    issued_at: str
    expires_at: str
    min_frames: int
    recommended_frames: int
    # colour_reflection only. Empty for every other type.
    colour_sequence: Tuple[str, ...] = ()
    hold_ms: int = 0
    settle_ms: int = 0
    frames_per_step: int = 1

    @property
    def instruction(self) -> str:
        return INSTRUCTIONS.get(self.challenge_type, "Follow the on-screen prompt.")

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        return datetime.fromisoformat(self.expires_at) < (now or utcnow())

    @property
    def expected_frames(self) -> int:
        """How many frames a correct submission carries.

        For colour_reflection this is exact and enforced, not a minimum. The
        submission is an ordered list with no labels, so the colour each frame
        was taken under is recovered from its POSITION -- which only works if
        the count is the one we asked for. Accepting a short list would mean
        guessing which steps were dropped, and every guess scores some frames
        against the wrong colour.
        """
        if self.challenge_type == "colour_reflection":
            return len(self.colour_sequence) * self.frames_per_step
        return self.min_frames

    @property
    def colours_per_frame(self) -> Tuple[str, ...]:
        """The colour each frame was taken under, derived here and only here.

        Module 3 needs one colour per frame. Deriving it from the stored
        sequence keeps the pairing on the server: a client that supplied its
        own labels could relabel captured frames to match whatever light it
        actually had, and the challenge would no longer be asking anything.
        """
        return tuple(colour for colour in self.colour_sequence
                     for _ in range(self.frames_per_step))

    @property
    def total_duration_ms(self) -> int:
        return len(self.colour_sequence) * self.hold_ms


class ChallengeStore(Protocol):
    def put(self, challenge: LivenessChallenge, ttl_seconds: int) -> None: ...
    def take(self, challenge_id: str) -> Optional[LivenessChallenge]: ...


class InMemoryChallengeStore:
    """Fallback store.

    Correct for tests and a single worker. With several uvicorn workers a
    challenge issued by one process is invisible to the others, so production
    should run Redis — hence the warning when this is selected.
    """

    def __init__(self) -> None:
        self._items: Dict[str, LivenessChallenge] = {}

    def put(self, challenge: LivenessChallenge, ttl_seconds: int) -> None:
        self._items[challenge.challenge_id] = challenge

    def take(self, challenge_id: str) -> Optional[LivenessChallenge]:
        # pop, not get: single-use is the anti-replay property.
        challenge = self._items.pop(challenge_id, None)
        if challenge is not None and challenge.is_expired():
            return None
        return challenge


class RedisChallengeStore:
    def __init__(self, client) -> None:
        self._redis = client

    def put(self, challenge: LivenessChallenge, ttl_seconds: int) -> None:
        self._redis.setex(_KEY_PREFIX + challenge.challenge_id, ttl_seconds,
                          json.dumps(asdict(challenge)))

    def take(self, challenge_id: str) -> Optional[LivenessChallenge]:
        key = _KEY_PREFIX + challenge_id
        # GETDEL makes consumption atomic: two concurrent submissions of the
        # same challenge cannot both succeed.
        raw = self._redis.getdel(key)
        if raw is None:
            return None
        data = json.loads(raw)
        # JSON has no tuple, so the sequence comes back as a list. Restoring
        # the tuple keeps the frozen dataclass hashable and stops a caller
        # mutating a challenge it was only meant to read.
        data["colour_sequence"] = tuple(data.get("colour_sequence") or ())
        challenge = LivenessChallenge(**data)
        return None if challenge.is_expired() else challenge


def build_store() -> ChallengeStore:
    """Redis if reachable, in-memory otherwise."""
    try:
        import redis

        client = redis.Redis.from_url(REDIS_URL, decode_responses=True,
                                      socket_connect_timeout=1, socket_timeout=1)
        client.ping()
        return RedisChallengeStore(client)
    except Exception as exc:
        logger.warning(
            "Redis unavailable at %s (%s); using in-process liveness challenge "
            "store. Fine for one worker, NOT safe across multiple workers.",
            REDIS_URL, type(exc).__name__)
        return InMemoryChallengeStore()


def luminance_ratio(sequence: Sequence[str]) -> float:
    """Brightest colour over dimmest. Module 3 will not judge shape below
    MIN_LUMINANCE_RATIO; see the palette comment for why it is a ratio."""
    values = [_COLOUR_LUMINANCE[colour] for colour in sequence]
    if not values:
        return 0.0
    return max(values) / max(min(values), 1e-9)


def build_colour_sequence(steps: int = COLOUR_STEPS) -> Tuple[str, ...]:
    """A fresh colour sequence: unpredictable, but never unverifiable.

    Two properties have to hold at once, and a uniform random draw gives
    neither reliably:

      * the luminance ratio must clear MIN_LUMINANCE_RATIO, or module 3
        cannot tell a face from a photograph and reports no verdict. Seeding
        every sequence with the darkest colour plus one bright one makes that
        structural instead of lucky -- six colours drawn uniformly from seven
        fall short often enough to matter, which the benchmark script found
        before this guard existed.

      * no two adjacent steps may repeat, since a repeat is a step during
        which nothing changes: it buys no evidence and halves the rate of
        change the student actually sees.

    What is left over is still unpredictable, which is all the challenge
    needs: the client cannot pre-compute an answer, and has one single-use
    attempt inside sixty seconds to guess the ORDER.
    """
    if steps < 3:
        raise ValueError("A colour sequence needs at least 3 steps")

    seeded = [_DARKEST, secrets.choice(_BRIGHT_ENOUGH)]
    rest = [secrets.choice(COLOUR_PALETTE) for _ in range(steps - len(seeded))]
    sequence = seeded + rest

    # Fisher-Yates with a CSPRNG. secrets has no shuffle of its own, and
    # random.shuffle is seeded from a predictable source.
    for i in range(len(sequence) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        sequence[i], sequence[j] = sequence[j], sequence[i]

    # Break up adjacent repeats by swapping in any colour that differs from
    # both neighbours. Bounded and terminating: each position is visited once.
    for i in range(1, len(sequence)):
        if sequence[i] != sequence[i - 1]:
            continue
        neighbours = {sequence[i - 1]}
        if i + 1 < len(sequence):
            neighbours.add(sequence[i + 1])
        options = [c for c in COLOUR_PALETTE if c not in neighbours]
        if options:
            sequence[i] = secrets.choice(options)

    result = tuple(sequence)
    # Defence in depth: if the construction above is ever changed in a way
    # that breaks the ratio, fail here rather than issue a challenge no
    # verifier can answer.
    ratio = luminance_ratio(result)
    if ratio < MIN_LUMINANCE_RATIO:
        raise AssertionError(
            f"Built a colour sequence with luminance ratio {ratio:.2f}, "
            f"below the {MIN_LUMINANCE_RATIO} module 3 needs: {result}")
    return result


def issue(student_id: str, session_id: str,
          challenge_type: Optional[str] = None) -> LivenessChallenge:
    """Mint a challenge.

    ``challenge_type`` is overridable for tests only. In the request path it is
    left None so the server chooses: a client that can pick its own challenge
    can prepare the answer in advance, which defeats the point.
    """
    chosen = challenge_type or secrets.choice(ACTIVE_CHALLENGE_TYPES)
    now = utcnow()
    colour = chosen == "colour_reflection"
    return LivenessChallenge(
        challenge_id=secrets.token_urlsafe(24),
        challenge_type=chosen,
        student_id=student_id,
        session_id=session_id,
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(seconds=CHALLENGE_TTL_SECONDS)).isoformat(),
        min_frames=MIN_FRAMES[chosen],
        recommended_frames=RECOMMENDED_FRAMES[chosen],
        colour_sequence=build_colour_sequence() if colour else (),
        hold_ms=COLOUR_HOLD_MS if colour else 0,
        settle_ms=COLOUR_SETTLE_MS if colour else 0,
        frames_per_step=COLOUR_FRAMES_PER_STEP if colour else 1,
    )
