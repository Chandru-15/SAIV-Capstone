"""Server-side VPN / proxy detection.

Follows the reference implementation in API-SPECIFICATION.md "Step 6:
VPN/Proxy Detection" — private IP ranges at 0.7 confidence, VPN keywords in
the user agent at 0.8 — with one necessary departure, explained below.

This lives in module 2 rather than being read off the request body. A client
that self-reports `vpn_detected` will never report `true`: the only effect of
the flag is to raise its own risk score, so an honest client gains nothing and
a dishonest one simply omits it. Detection has to happen where the connection
terminates.

Module 3 has the same heuristic inside POST /risk/assess, but module 2 does
not call that endpoint for the check-in decision (it must keep working when
module 3 is down, and module 3 cannot see enrolment, device binding or session
policy). So the logic is duplicated deliberately; see app/clients/README.md.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

PRIVATE_IP_CONFIDENCE = 0.7
USER_AGENT_CONFIDENCE = 0.8
VPN_KEYWORDS = ("vpn", "proxy", "tunnel", "tor")


def _is_private(ip: str) -> bool:
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return parsed.is_private or parsed.is_loopback or parsed.is_link_local


def detect_vpn_proxy(
    ip_address: Optional[str],
    user_agent: Optional[str] = None,
    *,
    forwarded: bool = True,
) -> Tuple[bool, float]:
    """Return ``(is_vpn, confidence)`` for a connection.

    ``forwarded`` says whether ``ip_address`` is the real client address taken
    from X-Forwarded-For, or merely the direct peer.

    THE DEPARTURE FROM THE SPEC: the reference implementation treats any
    private address as a VPN indicator at 0.7. In this deployment every
    legitimate request arrives from the Docker bridge gateway — measured as
    172.19.0.1 across all 28 recorded audit entries — which that rule flags.
    Applied literally it would add 0.7 x 0.15 = 0.105 risk to every check-in
    in the system, and tip borderline ones over the threshold.

    A private *direct peer* means "we are behind infrastructure", not "the user
    is on a VPN". In that case we have no information about the real client, so
    we report no detection and let RiskService exclude the network component
    and renormalise — absent evidence rather than invented evidence.

    Once a real reverse proxy sets X-Forwarded-For, ``forwarded=True`` and a
    private address there genuinely is a client-side tunnel, so the spec's rule
    applies as written.
    """
    is_vpn = False
    confidence = 0.0

    if ip_address:
        if _is_private(ip_address):
            if forwarded:
                is_vpn = True
                confidence = PRIVATE_IP_CONFIDENCE
            else:
                logger.debug(
                    "private direct peer %s: treating network signal as unknown",
                    ip_address)
        # A public address is the normal case and carries no signal on its own.
        # Real VPN detection needs an IP-intelligence lookup, which is out of
        # scope here; this heuristic cannot see a commercial VPN exit node.

    if user_agent and any(kw in user_agent.lower() for kw in VPN_KEYWORDS):
        # Trivially spoofable, and nothing honest sets it, so it only ever
        # catches careless tooling. Kept because the spec specifies it.
        is_vpn = True
        confidence = max(confidence, USER_AGENT_CONFIDENCE)

    return is_vpn, confidence


def network_signal_known(ip_address: Optional[str], *, forwarded: bool) -> bool:
    """Whether we have any basis for a network judgement at all.

    False when the only address available is a private direct peer, which is
    what RiskService uses to omit the component rather than score it zero.
    """
    if not ip_address:
        return False
    return forwarded or not _is_private(ip_address)
