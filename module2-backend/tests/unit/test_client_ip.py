"""Which address the backend believes, and when.

``X-Forwarded-For`` is written by whoever sends the request. A proxy APPENDS
to it, so everything left of what our own infrastructure added is a value the
caller chose. Reading the leftmost entry -- which this code used to do -- hands
a rate-limit key to the person being rate-limited: vary the header, get a
fresh bucket, and the per-IP budgets on /auth/login and /auth/register never
fire.

These tests pin the rule that replaced it: the header counts only when the
CONNECTION came from a proxy named in TRUSTED_PROXY_IPS.
"""

import pytest

from app.dependencies import client_ip, client_ip_is_forwarded

pytestmark = pytest.mark.unit

PROXY = "10.250.0.9"
OUTER_PROXY = "10.250.0.10"
REAL_CALLER = "203.0.113.7"
FORGED = "198.51.100.200"


class FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class FakeRequest:
    """Only the two attributes client_ip reads."""

    def __init__(self, peer, forwarded=None) -> None:
        self.client = FakeClient(peer) if peer else None
        self.headers = {"x-forwarded-for": forwarded} if forwarded else {}


@pytest.fixture
def trust(monkeypatch):
    def _trust(value: str):
        monkeypatch.setenv("TRUSTED_PROXY_IPS", value)
    return _trust


@pytest.fixture(autouse=True)
def no_trust_by_default(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)


class TestTheHeaderIsIgnoredFromAnUntrustedPeer:
    """The bypass this module exists to close."""

    def test_a_forged_header_cannot_choose_the_rate_limit_key(self):
        """Nothing is trusted, so the peer is the answer however loudly the
        caller claims otherwise."""
        request = FakeRequest(peer=REAL_CALLER, forwarded=FORGED)
        assert client_ip(request) == REAL_CALLER

    def test_rotating_the_header_does_not_rotate_the_bucket(self):
        """The actual attack: a different claimed address on every request.

        Each one must still land on the same key, or the budget is decorative.
        """
        keys = {
            client_ip(FakeRequest(peer=REAL_CALLER, forwarded=f"192.0.2.{n}"))
            for n in range(1, 50)
        }
        assert keys == {REAL_CALLER}

    def test_a_peer_outside_the_trusted_set_is_still_not_believed(self, trust):
        """Trust is configured, but this connection is not from that proxy."""
        trust(PROXY)
        request = FakeRequest(peer="192.0.2.55", forwarded=FORGED)
        assert client_ip(request) == "192.0.2.55"

    def test_nothing_is_trusted_by_default(self):
        """Unset TRUSTED_PROXY_IPS must mean 'trust no proxy'.

        A default that trusted one hop would reinstate the bypass wherever the
        backend port is reachable directly, which is the compose default.
        """
        request = FakeRequest(peer=REAL_CALLER, forwarded=FORGED)
        assert client_ip(request) == REAL_CALLER
        assert client_ip_is_forwarded(request) is False


class TestTheHeaderCountsFromATrustedProxy:
    def test_the_caller_behind_one_proxy(self, trust):
        """The proxy appended the caller; the forged prefix is dropped."""
        trust(PROXY)
        request = FakeRequest(peer=PROXY, forwarded=f"{FORGED}, {REAL_CALLER}")
        assert client_ip(request) == REAL_CALLER
        assert client_ip_is_forwarded(request) is True

    def test_a_chain_of_two_proxies(self, trust):
        """Walk right to left past our own hops, stop at the first we did not add.

        No proxy COUNT is configured anywhere: naming the proxies is enough,
        and a chain that grows a hop does not need a second setting changed.
        """
        trust(f"{PROXY},{OUTER_PROXY}")
        request = FakeRequest(
            peer=PROXY, forwarded=f"{FORGED}, {REAL_CALLER}, {OUTER_PROXY}")
        assert client_ip(request) == REAL_CALLER

    def test_a_cidr_range_is_accepted(self, trust):
        trust("10.250.0.0/24")
        request = FakeRequest(peer=PROXY, forwarded=REAL_CALLER)
        assert client_ip(request) == REAL_CALLER

    def test_claiming_to_be_the_proxy_does_not_help(self, trust):
        """A caller who writes the proxy's own address into the header.

        The proxy still appends the real address to the right of it, and the
        walk reaches that first.
        """
        trust(PROXY)
        request = FakeRequest(peer=PROXY, forwarded=f"{PROXY}, {REAL_CALLER}")
        assert client_ip(request) == REAL_CALLER

    def test_ipv6_callers_survive_the_round_trip(self, trust):
        trust(PROXY)
        request = FakeRequest(peer=PROXY, forwarded="2001:db8::1234")
        assert client_ip(request) == "2001:db8::1234"


class TestDegenerateInput:
    def test_no_peer_and_no_header(self):
        assert client_ip(FakeRequest(peer=None)) is None

    def test_trusted_peer_with_no_header_falls_back_to_the_peer(self, trust):
        trust(PROXY)
        request = FakeRequest(peer=PROXY)
        assert client_ip(request) == PROXY
        assert client_ip_is_forwarded(request) is False

    def test_a_chain_of_only_proxies_names_no_client(self, trust):
        """Nothing in the header is a caller, so do not invent one."""
        trust(f"{PROXY},{OUTER_PROXY}")
        request = FakeRequest(peer=PROXY, forwarded=OUTER_PROXY)
        assert client_ip(request) == PROXY
        assert client_ip_is_forwarded(request) is False

    @pytest.mark.parametrize("header", [
        "", "   ", ",,,", "not-an-ip", "999.999.999.999",
    ])
    def test_unusable_headers_fall_back_to_the_peer(self, trust, header):
        trust(PROXY)
        assert client_ip(FakeRequest(peer=PROXY, forwarded=header)) == PROXY

    def test_garbage_to_the_right_does_not_hide_the_caller(self, trust):
        """Keep walking left past entries that are not addresses."""
        trust(PROXY)
        request = FakeRequest(peer=PROXY, forwarded=f"{REAL_CALLER}, junk")
        assert client_ip(request) == REAL_CALLER

    def test_an_unparseable_trusted_entry_is_dropped_not_widened(self, trust):
        """A typo in configuration must narrow trust, never broaden it."""
        trust(f"nonsense,{PROXY}")
        assert client_ip(FakeRequest(peer=PROXY, forwarded=REAL_CALLER)) == REAL_CALLER
        assert client_ip(FakeRequest(peer="192.0.2.55", forwarded=FORGED)) == "192.0.2.55"

    def test_an_entirely_unparseable_setting_trusts_nothing(self, trust):
        trust("nonsense,,garbage")
        request = FakeRequest(peer=PROXY, forwarded=FORGED)
        assert client_ip(request) == PROXY
