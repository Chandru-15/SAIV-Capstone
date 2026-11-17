"""Shared FastAPI dependencies: authentication and role guards.

The Spring parallel is a ``HandlerInterceptor`` plus ``@PreAuthorize``. Guards
resolve the caller once per request and hand controllers a ``User``, so no
controller parses a token or compares a role string itself.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from functools import lru_cache
from typing import Callable, Iterator, Optional, Tuple

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import User, UserRole
from .services import AuthService
from .services import rate_limit
from .services.exceptions import AuthenticationError

# auto_error=False so a missing header raises our own AuthenticationError,
# producing the same error envelope as every other failure.
bearer_scheme = HTTPBearer(auto_error=False)

logger = logging.getLogger(__name__)


def get_auth_service(db: Session = Depends(get_db)) -> AuthService:
    return AuthService(db)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    auth: AuthService = Depends(get_auth_service),
) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Missing bearer token")
    return auth.current_user(credentials.credentials)


def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    auth: AuthService = Depends(get_auth_service),
) -> User | None:
    """The caller if they presented a valid token, otherwise None.

    For endpoints that are readable anonymously but reveal MORE to an
    authenticated caller. A bad token returns None rather than 401: the
    endpoint works either way, so a stale token should degrade to the public
    view instead of breaking the page.
    """
    if credentials is None or not credentials.credentials:
        return None
    try:
        return auth.current_user(credentials.credentials)
    except Exception:
        return None


def require_roles(*allowed: UserRole) -> Callable[..., User]:
    """Build a dependency that admits only the given roles.

        @router.get("/", dependencies=[Depends(require_roles(UserRole.ADMIN))])

    Or bind it to get the user as well::

        actor: User = Depends(require_roles(UserRole.ADMIN, UserRole.INSTRUCTOR))
    """

    def guard(user: User = Depends(get_current_user)) -> User:
        from .services.user_service import UserService

        UserService.require_role(user, *allowed)
        return user

    return guard


def rate_limit_by_ip(policy: "rate_limit.Policy") -> Callable[..., None]:
    """Guard an endpoint with one of the IP-keyed budgets.

        @router.post("/login", dependencies=[Depends(rate_limit_by_ip(LOGIN))])

    A dependency rather than a service call, so the counter is incremented
    before the request body is validated and before any password is hashed --
    the expensive part is what a flood is trying to buy.
    """

    def guard(request: Request) -> None:
        rate_limit.check(policy, client_ip(request) or "")

    return guard


def rate_limit_by_user(policy: "rate_limit.Policy") -> Callable[..., None]:
    """Guard an endpoint with one of the user-keyed budgets.

    Depends on ``get_current_user``, which FastAPI caches per request, so an
    endpoint that also takes ``current_user`` resolves it once rather than
    twice.
    """

    def guard(user: User = Depends(get_current_user)) -> None:
        rate_limit.check(policy, user.id)

    return guard


def rate_limit_api(
    user: User | None = Depends(get_current_user_optional),
) -> None:
    """The blanket 1000-requests-per-hour-per-user budget.

    Attached to the whole /api/v1 router in main.py. Anonymous callers are not
    counted here -- there is no user to key on, and their entry points
    (/auth/login, /auth/register) carry their own per-IP budgets.

    Uses the optional variant on purpose: a bad or expired token must produce
    that endpoint's own 401, not a rate-limit error.
    """
    if user is not None:
        rate_limit.check(rate_limit.API, user.id)


# ---------------------------------------------------------------------------
# Client address
# ---------------------------------------------------------------------------
# ``X-Forwarded-For`` is written by whoever sends the request. A proxy APPENDS
# the address it received from, so the header reads
#
#     X-Forwarded-For: <whatever the client wrote>, <client>, <proxy1>, ...
#                       \_____ forgeable ______/   \___ added by us ___/
#
# Taking the leftmost entry -- which this function used to do -- therefore
# returns a value the caller chose. That is harmless for a log line and NOT
# harmless for ``rate_limit_by_ip``, which uses this as the counter key: a
# caller that varies the header lands in a fresh bucket on every request and
# the per-IP budgets on /auth/login and /auth/register never fire at all.
#
# So the header is honoured only when the connection itself came from a proxy
# the operator named in TRUSTED_PROXY_IPS, and the chain is then walked from
# the RIGHT, skipping entries that are themselves trusted proxies, until an
# address we did not add is found. Everything to the left of it is ignored.
# Unset -- the default -- means no proxy is trusted and the peer address is
# used, which is correct for a backend clients can reach directly.


@lru_cache(maxsize=8)
def _parse_networks(raw: str) -> Tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Parse and cache TRUSTED_PROXY_IPS. Bare addresses count as /32 or /128."""
    networks = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            # A typo must not silently widen trust, so the entry is dropped
            # and the rest still apply.
            logger.warning(
                "TRUSTED_PROXY_IPS: ignoring unparseable entry %r", entry)
    return tuple(networks)


def trusted_proxy_networks() -> Tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Read the setting at call time, as the rest of the backend does."""
    return _parse_networks(os.getenv("TRUSTED_PROXY_IPS", "").strip())


def _is_trusted(address: Optional[str], networks) -> bool:
    if not address or not networks:
        return False
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in networks)


def _resolve_client(request: Request) -> Tuple[Optional[str], bool]:
    """Return ``(address, came_from_a_trusted_proxy)``.

    The second value is what tells RiskService whether the address is the real
    client's or merely the peer we happen to be talking to -- see
    network_service.network_signal_known.
    """
    peer = request.client.host if request.client else None
    networks = trusted_proxy_networks()
    if not _is_trusted(peer, networks):
        # Either nothing is trusted, or this connection did not come from a
        # proxy. Whatever the header says, we are talking to this address.
        return peer, False

    forwarded = request.headers.get("x-forwarded-for") or ""
    for entry in reversed(forwarded.split(",")):
        entry = entry.strip()
        if not entry:
            continue
        try:
            ipaddress.ip_address(entry)
        except ValueError:
            continue  # not an address; keep walking left
        if not _is_trusted(entry, networks):
            return entry, True

    # Only proxies in the chain, or no usable header. The nearest thing to a
    # client we can honestly name is the proxy we are talking to.
    return peer, False


def client_ip(request: Request) -> str | None:
    """The caller's address, as far as it can be established.

    Safe to use as a rate-limit key: see the note above for why the raw header
    is not.
    """
    return _resolve_client(request)[0]


def client_ip_is_forwarded(request: Request) -> bool:
    """True when ``client_ip`` came from a trusted proxy's X-Forwarded-For.

    False means the address is the direct peer -- which, behind container
    networking, is infrastructure rather than the student. RiskService uses
    this to omit the network component instead of inventing a verdict.
    """
    return _resolve_client(request)[1]


def user_agent(request: Request) -> str | None:
    ua = request.headers.get("user-agent")
    return ua[:500] if ua else None
