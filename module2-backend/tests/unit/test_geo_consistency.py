"""The two geolocation CONSISTENCY checks.

A GPS fix arrives in the request body, so a spoofer picks it freely. Neither
check asks "is this fix at the venue" -- the geofence already does -- they ask
whether the fix contradicts something the client does not choose:

    impossible travel   where this student's last fix was, and how long ago
    geo/IP mismatch     where the source address appears to be

Both are deliberately conservative. The speed test has a distance floor so GPS
noise between rapid retries cannot manufacture an aircraft's speed, and the IP
test subtracts the resolver's own stated accuracy radius before comparing.
"""

import json

import pytest

from app.models import CheckInStatus, RiskSeverity, RiskSignalType
from app.services import ip_geolocation
from app.services.checkin_service import CheckInService
from app.services.risk_service import (
    IMPLAUSIBLE_SPEED_KMH,
    IP_MISMATCH_RISK,
    MIN_TRAVEL_DISTANCE_METERS,
    CheckInContext,
    RiskService,
    haversine_meters,
)

pytestmark = pytest.mark.unit

VENUE = (1.2966, 103.7764)

# Offsets from VENUE, measured (not estimated) with haversine_meters:
#   +0.0045 lon ->    500.8 m   below the 1 km jitter floor
#   +0.0090 lon ->   1001.6 m   just above it
#   +0.3600 lon ->  40064.8 m
#   +2.2500 lon -> 250400   m
NEAR_500M = (1.2966, 103.7764 + 0.0045)
JUST_OVER_1KM = (1.2966, 103.7764 + 0.0090)
AWAY_40KM = (1.2966, 103.7764 + 0.3600)
AWAY_250KM = (1.2966, 103.7764 + 2.2500)
FRANKFURT = (50.1109, 8.6821)  # ~10 262 km from VENUE


def ctx(**overrides) -> CheckInContext:
    """A clean submission standing exactly on the venue.

    Standing on the venue is the point: it is what a spoofer submits, so the
    geofence agrees with the lie and only a consistency check can disagree.
    """
    base = dict(
        latitude=VENUE[0], longitude=VENUE[1], location_accuracy_meters=5.0,
        venue_latitude=VENUE[0], venue_longitude=VENUE[1],
        geofence_radius_meters=100.0,
        require_liveness=True, liveness_passed=True, liveness_score=0.97,
        liveness_challenge_type="blink",
        device_known=True, attestation_passed=True, network_signal_known=True,
    )
    base.update(overrides)
    return CheckInContext(**base)


@pytest.fixture
def risk() -> RiskService:
    return RiskService()


def types_of(assessment):
    return [s.signal_type for s in assessment.signals]


def signal_named(assessment, signal_type):
    return next(s for s in assessment.signals if s.signal_type == signal_type)


def geo_risk(assessment) -> float:
    return assessment.breakdown["geolocation"]


class TestImpossibleTravel:
    def test_no_previous_fix_raises_nothing(self, risk):
        """A student's first ever positioned check-in has nothing to contradict."""
        assessment = risk.assess(ctx())
        assert RiskSignalType.IMPOSSIBLE_TRAVEL not in types_of(assessment)
        assert geo_risk(assessment) == 0.0

    def test_teleport_is_detected(self, risk):
        """40 km in a minute is 2 400 km/h."""
        assessment = risk.assess(ctx(
            previous_latitude=AWAY_40KM[0], previous_longitude=AWAY_40KM[1],
            seconds_since_previous_fix=60.0,
        ))
        assert RiskSignalType.IMPOSSIBLE_TRAVEL in types_of(assessment)

    def test_teleport_carries_the_whole_geo_component(self, risk):
        """The fix sits ON the venue, so distance risk is 0.

        Without ``max(risk, consistency)`` in _geo the contradiction would be
        averaged away by the geofence agreeing with the spoofed coordinates.
        """
        assessment = risk.assess(ctx(
            previous_latitude=AWAY_40KM[0], previous_longitude=AWAY_40KM[1],
            seconds_since_previous_fix=60.0,
        ))
        assert geo_risk(assessment) == 1.0
        assert signal_named(
            assessment, RiskSignalType.IMPOSSIBLE_TRAVEL
        ).severity == RiskSeverity.CRITICAL

    def test_gps_jitter_between_rapid_retries_is_not_travel(self, risk):
        """500 m in one second is 1 800 km/h, and means nothing.

        This is the false positive that matters: a phone indoors wanders
        between fixes, and retries are seconds apart. The distance floor, not
        the speed limit, is what makes the check safe to apply to retries.
        """
        assert haversine_meters(*VENUE, *NEAR_500M) < MIN_TRAVEL_DISTANCE_METERS
        assessment = risk.assess(ctx(
            previous_latitude=NEAR_500M[0], previous_longitude=NEAR_500M[1],
            seconds_since_previous_fix=1.0,
        ))
        assert RiskSignalType.IMPOSSIBLE_TRAVEL not in types_of(assessment)
        assert geo_risk(assessment) == 0.0

    def test_just_over_the_floor_is_judged_on_speed(self, risk):
        """1 001 m in one second clears the floor and is 3 600 km/h."""
        assert haversine_meters(*VENUE, *JUST_OVER_1KM) > MIN_TRAVEL_DISTANCE_METERS
        assessment = risk.assess(ctx(
            previous_latitude=JUST_OVER_1KM[0], previous_longitude=JUST_OVER_1KM[1],
            seconds_since_previous_fix=1.0,
        ))
        assert RiskSignalType.IMPOSSIBLE_TRAVEL in types_of(assessment)

    def test_ordinary_commute_raises_nothing(self, risk):
        """40 km in two hours is 20 km/h. A bus, not a teleport."""
        assessment = risk.assess(ctx(
            previous_latitude=AWAY_40KM[0], previous_longitude=AWAY_40KM[1],
            seconds_since_previous_fix=7200.0,
        ))
        assert RiskSignalType.IMPOSSIBLE_TRAVEL not in types_of(assessment)

    @pytest.mark.parametrize("elapsed,expected", [
        (720.0, True),   # 200.32 km/h -- over
        (722.0, False),  # 199.77 km/h -- under
    ])
    def test_speed_limit_boundary(self, risk, elapsed, expected):
        """40 064.8 m / 721.17 s is exactly IMPLAUSIBLE_SPEED_KMH."""
        assessment = risk.assess(ctx(
            previous_latitude=AWAY_40KM[0], previous_longitude=AWAY_40KM[1],
            seconds_since_previous_fix=elapsed,
        ))
        fired = RiskSignalType.IMPOSSIBLE_TRAVEL in types_of(assessment)
        assert fired is expected

    def test_two_positions_at_the_same_instant(self, risk):
        """Zero elapsed is infinite speed, and must stay valid JSON."""
        assessment = risk.assess(ctx(
            previous_latitude=AWAY_40KM[0], previous_longitude=AWAY_40KM[1],
            seconds_since_previous_fix=0.0,
        ))
        signal = signal_named(assessment, RiskSignalType.IMPOSSIBLE_TRAVEL)
        details = json.loads(signal.details)
        assert details["implied_speed_kmh"] is None
        assert details["elapsed_seconds"] == 0.0
        assert details["limit_kmh"] == IMPLAUSIBLE_SPEED_KMH

    def test_negative_interval_is_not_evidence(self, risk):
        """A fix 'before' an earlier one is a fault in our own data.

        Absent evidence rather than evidence of fraud: a clock or ordering
        problem must not cost a student their attendance mark.
        """
        assessment = risk.assess(ctx(
            previous_latitude=AWAY_40KM[0], previous_longitude=AWAY_40KM[1],
            seconds_since_previous_fix=-30.0,
        ))
        assert RiskSignalType.IMPOSSIBLE_TRAVEL not in types_of(assessment)

    def test_no_current_fix_means_nothing_to_compare(self, risk):
        """A withheld location is already charged full geo risk, once."""
        assessment = risk.assess(ctx(
            latitude=None, longitude=None,
            previous_latitude=AWAY_40KM[0], previous_longitude=AWAY_40KM[1],
            seconds_since_previous_fix=1.0,
        ))
        assert RiskSignalType.IMPOSSIBLE_TRAVEL not in types_of(assessment)
        assert geo_risk(assessment) == 1.0

    def test_detected_even_when_the_session_has_no_venue(self, risk):
        """The speed test needs no venue, so it survives a session without one.

        _geo returns early when there is nothing to measure distance against;
        a contradiction found before that has to carry the component itself.
        """
        assessment = risk.assess(ctx(
            venue_latitude=None, venue_longitude=None,
            previous_latitude=AWAY_40KM[0], previous_longitude=AWAY_40KM[1],
            seconds_since_previous_fix=60.0,
        ))
        assert RiskSignalType.IMPOSSIBLE_TRAVEL in types_of(assessment)
        assert geo_risk(assessment) == 1.0

    def test_clean_submission_with_no_venue_scores_no_geo_component(self, risk):
        """The early return is preserved when nothing contradicts."""
        assessment = risk.assess(ctx(venue_latitude=None, venue_longitude=None))
        assert "geolocation" not in assessment.breakdown


class TestIpMismatch:
    def test_unresolvable_address_raises_nothing(self, risk):
        """The normal case: no GeoIP data, so no signal either way.

        Agreement is NOT recorded -- "we could not tell" must not masquerade
        as "the address matches", which is the same rule network_service
        applies to a private direct peer.
        """
        assessment = risk.assess(ctx())
        assert RiskSignalType.GEO_IP_MISMATCH not in types_of(assessment)

    def test_gross_mismatch_is_detected(self, risk):
        """A fix in Singapore from an address that egresses in Frankfurt."""
        assessment = risk.assess(ctx(
            ip_latitude=FRANKFURT[0], ip_longitude=FRANKFURT[1],
            ip_accuracy_radius_meters=50_000.0,
        ))
        assert RiskSignalType.GEO_IP_MISMATCH in types_of(assessment)
        assert geo_risk(assessment) == IP_MISMATCH_RISK

    def test_same_city_raises_nothing(self, risk):
        """Carrier egress is legitimately kilometres from the user."""
        assessment = risk.assess(ctx(
            ip_latitude=1.3521, ip_longitude=103.8198,
            ip_accuracy_radius_meters=20_000.0,
        ))
        assert RiskSignalType.GEO_IP_MISMATCH not in types_of(assessment)

    def test_accuracy_radius_is_subtracted_before_comparing(self, risk):
        """A record that admits to being vague cannot indict on its vagueness.

        250 km away: a mismatch at face value, not once the resolver's own
        200 km of stated uncertainty is taken off.
        """
        far = dict(ip_latitude=AWAY_250KM[0], ip_longitude=AWAY_250KM[1])
        confident = risk.assess(ctx(**far, ip_accuracy_radius_meters=0.0))
        vague = risk.assess(ctx(**far, ip_accuracy_radius_meters=200_000.0))
        assert RiskSignalType.GEO_IP_MISMATCH in types_of(confident)
        assert RiskSignalType.GEO_IP_MISMATCH not in types_of(vague)

    def test_mismatch_does_not_outrank_a_teleport(self, risk):
        """Both fire: the component takes the worse, and both stay on record."""
        assessment = risk.assess(ctx(
            previous_latitude=AWAY_40KM[0], previous_longitude=AWAY_40KM[1],
            seconds_since_previous_fix=60.0,
            ip_latitude=FRANKFURT[0], ip_longitude=FRANKFURT[1],
            ip_accuracy_radius_meters=50_000.0,
        ))
        assert RiskSignalType.IMPOSSIBLE_TRAVEL in types_of(assessment)
        assert RiskSignalType.GEO_IP_MISMATCH in types_of(assessment)
        assert geo_risk(assessment) == 1.0

    def test_recommendation_collapses_with_the_vpn_advice(self, risk):
        """A VPN is the commonest innocent cause, so the student sees one line."""
        assessment = risk.assess(ctx(
            vpn_detected=True,
            ip_latitude=FRANKFURT[0], ip_longitude=FRANKFURT[1],
            ip_accuracy_radius_meters=50_000.0,
        ))
        assert assessment.recommendations.count("Disable VPN for check-in") == 1


class TestTeleportCannotBeApproved:
    """_decide must act on the signal, because the score cannot.

    Geolocation is 15% of the weighted mean, so a teleport raises the score by
    about 0.15-0.21 -- never across a 0.5 threshold. Left to the average it
    would be filed as a signal on an APPROVED check-in that nobody reads.
    """

    class FakeSession:
        require_liveness_check = True
        require_face_match = False

        def __init__(self, threshold=None):
            self.risk_threshold = threshold

    class FakeCourse:
        risk_threshold = 0.5
        require_face_recognition = False

    @pytest.fixture
    def decide(self):
        service = CheckInService.__new__(CheckInService)
        engine = RiskService()

        def _decide(**overrides):
            context = ctx(**overrides)
            assessment = engine.assess(context)
            verdict = service._decide(
                assessment, context, self.FakeSession(), self.FakeCourse()
            )
            return assessment.risk_score, verdict

        return _decide

    def test_control_without_a_teleport_is_approved(self, decide):
        score, verdict = decide()
        assert verdict == CheckInStatus.APPROVED

    def test_teleport_is_flagged_despite_scoring_under_the_threshold(self, decide):
        score, verdict = decide(
            previous_latitude=AWAY_40KM[0], previous_longitude=AWAY_40KM[1],
            seconds_since_previous_fix=60.0,
        )
        assert score < 0.5, "if this ever exceeds 0.5 the rule below is untested"
        assert verdict == CheckInStatus.FLAGGED

    def test_flagged_not_rejected(self, decide):
        """A stale OS fix can manufacture a jump the student had no part in.

        The cost of a false positive is an attendance mark withheld from
        someone who attended, so a human decides.
        """
        _, verdict = decide(
            previous_latitude=AWAY_40KM[0], previous_longitude=AWAY_40KM[1],
            seconds_since_previous_fix=0.0,
        )
        assert verdict == CheckInStatus.FLAGGED

    def test_an_ip_mismatch_alone_does_not_flag(self, decide):
        """Confounded by VPN use, so it informs the score and a reviewer.

        It is not given the power to withhold an attendance mark on its own.
        """
        _, verdict = decide(
            ip_latitude=FRANKFURT[0], ip_longitude=FRANKFURT[1],
            ip_accuracy_radius_meters=50_000.0,
        )
        assert verdict == CheckInStatus.APPROVED


class TestResolverDegradesToUnknown:
    """ip_geolocation must never raise and never invent a location."""

    @pytest.mark.parametrize("address", [
        None, "", "not-an-ip", "999.999.999.999",
        "172.19.0.1",   # the Docker bridge: every request in this deployment
        "127.0.0.1", "10.0.0.5", "192.168.1.1", "::1", "169.254.1.1",
    ])
    def test_unlocatable_addresses_resolve_to_none(self, address):
        assert ip_geolocation.resolve(address) is None

    def test_public_address_without_a_database_resolves_to_none(self, monkeypatch):
        monkeypatch.delenv("GEOIP_DATABASE_PATH", raising=False)
        ip_geolocation._readers.reset()
        assert ip_geolocation.resolve("8.8.8.8") is None

    def test_a_missing_database_file_does_not_raise(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GEOIP_DATABASE_PATH", str(tmp_path / "absent.mmdb"))
        ip_geolocation._readers.reset()
        assert ip_geolocation.resolve("8.8.8.8") is None
        # Cached as failed, so a second check-in does not retry the open.
        assert ip_geolocation.resolve("8.8.8.8") is None
        ip_geolocation._readers.reset()

    def test_public_addresses_are_locatable_in_principle(self):
        assert ip_geolocation._is_locatable("8.8.8.8")
        assert ip_geolocation._is_locatable("2001:4860:4860::8888")
