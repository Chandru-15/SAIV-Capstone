"""Server-side VPN / proxy detection.

Implements API-SPECIFICATION.md "Step 6", with one deliberate departure that
these tests pin: a private DIRECT PEER is not evidence of a client-side VPN.
"""

import pytest

from app.services.network_service import (
    PRIVATE_IP_CONFIDENCE,
    USER_AGENT_CONFIDENCE,
    detect_vpn_proxy,
    network_signal_known,
)

pytestmark = pytest.mark.unit


class TestSpecBehaviour:
    """The spec's rules, applied to a genuine client address."""

    @pytest.mark.parametrize("ip", ["10.8.0.6", "192.168.1.40", "172.16.0.9", "172.19.0.5"])
    def test_private_forwarded_address_is_a_vpn_indicator(self, ip):
        is_vpn, confidence = detect_vpn_proxy(ip, forwarded=True)
        assert is_vpn
        assert confidence == PRIVATE_IP_CONFIDENCE == 0.7

    @pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1"])
    def test_public_address_carries_no_signal(self, ip):
        assert detect_vpn_proxy(ip, forwarded=True) == (False, 0.0)

    @pytest.mark.parametrize("keyword", ["vpn", "proxy", "tunnel", "tor"])
    def test_user_agent_keywords_flag_at_higher_confidence(self, keyword):
        is_vpn, confidence = detect_vpn_proxy("8.8.8.8", f"Mozilla/5.0 {keyword}-client/1",
                                              forwarded=True)
        assert is_vpn
        assert confidence == USER_AGENT_CONFIDENCE == 0.8

    def test_user_agent_wins_over_private_ip(self):
        _, confidence = detect_vpn_proxy("10.8.0.6", "x-tunnel/1", forwarded=True)
        assert confidence == USER_AGENT_CONFIDENCE

    def test_ordinary_user_agent_is_clean(self):
        assert detect_vpn_proxy("8.8.8.8", "Mozilla/5.0 (Macintosh) Chrome/120",
                                forwarded=True) == (False, 0.0)


class TestDeploymentDeparture:
    """The reason this is not a literal transcription of the spec.

    Every request in the docker-compose deployment arrives from the bridge
    gateway (measured: 172.19.0.1 across all recorded audit entries). The
    spec's rule flags any private address, which would add 0.7 x 0.15 = 0.105
    risk to EVERY check-in and tip borderline ones over the threshold.
    """

    def test_private_direct_peer_is_not_flagged(self):
        assert detect_vpn_proxy("172.19.0.1", forwarded=False) == (False, 0.0)

    def test_private_direct_peer_reports_signal_unknown(self):
        """Unknown, so RiskService omits the component instead of scoring 0.0 —
        "we could not tell" must not read as "no VPN"."""
        assert network_signal_known("172.19.0.1", forwarded=False) is False

    def test_the_same_address_forwarded_IS_flagged(self):
        """Once a real proxy sets X-Forwarded-For, a private address there is a
        genuine client-side tunnel and the spec's rule applies as written."""
        assert detect_vpn_proxy("172.19.0.1", forwarded=True)[0] is True
        assert network_signal_known("172.19.0.1", forwarded=True) is True

    def test_public_direct_peer_is_a_usable_signal(self):
        assert network_signal_known("8.8.8.8", forwarded=False) is True


class TestRobustness:
    @pytest.mark.parametrize("ip", [None, "", "not-an-ip", "999.999.999.999"])
    def test_unparseable_addresses_never_raise(self, ip):
        assert detect_vpn_proxy(ip, forwarded=True) == (False, 0.0)

    def test_missing_address_means_no_signal(self):
        assert network_signal_known(None, forwarded=True) is False
        assert network_signal_known("", forwarded=False) is False

    def test_ipv6_loopback_is_private(self):
        assert detect_vpn_proxy("::1", forwarded=True)[0] is True

    def test_confidence_is_always_a_probability(self):
        for ip in ("10.0.0.1", "8.8.8.8", None, "garbage"):
            for ua in (None, "vpn", "Chrome"):
                _, c = detect_vpn_proxy(ip, ua, forwarded=True)
                assert 0.0 <= c <= 1.0


class TestRiskIntegration:
    def test_unknown_network_omits_the_component(self):
        from app.services.risk_service import CheckInContext, RiskService

        base = dict(latitude=1.2966, longitude=103.7764, location_accuracy_meters=5.0,
                    venue_latitude=1.2966, venue_longitude=103.7764,
                    geofence_radius_meters=100.0, require_liveness=True,
                    liveness_passed=True, liveness_score=0.95, device_known=True)
        known = RiskService().assess(CheckInContext(**base, network_signal_known=True))
        unknown = RiskService().assess(CheckInContext(**base, network_signal_known=False))
        assert "network" in known.breakdown
        assert "network" not in unknown.breakdown

    def test_a_private_peer_adds_no_risk_to_an_otherwise_clean_checkin(self):
        """The whole point of the departure: containerised traffic must not be
        penalised for being containerised."""
        from app.services.checkin_service import CheckInService

        flags = CheckInService._network_flags("172.19.0.1", "Chrome/120", False)
        assert flags == {"network_signal_known": False}
