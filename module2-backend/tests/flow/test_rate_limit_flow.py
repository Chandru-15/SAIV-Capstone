"""Each rate-limit policy is attached to the right endpoint, with the right key.

The numbers themselves are pinned in tests/unit/test_rate_limit.py. What these
tests check is the wiring, which is where the mistakes live: a budget keyed by
IP where it should be keyed by user, a policy attached to one endpoint and
forgotten on another, or a health probe that can be rate-limited.

The counters are filled directly rather than by making 60 HTTP calls. Sixty
sign-in attempts means sixty bcrypt verifications, which would add seconds to
the suite to re-test an assertion the unit tests already make. Filling the
counter and sending ONE request proves the endpoint consults that policy with
that key, which is the part the unit tests cannot see.
"""

import pytest

from app.services import rate_limit
from app.services.exceptions import RateLimitedError
from tests.conftest import TEST_PROXY_IP

pytestmark = pytest.mark.flow

# conftest injects a peer address and names it as the one trusted proxy, so
# these requests look like browser traffic arriving via the frontend's
# /api/v1 proxy -- which is the real deployment. Every IP-keyed test therefore
# sends X-Forwarded-For explicitly, and client_ip believes it because of where
# the connection came from. That the header is NOT believed from an untrusted
# peer is pinned in tests/unit/test_client_ip.py.
CALLER_IP = "203.0.113.7"
FROM_CALLER = {"X-Forwarded-For": CALLER_IP}


def fill(store, policy, identifier):
    """Spend whatever is LEFT of the budget for one key.

    Not ``range(policy.limit)``: fixtures make real requests before the test
    body runs, so the counter is rarely at zero. The ``student`` fixture, for
    one, calls grant_consent(), which is an authenticated PATCH /auth/me and
    costs an API hit.
    """
    for _ in range(policy.limit + 2):
        try:
            rate_limit.check(policy, identifier, store=store)
        except RateLimitedError:
            return
    raise AssertionError(f"{policy.name} budget never ran out")


class TestLoginIsLimitedPerIp:
    def test_a_login_over_the_budget_is_refused(self, client, isolated_rate_limits):
        fill(isolated_rate_limits, rate_limit.LOGIN, CALLER_IP)
        response = client.post("/auth/login", headers=FROM_CALLER,
                               json={"email": "nobody@example.edu", "password": "whatever"})
        assert response.status_code == 429, response.text
        assert response.json()["code"] == "rate_limited"
        assert response.json()["details"]["policy"] == "login"

    def test_the_budget_is_spent_by_failures_too(self, client, student,
                                                 isolated_rate_limits):
        """A credential-stuffing run is made of failures. Counting only
        successes would leave the budget untouched by the thing it exists to
        slow down."""
        fill(isolated_rate_limits, rate_limit.LOGIN, CALLER_IP)
        # Correct credentials, but the IP has no budget left.
        response = client.post("/auth/login", headers=FROM_CALLER,
                               json={"email": student.email, "password": "password123"})
        assert response.status_code == 429, response.text

    def test_login_does_not_spend_the_registration_budget(self, client,
                                                          isolated_rate_limits):
        fill(isolated_rate_limits, rate_limit.LOGIN, CALLER_IP)
        response = client.post(
            "/auth/register", headers=FROM_CALLER,
            json={"email": "fresh@example.edu", "full_name": "Fresh Student",
                  "password": "password123", "role": "student"},
        )
        assert response.status_code == 201, response.text


class TestTheHeaderCannotBuyAFreshBudget:
    """The bypass, at the endpoint it mattered on.

    client_ip used to return the LEFTMOST X-Forwarded-For entry, which is
    whatever the caller wrote. A credential-stuffing run could therefore send
    a different claimed address on every request, land in a fresh bucket each
    time and never meet the 60-per-hour budget at all.

    These tests remove this suite's trusted-proxy setting, so the injected
    peer is an ordinary untrusted caller -- which is what the compose default
    looks like, with port 8000 published and nothing in front of it.
    """

    @pytest.fixture
    def untrusted(self, monkeypatch):
        """Overrides conftest's autouse trusted_proxy, running after it."""
        monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)

    def test_a_forged_header_does_not_escape_a_spent_budget(
            self, client, isolated_rate_limits, untrusted):
        fill(isolated_rate_limits, rate_limit.LOGIN, TEST_PROXY_IP)
        response = client.post(
            "/auth/login", headers={"X-Forwarded-For": "198.51.100.200"},
            json={"email": "nobody@example.edu", "password": "whatever"})
        assert response.status_code == 429, response.text
        assert response.json()["details"]["policy"] == "login"

    def test_a_new_claimed_address_every_time_still_runs_out(
            self, client, isolated_rate_limits, untrusted):
        """The attack as it would actually be run.

        Twenty attempts, twenty different claimed addresses, one real
        connection. At least one must be refused, or the budget is decorative.
        """
        fill(isolated_rate_limits, rate_limit.LOGIN, TEST_PROXY_IP)
        codes = {
            client.post(
                "/auth/login", headers={"X-Forwarded-For": f"192.0.2.{n}"},
                json={"email": "nobody@example.edu", "password": "whatever"},
            ).status_code
            for n in range(1, 21)
        }
        assert codes == {429}

    def test_registration_is_covered_by_the_same_rule(
            self, client, isolated_rate_limits, untrusted):
        fill(isolated_rate_limits, rate_limit.REGISTRATION, TEST_PROXY_IP)
        response = client.post(
            "/auth/register", headers={"X-Forwarded-For": "198.51.100.201"},
            json={"email": "forged@example.edu", "full_name": "Forged",
                  "password": "password123", "role": "student"},
        )
        assert response.status_code == 429, response.text

    def test_the_account_was_not_created(self, client, db, isolated_rate_limits,
                                         untrusted):
        """A refused registration must leave nothing behind."""
        from app.models import User

        fill(isolated_rate_limits, rate_limit.REGISTRATION, TEST_PROXY_IP)
        client.post(
            "/auth/register", headers={"X-Forwarded-For": "198.51.100.202"},
            json={"email": "forged2@example.edu", "full_name": "Forged Two",
                  "password": "password123", "role": "student"},
        )
        assert db.query(User).filter(User.email == "forged2@example.edu").count() == 0


class TestRegistrationIsLimitedPerIp:
    def test_a_registration_over_the_budget_is_refused(self, client,
                                                       isolated_rate_limits):
        fill(isolated_rate_limits, rate_limit.REGISTRATION, CALLER_IP)
        response = client.post(
            "/auth/register", headers=FROM_CALLER,
            json={"email": "late@example.edu", "full_name": "Late Student",
                  "password": "password123", "role": "student"},
        )
        assert response.status_code == 429, response.text
        assert response.json()["details"]["policy"] == "registration"

    def test_the_account_is_not_created(self, client, db, isolated_rate_limits):
        """The limit is a dependency, so it runs before the service: a refused
        registration must leave nothing behind."""
        from app.models import User

        fill(isolated_rate_limits, rate_limit.REGISTRATION, CALLER_IP)
        client.post(
            "/auth/register", headers=FROM_CALLER,
            json={"email": "never@example.edu", "full_name": "Never Created",
                  "password": "password123", "role": "student"},
        )
        assert db.query(User).filter(User.email == "never@example.edu").count() == 0


class TestApiRequestsAreLimitedPerUser:
    def test_an_authenticated_request_over_the_budget_is_refused(
            self, client, student, isolated_rate_limits):
        fill(isolated_rate_limits, rate_limit.API, student.id)
        response = client.get("/auth/me", headers=student.headers)
        assert response.status_code == 429, response.text
        assert response.json()["details"]["policy"] == "api"
        assert response.json()["details"]["limit"] == 1000

    def test_the_budget_is_keyed_by_user_not_by_address(
            self, client, student, make_actor, isolated_rate_limits):
        """Both students share one IP here. If the blanket budget were keyed by
        address, one heavy user would throttle the whole cohort."""
        fill(isolated_rate_limits, rate_limit.API, student.id)
        other = make_actor("student")
        assert client.get("/auth/me", headers=other.headers).status_code == 200

    def test_an_anonymous_request_still_reaches_its_own_401(
            self, client, isolated_rate_limits):
        """There is no user to key on, so the blanket budget must not turn a
        missing token into a 429 and hide the real problem."""
        assert client.get("/auth/me").status_code == 401

    def test_an_expired_or_bad_token_still_reaches_its_own_401(
            self, client, isolated_rate_limits):
        response = client.get("/auth/me",
                              headers={"Authorization": "Bearer not-a-real-token"})
        assert response.status_code == 401, response.text


class TestProbesAreNeverLimited:
    def test_health_is_outside_the_limited_prefix(self, client, student,
                                                  isolated_rate_limits):
        """A rate-limited health check looks exactly like an outage, and
        docker-compose plus module 4 both poll it continuously."""
        fill(isolated_rate_limits, rate_limit.API, student.id)
        # base_url is /api/v1, so step out of it to reach the root probe.
        assert client.get("/health", headers=student.headers).status_code == 404
        assert client.get("http://testserver/health",
                          headers=student.headers).status_code == 200

    def test_metrics_is_outside_the_limited_prefix(self, client, student,
                                                   isolated_rate_limits):
        fill(isolated_rate_limits, rate_limit.API, student.id)
        assert client.get("http://testserver/metrics",
                          headers=student.headers).status_code == 200


class TestAnUnidentifiableCallerIsNotLimited:
    """Deliberate, and worth stating because it is a real gap.

    ``rate_limit.check`` skips an empty identifier rather than putting every
    such caller in one shared bucket, because a shared bucket lets a single
    anonymous request exhaust the budget for everyone behind it. The cost is
    that a caller with no resolvable address is not limited at all -- which is
    exactly the case under TestClient, where request.client is None.

    This is acceptable only because the IP-keyed budgets are throttles rather
    than a security boundary (see the rate_limit module docstring). If they
    ever became load-bearing, this would need a trusted-proxy allowlist and a
    real peer address instead.
    """

    def test_no_address_means_no_ip_budget(self, client, isolated_rate_limits):
        """Repeated addressless attempts are never throttled.

        There is no key to fill, which IS the point -- the counter semantics
        themselves are pinned in tests/unit/test_rate_limit.py. A handful of
        attempts is enough here: each one costs a bcrypt verification.
        """
        for _ in range(3):
            response = client.post(
                "/auth/login",
                json={"email": "nobody@example.edu", "password": "whatever"})
            assert response.status_code == 401, response.text

    def test_a_different_address_has_its_own_budget(self, client, isolated_rate_limits):
        fill(isolated_rate_limits, rate_limit.LOGIN, CALLER_IP)
        response = client.post("/auth/login",
                               headers={"X-Forwarded-For": "198.51.100.4"},
                               json={"email": "nobody@example.edu", "password": "whatever"})
        assert response.status_code == 401, response.text
