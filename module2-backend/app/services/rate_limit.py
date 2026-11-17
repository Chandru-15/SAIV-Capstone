"""Request-rate policies from docs/API-SPECIFICATION.md ("Rate Limiting").

    Login attempts        60    per hour   per IP
    Registration          10    per hour   per IP
    API requests        1000    per hour   per user
    Check-in attempts     10    per minute per user

Fixed windows with a TTL, which is what the specification asks for: a counter
is created with an expiry and incremented, and the window ends when the key
expires. That is one round trip per request and needs no scheduled cleanup --
Redis forgets the counter on its own.

A fixed window is coarser than a sliding one: a caller can spend the whole
budget in the last second of one window and again in the first second of the
next, so the true worst case over a short span is twice the limit. That is
accepted here because the limits are about stopping grinding and scripted
abuse, not about smoothing traffic, and because a sliding window costs a
sorted set per caller.

Keyed by IP for the unauthenticated limits and by user id for the
authenticated ones, exactly as the table says.

Two honest caveats about the IP-keyed half, because they decide how much this
is worth. The address comes from ``dependencies.client_ip``, which prefers
``X-Forwarded-For`` so that a caller behind the frontend's /api/v1 proxy is
counted individually rather than as the proxy -- but that header is set by the
client, so a determined caller can rotate it and get a fresh budget each time.
And if a hop does NOT forward the header, every browser behind it shares one
bucket, which would exhaust a cohort's login budget between them.

So these limits throttle casual grinding and runaway clients; they are not a
security boundary, and nothing irreversible depends on them. The controls that
do carry weight are bcrypt's cost factor on the password path, the audit trail
(``login_failed`` is recorded independently of any rollback), and the risk
engine. Every budget is env-overridable for the same reason: a shared-bucket
surprise must be fixable in one restart rather than a redeploy.

Storage is Redis when reachable, falling back to an in-process dict, the same
arrangement as liveness_challenge. The fallback is per-process, so with
several uvicorn workers each worker enforces its own share of the budget;
production should run Redis, hence the warning when the fallback is chosen.

No FastAPI here: controllers supply the identifier and this module only
counts, so the service layer stays free of HTTP, as app/main.py describes.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Protocol, Tuple

from .exceptions import RateLimitedError

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6380")

_KEY_PREFIX = "saiv:ratelimit:"

# Set RATE_LIMIT_ENABLED=false to switch the limits off. Intended for load
# testing against a local stack, never for a deployment.
ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "true").strip().lower() not in {
    "false", "0", "no", "off",
}


@dataclass(frozen=True)
class Policy:
    """One row of the specification's rate-limit table."""

    name: str
    limit: int
    window_seconds: int
    #: What a breach should say. Kept with the policy so the message cannot
    #: drift from the number it describes.
    subject: str

    def message(self, retry_after: int) -> str:
        unit = "minute" if self.window_seconds == 60 else "hour"
        return (f"Too many {self.subject}. The limit is {self.limit} per {unit}. "
                f"Try again in {retry_after}s.")


def _limit(env_name: str, default: int) -> int:
    """Specification default, overridable without a redeploy.

    The IP-keyed budgets are the ones most likely to need it: see the note in
    the module docstring about a whole cohort arriving from one proxy address.
    """
    try:
        value = int(os.environ.get(env_name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


LOGIN = Policy("login", _limit("RATE_LIMIT_LOGIN_PER_HOUR", 60), 3600,
               "sign-in attempts")
REGISTRATION = Policy("registration", _limit("RATE_LIMIT_REGISTER_PER_HOUR", 10), 3600,
                      "registration attempts")
API = Policy("api", _limit("RATE_LIMIT_API_PER_HOUR", 1000), 3600, "requests")
CHECKIN = Policy("checkin", _limit("RATE_LIMIT_CHECKIN_PER_MINUTE", 10), 60,
                 "check-in attempts")
BROWSER_CHECKIN_CHALLENGE = Policy("browser_checkin_challenge", 15, 60, "browser challenges")
BROWSER_ENROLLMENT = Policy("browser_enrollment", 15, 60, "browser enrollment requests")


class CounterStore(Protocol):
    def hit(self, key: str, window_seconds: int) -> Tuple[int, int]:
        """Count one request against ``key``.

        Returns ``(count_in_window, seconds_until_the_window_resets)``.
        """


class InMemoryCounterStore:
    """Fallback store. Correct for tests and a single worker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: Dict[str, Tuple[int, float]] = {}

    def hit(self, key: str, window_seconds: int) -> Tuple[int, int]:
        # Monotonic, so a clock adjustment cannot extend or clear a window.
        now = time.monotonic()
        with self._lock:
            count, expires_at = self._windows.get(key, (0, 0.0))
            if expires_at <= now:
                count, expires_at = 0, now + window_seconds
            count += 1
            self._windows[key] = (count, expires_at)
            return count, max(1, int(expires_at - now))

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()


class RedisCounterStore:
    def __init__(self, client) -> None:
        self._redis = client

    def hit(self, key: str, window_seconds: int) -> Tuple[int, int]:
        pipe = self._redis.pipeline()
        # SET ... NX EX establishes the counter AND its expiry together, so a
        # crash between the two can never leave a key with no TTL -- which
        # would lock the caller out permanently rather than for one window.
        pipe.set(key, 0, ex=window_seconds, nx=True)
        pipe.incr(key)
        pipe.ttl(key)
        _created, count, ttl = pipe.execute()
        # -1 means no expiry, -2 means the key vanished between INCR and TTL.
        if ttl is None or ttl < 0:
            self._redis.expire(key, window_seconds)
            ttl = window_seconds
        return int(count), max(1, int(ttl))


def build_store() -> CounterStore:
    """Redis if reachable, in-memory otherwise."""
    try:
        import redis

        client = redis.Redis.from_url(REDIS_URL, decode_responses=True,
                                      socket_connect_timeout=1, socket_timeout=1)
        client.ping()
        return RedisCounterStore(client)
    except Exception as exc:
        logger.warning(
            "Redis unavailable at %s (%s); counting rate limits in-process. "
            "Fine for one worker, but each worker then enforces its own copy "
            "of every budget.", REDIS_URL, type(exc).__name__)
        return InMemoryCounterStore()


# Built on first use, never at import: importing app.main must not require a
# reachable Redis.
_STORE: Optional[CounterStore] = None


def _shared_store() -> CounterStore:
    global _STORE
    if _STORE is None:
        _STORE = build_store()
    return _STORE


def check(policy: Policy, identifier: str, *, store: Optional[CounterStore] = None) -> None:
    """Count one request and raise :class:`RateLimitedError` if over budget.

    ``identifier`` is an IP or a user id. An empty one is not counted: that
    means the caller could not be identified at all, and a shared ""-bucket
    would let one anonymous request exhaust everybody's budget at once.

    A storage failure lets the request through. The alternative -- refusing
    every request when Redis blips -- turns a cache outage into a total
    outage, and these limits protect against grinding rather than guarding
    anything whose breach is irreversible.
    """
    if not ENABLED or not identifier:
        return
    key = f"{_KEY_PREFIX}{policy.name}:{identifier}"
    try:
        count, retry_after = (store or _shared_store()).hit(key, policy.window_seconds)
    except Exception as exc:
        logger.warning("Rate-limit store unavailable (%s); allowing %s request",
                       type(exc).__name__, policy.name)
        return
    if count > policy.limit:
        raise RateLimitedError(
            policy.message(retry_after),
            retry_after=retry_after,
            details={"limit": policy.limit, "window_seconds": policy.window_seconds,
                     "policy": policy.name},
        )
