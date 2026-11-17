"""The two bounds in CheckInService._decide.

    weighted mean of component risks  < threshold    (overall budget)
    a REQUIRED component's own risk   < threshold    (no single catastrophic
                                                      signal hidden by averaging)

Both on the risk scale. A component's risk is ``1 - score``, so the second is
equivalent to ``score > 1 - threshold``.
"""

import pytest

from app.models import CheckInStatus
from app.services.checkin_service import CheckInService
from app.services.risk_service import CheckInContext, RiskService

pytestmark = pytest.mark.unit


class FakeSession:
    require_liveness_check = True
    require_face_match = False

    def __init__(self, threshold=None):
        self.risk_threshold = threshold


class FakeCourse:
    risk_threshold = 0.5
    require_face_recognition = False


@pytest.fixture
def decide():
    service = CheckInService.__new__(CheckInService)
    engine = RiskService()

    def _decide(threshold=None, **overrides):
        base = dict(
            latitude=1.2966, longitude=103.7764, location_accuracy_meters=5.0,
            venue_latitude=1.2966, venue_longitude=103.7764,
            geofence_radius_meters=100.0, require_liveness=True,
            liveness_passed=True, liveness_score=0.97,
            # An admissible verdict by default: these tests pin where the
            # score bar sits, not how the verdict was obtained.
            liveness_challenge_type="blink",
            device_known=True, attestation_passed=True, network_signal_known=True,
        )
        base.update(overrides)
        ctx = CheckInContext(**base)
        assessment = engine.assess(ctx)
        verdict = service._decide(assessment, ctx, FakeSession(threshold), FakeCourse())
        return assessment.risk_score, verdict

    return _decide


class TestDirectionIsCorrect:
    """A stricter threshold must RAISE the confidence bar, not lower it.

    Comparing a confidence score directly against the risk threshold would
    invert this: at threshold 0.2 a liveness confidence of 0.21 would pass,
    weaker than module 3's calibrated 0.60.
    """

    @pytest.mark.parametrize("threshold,bar", [(0.5, 0.5), (0.4, 0.6), (0.3, 0.7), (0.2, 0.8)])
    def test_bar_is_one_minus_threshold(self, decide, threshold, bar):
        just_under = round(1.0 - bar - 0.01, 4) + bar  # a shade above the bar
        _, verdict = decide(threshold, liveness_score=bar + 0.02)
        assert verdict == CheckInStatus.APPROVED
        _, verdict = decide(threshold, liveness_score=bar - 0.02)
        assert verdict == CheckInStatus.FLAGGED

    def test_tightening_the_threshold_only_ever_tightens(self, decide):
        """Monotonicity: the same submission can go approved -> flagged as the
        threshold drops, never the reverse."""
        verdicts = [decide(t, liveness_score=0.65)[1] for t in (0.5, 0.4, 0.3, 0.2)]
        flagged_from = [i for i, v in enumerate(verdicts) if v == CheckInStatus.FLAGGED]
        assert flagged_from, "a strict enough threshold must eventually flag 0.65"
        # once flagged, it stays flagged for every stricter threshold
        assert flagged_from == list(range(flagged_from[0], len(verdicts)))


class TestModuleThreeCalibrationIsAFloor:
    def test_module_three_verdict_binds_at_the_default_threshold(self, decide):
        """At threshold 0.5 the policy bar is 0.5, looser than module 3's 0.60.
        Module 3's boolean must still rule, or we would accept liveness it
        rejected."""
        _, verdict = decide(0.5, liveness_passed=False, liveness_score=0.55)
        assert verdict == CheckInStatus.REJECTED  # liveness failure is a hard reject

    def test_a_check_that_never_ran_is_not_a_pass(self, decide):
        _, verdict = decide(0.5, liveness_passed=None, liveness_score=None)
        assert verdict == CheckInStatus.FLAGGED


class TestScopedToRequiredComponents:
    """The per-component bound deliberately does NOT apply to device,
    geolocation or network.

    Measured at threshold 0.5: an unregistered device scores risk 1.0 and a
    student who declined geolocation consent scores 1.0, so applying the bound
    universally would flag every first-ever check-in and every
    privacy-conscious student. The review queue would be the whole class.
    """

    def test_unregistered_device_alone_does_not_flag(self, decide):
        score, verdict = decide(0.5, device_known=False)
        assert score < 0.5
        assert verdict == CheckInStatus.APPROVED

    def test_withheld_geolocation_alone_does_not_flag(self, decide):
        score, verdict = decide(0.5, latitude=None, longitude=None)
        assert verdict == CheckInStatus.APPROVED

    def test_vpn_alone_does_not_flag(self, decide):
        _, verdict = decide(0.5, vpn_detected=True)
        assert verdict == CheckInStatus.APPROVED

    def test_but_they_still_accumulate_through_the_mean(self, decide):
        """Excluded from the per-component bound, not from the score."""
        clean, _ = decide(0.5)
        noisy, verdict = decide(0.5, device_known=False, vpn_detected=True,
                                latitude=None, longitude=None)
        assert noisy > clean
        assert verdict == CheckInStatus.FLAGGED, "the mean catches the accumulation"


class TestRequiredFaceMatch:
    class FaceSession(FakeSession):
        require_face_match = True

    def test_weak_but_passing_face_match_fails_a_strict_policy(self):
        service = CheckInService.__new__(CheckInService)
        engine = RiskService()
        ctx = CheckInContext(
            latitude=1.2966, longitude=103.7764, location_accuracy_meters=5.0,
            venue_latitude=1.2966, venue_longitude=103.7764,
            geofence_radius_meters=100.0, require_liveness=True,
            liveness_passed=True, liveness_score=0.97,
            liveness_challenge_type="blink",
            require_face_match=True, face_match_passed=True, face_match_score=0.72,
            device_known=True, attestation_passed=True)
        a = engine.assess(ctx)
        lenient = service._decide(a, ctx, self.FaceSession(0.5), FakeCourse())
        strict = service._decide(a, ctx, self.FaceSession(0.25), FakeCourse())
        assert lenient == CheckInStatus.APPROVED, "0.72 clears module 3's 0.70 and a 0.5 policy"
        assert strict == CheckInStatus.FLAGGED, "0.72 fails a 0.75 bar from threshold 0.25"


class TestLivenessMustBeChallenged:
    """`require_liveness_check` binds the backend, not just a polite client.

    The passive path scores detection confidence, image quality and an inferred
    depth value. None of the three distinguishes a face from a photograph of
    one -- a sharp print scores HIGHER on the first two, not lower. Only an
    active challenge is bound to a type this server chose, for this student,
    seconds ago, once.

    Before this gate existed, a session with require_liveness_check=True was
    satisfied by a single still image: module 3 returned passed=True with a
    score around 0.93 and the check-in was APPROVED. That was reproduced
    end-to-end, not inferred.
    """

    @pytest.mark.parametrize("challenge_type", ["blink", "head_turn"])
    def test_a_server_issued_challenge_satisfies_the_requirement(self, decide, challenge_type):
        _, verdict = decide(0.5, liveness_challenge_type=challenge_type)
        assert verdict == CheckInStatus.APPROVED

    def test_a_passive_verdict_does_not(self, decide):
        """The exact submission that used to be approved."""
        _, verdict = decide(0.5, liveness_challenge_type="passive",
                            liveness_passed=True, liveness_score=0.93)
        assert verdict == CheckInStatus.FLAGGED

    def test_a_high_passive_score_does_not_buy_its_way_past(self, decide):
        """There is no score at which an unchallenged verdict becomes evidence,
        so a perfect 1.0 must not be approved either."""
        _, verdict = decide(0.5, liveness_challenge_type="passive",
                            liveness_passed=True, liveness_score=1.0)
        assert verdict == CheckInStatus.FLAGGED

    def test_a_lenient_threshold_does_not_reopen_it(self, decide):
        """The gate is a requirement, not a budget: raising risk_threshold to
        0.95 buys tolerance for the SCORE and must buy none here."""
        _, verdict = decide(0.95, liveness_challenge_type="passive",
                            liveness_passed=True, liveness_score=0.99)
        assert verdict == CheckInStatus.FLAGGED

    def test_flagged_rather_than_rejected(self, decide):
        """Liveness was not failed, it was not performed. Rejected would claim
        a failure that did not happen, and the student gets no retry path."""
        _, verdict = decide(0.5, liveness_challenge_type="passive",
                            liveness_passed=True, liveness_score=0.93)
        assert verdict != CheckInStatus.REJECTED

    def test_a_failed_active_challenge_is_still_a_hard_rejection(self, decide):
        """The new gate must not downgrade the spec's existing reject rule."""
        _, verdict = decide(0.5, liveness_challenge_type="blink",
                            liveness_passed=False, liveness_score=0.0)
        assert verdict == CheckInStatus.REJECTED

    def test_a_session_not_requiring_liveness_is_untouched(self, decide):
        """A face-match-only session legitimately uses the passive image path,
        and deriveFacePolicy in module 1 sends `image` for exactly that case."""
        _, verdict = decide(0.5, require_liveness=False,
                            liveness_challenge_type="passive")
        assert verdict == CheckInStatus.APPROVED
