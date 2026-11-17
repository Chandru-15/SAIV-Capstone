"""The rate-limit counters, as specified in docs/API-SPECIFICATION.md.

    Login attempts        60    per hour   per IP
    Registration          10    per hour   per IP
    API requests        1000    per hour   per user
    Check-in attempts     10    per minute per user

These are pure counter tests: no database, no HTTP, no Redis. The wiring that
attaches each policy to an endpoint is covered in tests/flow.
"""

import pytest

from app.services import rate_limit
from app.services.exceptions import RateLimitedError

pytestmark = pytest.mark.unit


@pytest.fixture
def store():
    return rate_limit.InMemoryCounterStore()


def test_the_published_numbers_are_what_is_enforced():
    """Pinned against the specification table, so a casual edit to a limit
    fails here rather than silently changing the contract."""
    assert (rate_limit.LOGIN.limit, rate_limit.LOGIN.window_seconds) == (60, 3600)
    assert (rate_limit.REGISTRATION.limit,
            rate_limit.REGISTRATION.window_seconds) == (10, 3600)
    assert (rate_limit.API.limit, rate_limit.API.window_seconds) == (1000, 3600)
    assert (rate_limit.CHECKIN.limit, rate_limit.CHECKIN.window_seconds) == (10, 60)


def test_requests_up_to_the_limit_pass_and_the_next_one_does_not(store):
    for _ in range(rate_limit.CHECKIN.limit):
        rate_limit.check(rate_limit.CHECKIN, "user-1", store=store)
    with pytest.raises(RateLimitedError):
        rate_limit.check(rate_limit.CHECKIN, "user-1", store=store)


def test_identifiers_have_separate_budgets(store):
    """Otherwise one noisy caller would lock out everybody else."""
    for _ in range(rate_limit.CHECKIN.limit):
        rate_limit.check(rate_limit.CHECKIN, "user-1", store=store)
    rate_limit.check(rate_limit.CHECKIN, "user-2", store=store)


def test_policies_have_separate_budgets(store):
    """Spending the check-in budget must not spend the login budget."""
    for _ in range(rate_limit.CHECKIN.limit):
        rate_limit.check(rate_limit.CHECKIN, "shared-id", store=store)
    rate_limit.check(rate_limit.LOGIN, "shared-id", store=store)


def test_a_breach_carries_retry_after_and_the_policy(store):
    for _ in range(rate_limit.CHECKIN.limit):
        rate_limit.check(rate_limit.CHECKIN, "user-1", store=store)
    with pytest.raises(RateLimitedError) as excinfo:
        rate_limit.check(rate_limit.CHECKIN, "user-1", store=store)

    error = excinfo.value
    assert error.status_code == 429
    assert error.code == "rate_limited"
    # Whole seconds, at least 1, and never longer than the window itself.
    assert 1 <= error.retry_after <= rate_limit.CHECKIN.window_seconds
    assert error.details["limit"] == 10
    assert error.details["window_seconds"] == 60
    assert error.details["policy"] == "checkin"


def test_the_message_names_the_limit_and_the_unit(store):
    for _ in range(rate_limit.CHECKIN.limit):
        rate_limit.check(rate_limit.CHECKIN, "user-1", store=store)
    with pytest.raises(RateLimitedError) as excinfo:
        rate_limit.check(rate_limit.CHECKIN, "user-1", store=store)
    assert "10 per minute" in excinfo.value.message

    for _ in range(rate_limit.REGISTRATION.limit):
        rate_limit.check(rate_limit.REGISTRATION, "1.2.3.4", store=store)
    with pytest.raises(RateLimitedError) as excinfo:
        rate_limit.check(rate_limit.REGISTRATION, "1.2.3.4", store=store)
    assert "10 per hour" in excinfo.value.message


def test_an_empty_identifier_is_not_counted(store):
    """An unidentifiable caller must not share a bucket with every other
    unidentifiable caller: one of them could exhaust it for all of them."""
    for _ in range(rate_limit.CHECKIN.limit * 3):
        rate_limit.check(rate_limit.CHECKIN, "", store=store)


def test_the_window_resets_once_it_expires(store, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock["t"])

    for _ in range(rate_limit.CHECKIN.limit):
        rate_limit.check(rate_limit.CHECKIN, "user-1", store=store)
    with pytest.raises(RateLimitedError):
        rate_limit.check(rate_limit.CHECKIN, "user-1", store=store)

    # One second past the 60s window.
    clock["t"] += rate_limit.CHECKIN.window_seconds + 1
    rate_limit.check(rate_limit.CHECKIN, "user-1", store=store)


def test_the_window_does_not_reset_early(store, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock["t"])

    for _ in range(rate_limit.CHECKIN.limit):
        rate_limit.check(rate_limit.CHECKIN, "user-1", store=store)
    clock["t"] += rate_limit.CHECKIN.window_seconds - 1
    with pytest.raises(RateLimitedError):
        rate_limit.check(rate_limit.CHECKIN, "user-1", store=store)


def test_a_broken_store_lets_the_request_through(monkeypatch):
    """Fail open, deliberately. Refusing everything when the counter store
    blips would turn a cache outage into a total outage, and these limits
    guard nothing whose breach is irreversible."""

    class Broken:
        def hit(self, key, window_seconds):
            raise RuntimeError("redis is down")

    rate_limit.check(rate_limit.CHECKIN, "user-1", store=Broken())


def test_the_limits_can_be_switched_off(store, monkeypatch):
    """RATE_LIMIT_ENABLED=false, for load testing a local stack."""
    monkeypatch.setattr(rate_limit, "ENABLED", False)
    for _ in range(rate_limit.CHECKIN.limit * 5):
        rate_limit.check(rate_limit.CHECKIN, "user-1", store=store)


def test_the_counter_is_created_with_an_expiry_in_one_round_trip():
    """The Redis path must SET ... NX EX before INCR.

    If the expiry were a separate call, a crash between INCR and EXPIRE would
    leave a key with no TTL — locking that caller out permanently instead of
    for one window.
    """
    calls = []

    class FakePipeline:
        def set(self, key, value, ex=None, nx=None):
            calls.append(("set", key, value, ex, nx))

        def incr(self, key):
            calls.append(("incr", key))

        def ttl(self, key):
            calls.append(("ttl", key))

        def execute(self):
            return [True, 1, 60]

    class FakeRedis:
        def pipeline(self):
            return FakePipeline()

    count, retry_after = rate_limit.RedisCounterStore(FakeRedis()).hit("k", 60)
    assert (count, retry_after) == (1, 60)
    assert [call[0] for call in calls] == ["set", "incr", "ttl"]
    assert calls[0][3:] == (60, True)  # ex=60, nx=True


def test_a_missing_ttl_is_repaired_rather_than_reported_as_forever():
    """TTL returns -1 for a key with no expiry and -2 if it vanished. Neither
    is a sane Retry-After, so the store re-applies the window."""

    class FakePipeline:
        def set(self, *a, **k): pass
        def incr(self, key): pass
        def ttl(self, key): pass
        def execute(self):
            return [None, 5, -1]

    expired = []

    class FakeRedis:
        def pipeline(self):
            return FakePipeline()

        def expire(self, key, seconds):
            expired.append((key, seconds))

    count, retry_after = rate_limit.RedisCounterStore(FakeRedis()).hit("k", 3600)
    assert (count, retry_after) == (5, 3600)
    assert expired == [("k", 3600)]
