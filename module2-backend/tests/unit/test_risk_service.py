"""RiskService — pure scoring, no database.

The whole point of keeping RiskService free of a session is that its rules can
be tested exhaustively at this speed.
"""

import json

import pytest

from app.models import RiskSeverity, RiskSignalType
from app.services.risk_service import (
    WEIGHT_FACE_MATCH,
    WEIGHT_GEO,
    CheckInContext,
    RiskService,
    haversine_meters,
    severity_for,
)

pytestmark = pytest.mark.unit

VENUE = (1.2966, 103.7764)


def ctx(**overrides) -> CheckInContext:
    base = dict(
        latitude=VENUE[0], longitude=VENUE[1], location_accuracy_meters=5.0,
        venue_latitude=VENUE[0], venue_longitude=VENUE[1],
        geofence_radius_meters=100.0,
        require_liveness=True, liveness_passed=True, liveness_score=0.95,
        # These tests pin the SCORING formula, so the baseline is a verdict
        # that is admissible: a server-issued challenge. Admissibility itself
        # is pinned separately in TestLivenessMustBeChallenged below.
        liveness_challenge_type="blink",
        # No attestation_known: the baseline is an ordinary browser, which has
        # no attestation verdict either way. Tests that pin the attestation
        # rule set both fields explicitly.
        device_known=True,
    )
    base.update(overrides)
    return CheckInContext(**base)


@pytest.fixture
def risk() -> RiskService:
    return RiskService()


class TestGeolocation:
    def test_at_venue_raises_no_geo_signal(self, risk):
        result = risk.assess(ctx())
        assert not any(s.signal_type == RiskSignalType.GEO_OUT_OF_BOUNDS
                       for s in result.signals)
        assert result.distance_from_venue_meters == pytest.approx(0, abs=1)

    def test_outside_fence_raises_signal(self, risk):
        # ~330m north of the venue, well outside a 100m fence.
        result = risk.assess(ctx(latitude=VENUE[0] + 0.003))
        assert any(s.signal_type == RiskSignalType.GEO_OUT_OF_BOUNDS
                   for s in result.signals)
        assert result.distance_from_venue_meters > 100

    def test_risk_ramps_with_distance(self, risk):
        near = risk.assess(ctx(latitude=VENUE[0] + 0.0002)).breakdown["geolocation"]
        far = risk.assess(ctx(latitude=VENUE[0] + 0.0008)).breakdown["geolocation"]
        assert far > near

    def test_absent_location_is_full_risk_not_a_pass(self, risk):
        """Missing coordinates are unverifiable, not innocent. If they scored
        zero, withholding consent would be the cheapest way to look clean."""
        result = risk.assess(ctx(latitude=None, longitude=None))
        assert result.breakdown["geolocation"] == 1.0
        assert result.risk_score > risk.assess(ctx()).risk_score

    def test_poor_accuracy_flags_signal(self, risk):
        result = risk.assess(ctx(location_accuracy_meters=500.0))
        assert any(s.signal_type == RiskSignalType.GEO_ACCURACY_LOW
                   for s in result.signals)

    def test_unknown_venue_is_not_scored(self, risk):
        result = risk.assess(ctx(venue_latitude=None, venue_longitude=None))
        assert "geolocation" not in result.breakdown
        assert result.distance_from_venue_meters is None


class TestLivenessAndFace:
    def test_liveness_failure_is_maximum_risk(self, risk):
        result = risk.assess(ctx(liveness_passed=False, liveness_score=0.1))
        assert result.breakdown["liveness"] == 1.0
        assert any(s.signal_type == RiskSignalType.LIVENESS_FAILED for s in result.signals)

    def test_low_liveness_score_inverts_to_risk(self, risk):
        result = risk.assess(ctx(liveness_score=0.3))
        assert result.breakdown["liveness"] == pytest.approx(0.7)

    def test_missing_liveness_when_required_is_full_risk(self, risk):
        assert risk.assess(ctx(liveness_score=None)).breakdown["liveness"] == 1.0

    def test_liveness_not_required_is_excluded(self, risk):
        assert "liveness" not in risk.assess(ctx(require_liveness=False)).breakdown

    def test_an_unchallenged_verdict_scores_as_full_risk(self, risk):
        """Not 1 - 0.93. A passive verdict is not weak evidence of liveness,
        it is no evidence of it, so it is scored exactly as a missing one."""
        result = risk.assess(ctx(liveness_challenge_type="passive", liveness_score=0.93))
        assert result.breakdown["liveness"] == 1.0

    def test_an_unchallenged_verdict_raises_replay_suspected(self, risk):
        result = risk.assess(ctx(liveness_challenge_type="passive"))
        signal = next(s for s in result.signals
                      if s.signal_type == RiskSignalType.REPLAY_SUSPECTED)
        # details is persisted as a JSON string, hence the decode.
        details = json.loads(signal.details)
        assert details["liveness_challenge_type"] == "passive"
        assert details["reason"] == "no_server_issued_challenge"
        assert "Complete the on-screen liveness challenge" in result.recommendations

    @pytest.mark.parametrize("challenge_type", ["blink", "head_turn"])
    def test_a_challenged_verdict_raises_no_such_signal(self, risk, challenge_type):
        result = risk.assess(ctx(liveness_challenge_type=challenge_type))
        assert not any(s.signal_type == RiskSignalType.REPLAY_SUSPECTED
                       for s in result.signals)

    def test_no_evidence_at_all_is_not_called_a_replay(self, risk):
        """A type of None means nothing biometric was submitted, or module 3
        was down. Both are already full risk via the missing score; labelling
        them a suspected replay would send a reviewer hunting an attacker who
        is usually a student with a broken camera."""
        result = risk.assess(ctx(liveness_challenge_type=None,
                                 liveness_passed=None, liveness_score=None))
        assert result.breakdown["liveness"] == 1.0
        assert not any(s.signal_type == RiskSignalType.REPLAY_SUSPECTED
                       for s in result.signals)

    def test_a_session_not_requiring_liveness_is_not_judged_on_the_path(self, risk):
        """A face-match-only session uses the passive image path by design."""
        result = risk.assess(ctx(require_liveness=False,
                                 liveness_challenge_type="passive"))
        assert not any(s.signal_type == RiskSignalType.REPLAY_SUSPECTED
                       for s in result.signals)

    def test_an_unchallenged_failure_reports_both_reasons(self, risk):
        """The paths are independent facts: it was not challenged, AND module 3
        said no. A reviewer needs both to tell a spoof attempt from a bad
        photo."""
        result = risk.assess(ctx(liveness_challenge_type="passive",
                                 liveness_passed=False, liveness_score=0.1))
        kinds = {s.signal_type for s in result.signals}
        assert RiskSignalType.REPLAY_SUSPECTED in kinds
        assert RiskSignalType.LIVENESS_FAILED in kinds

    def test_absent_face_match_does_not_dilute_the_score(self, risk):
        """A disabled check must be excluded and the remaining weights
        renormalised. Counting it as zero risk would let a lax session score
        artificially low and slip under the threshold."""
        without = risk.assess(ctx(require_face_match=False))
        with_perfect = risk.assess(ctx(require_face_match=True, face_match_score=1.0))
        assert WEIGHT_FACE_MATCH not in (0,)
        assert "face_match" not in without.breakdown
        # Adding a *passing* face check must not change the verdict materially.
        assert with_perfect.risk_score <= without.risk_score

    def test_face_mismatch_raises_risk(self, risk):
        clean = risk.assess(ctx())
        bad = risk.assess(ctx(require_face_match=True, face_match_score=0.1))
        assert bad.risk_score > clean.risk_score
        assert any(s.signal_type == RiskSignalType.FACE_MATCH_LOW_CONFIDENCE
                   for s in bad.signals)


class TestDeviceAndNetwork:
    def test_unknown_device_signals(self, risk):
        result = risk.assess(ctx(device_known=False))
        assert any(s.signal_type == RiskSignalType.DEVICE_UNKNOWN for s in result.signals)

    def test_emulator_is_maximum_device_risk(self, risk):
        result = risk.assess(ctx(device_is_emulator=True))
        assert result.breakdown["device"] == 1.0
        assert any(s.signal_type == RiskSignalType.DEVICE_EMULATOR for s in result.signals)

    def test_a_device_that_never_attested_raises_no_signal(self, risk):
        # The normal state of every browser. No ceremony has run, so there is
        # no verdict to hold against it.
        result = risk.assess(ctx(attestation_known=False, attestation_passed=False))
        assert not any(s.signal_type == RiskSignalType.ATTESTATION_FAILED
                       for s in result.signals)
        assert result.breakdown["device"] == 0.0

    def test_a_ceremony_that_ran_and_failed_raises_the_signal(self, risk):
        result = risk.assess(ctx(attestation_known=True, attestation_passed=False))
        assert any(s.signal_type == RiskSignalType.ATTESTATION_FAILED
                   for s in result.signals)
        assert result.breakdown["device"] == pytest.approx(0.7)

    def test_a_ceremony_that_passed_raises_no_signal(self, risk):
        result = risk.assess(ctx(attestation_known=True, attestation_passed=True))
        assert not any(s.signal_type == RiskSignalType.ATTESTATION_FAILED
                       for s in result.signals)
        assert result.breakdown["device"] == 0.0

    def test_registering_a_device_never_costs_more_than_owning_none(self, risk):
        """The regression this rule exists for.

        A registered browser has no attestation verdict, and an unregistered
        one has no device row at all. Scoring the absent verdict as a failure
        made the registered device the worse of the two -- so the only way to
        look clean was to skip device binding entirely, which is the opposite
        of what the feature is for.
        """
        registered = risk.assess(ctx(device_known=True, attestation_known=False,
                                     attestation_passed=False))
        unregistered = risk.assess(ctx(device_known=False, attestation_known=False,
                                       attestation_passed=False))
        assert registered.breakdown["device"] < unregistered.breakdown["device"]
        assert registered.risk_score < unregistered.risk_score

    def test_tor_outranks_vpn(self, risk):
        vpn = risk.assess(ctx(vpn_detected=True)).breakdown["network"]
        tor = risk.assess(ctx(tor_detected=True)).breakdown["network"]
        assert tor > vpn

    def test_clean_network_is_zero_risk(self, risk):
        assert risk.assess(ctx()).breakdown["network"] == 0.0


class TestScoreShape:
    def test_clean_submission_scores_low(self, risk):
        assert risk.assess(ctx()).risk_score < 0.2

    def test_everything_wrong_scores_high(self, risk):
        result = risk.assess(ctx(
            latitude=1.3521, longitude=103.8198,       # miles away
            liveness_passed=False, liveness_score=0.0,
            device_known=False, device_is_emulator=True,
            tor_detected=True,
        ))
        assert result.risk_score > 0.9

    def test_score_stays_within_the_column_bounds(self, risk):
        """risk_score has a CHECK (0..1); a score outside it would be an
        IntegrityError at insert time rather than a clean rejection."""
        for context in (ctx(), ctx(tor_detected=True, device_is_emulator=True,
                                   liveness_passed=False, latitude=0.0, longitude=0.0)):
            assert 0.0 <= risk.assess(context).risk_score <= 1.0

    def test_has_critical_detects_critical_signals(self, risk):
        assert risk.assess(ctx(liveness_passed=False)).has_critical

    def test_recommendations_are_actionable_and_deduplicated(self, risk):
        result = risk.assess(ctx(vpn_detected=True, liveness_score=0.2))
        assert "Disable VPN for check-in" in result.recommendations
        assert len(result.recommendations) == len(set(result.recommendations))


class TestHelpers:
    @pytest.mark.parametrize("score,expected", [
        (0.0, RiskSeverity.LOW), (0.29, RiskSeverity.LOW),
        (0.3, RiskSeverity.MEDIUM), (0.49, RiskSeverity.MEDIUM),
        (0.5, RiskSeverity.HIGH), (0.69, RiskSeverity.HIGH),
        (0.7, RiskSeverity.CRITICAL), (1.0, RiskSeverity.CRITICAL),
    ])
    def test_severity_bands_match_the_spec(self, score, expected):
        assert severity_for(score) == expected

    def test_haversine_matches_known_distance(self):
        """NTU to Marina Bay is about 12 km."""
        metres = haversine_meters(1.2966, 103.7764, 1.2836, 103.8607)
        assert 9_000 < metres < 12_000

    def test_haversine_is_zero_for_identical_points(self):
        assert haversine_meters(*VENUE, *VENUE) == pytest.approx(0, abs=0.001)
