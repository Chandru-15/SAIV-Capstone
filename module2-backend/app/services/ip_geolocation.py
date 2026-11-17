"""Where an IP address appears to be, when that is knowable at all.

This exists so the geolocation component can cross-check a *claimed* GPS fix
against a second, independent observation the client does not control. GPS
coordinates arrive in the request body and a spoofer picks them freely; the
source address is chosen by whoever routes the packet, so making both agree
costs the spoofer a proxy near the venue instead of a browser console.

THE HONEST LIMITS, because they decide how this is used:

  * IP geolocation is coarse. A mobile carrier can egress a whole country
    through one city, and a university's traffic may leave through a campus
    gateway kilometres from the lecture theatre. City-level accuracy is the
    best case, and GeoLite2 reports its own ``accuracy_radius``.
  * It is therefore evidence of a *gross* mismatch -- a fix in Singapore from
    an address that egresses in Germany -- and nothing finer. The threshold in
    risk_service is set accordingly, and the resolver's own accuracy radius is
    subtracted before any comparison.
  * A VPN moves the apparent address, so a mismatch may mean "VPN" rather than
    "spoofed GPS". The network component already scores VPN use; this one adds
    the geographic contradiction, and a reviewer sees both signals.

WHEN IT RETURNS None, WHICH IS MOST OF THE TIME HERE: a private or loopback
address carries no location at all, and without a GeoLite2 database there is
nothing to look anything up in. Both cases return None, the signal is omitted,
and RiskService renormalises over the components that do apply -- the same
"absent evidence rather than invented evidence" rule network_service follows
for a private direct peer. In this deployment every request arrives from the
Docker bridge gateway, so this resolver is inert until the service sits behind
a real reverse proxy that sets X-Forwarded-For AND a database is provided.

SETUP (optional, and nothing breaks without it):

    pip install geoip2
    # Download GeoLite2-City.mmdb from MaxMind (free, account required)
    export GEOIP_DATABASE_PATH=/path/to/GeoLite2-City.mmdb

No address is ever sent to a third-party service. A lookup is a local read
against a file, so a student's IP does not leave the deployment and a check-in
does not depend on somebody else's uptime or rate limit.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import threading
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# GeoLite2 omits accuracy_radius on some records. Assume a generous city-sized
# uncertainty rather than treating a silent record as pinpoint accurate: this
# value is SUBTRACTED from the measured distance, so a wrong guess here must
# err towards reporting no mismatch.
DEFAULT_ACCURACY_RADIUS_METERS = 50_000.0


@dataclass(frozen=True)
class IpLocation:
    """A resolved address. ``accuracy_radius_meters`` is the resolver's own
    stated uncertainty, not a measurement of the client."""

    latitude: float
    longitude: float
    accuracy_radius_meters: float
    source: str


def _is_locatable(ip: str) -> bool:
    """False for anything whose address carries no geography.

    Private, loopback, link-local, multicast and reserved ranges all describe
    position inside somebody's network rather than a place on earth.
    """
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
    )


class _ReaderCache:
    """One open reader per process.

    ``geoip2`` memory-maps the database; reopening it per check-in would make
    every submission pay for the mapping. The reader is thread-safe for reads,
    which is what a sync FastAPI endpoint running in the threadpool needs.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reader = None
        self._path: Optional[str] = None
        self._failed_for: Optional[str] = None

    def get(self, path: str):
        with self._lock:
            if self._reader is not None and self._path == path:
                return self._reader
            if self._failed_for == path:
                # Already tried and could not open it. Do not retry on every
                # check-in: a missing file or absent package will not appear
                # mid-process, and the log line has been emitted once already.
                return None
            self._close_locked()
            try:
                import geoip2.database  # imported lazily: an optional extra

                self._reader = geoip2.database.Reader(path)
                self._path = path
                self._failed_for = None
                logger.info("GeoIP database opened: %s", path)
            except Exception as exc:
                self._failed_for = path
                logger.warning(
                    "GeoIP lookups disabled (%s): %s. "
                    "The geolocation/IP consistency signal will be omitted.",
                    type(exc).__name__, exc,
                )
                return None
            return self._reader

    def _close_locked(self) -> None:
        if self._reader is not None:
            try:
                self._reader.close()
            except Exception:  # pragma: no cover - close is best effort
                pass
        self._reader = None
        self._path = None

    def reset(self) -> None:
        """Drop the cached reader. For tests, and for a config reload."""
        with self._lock:
            self._close_locked()
            self._failed_for = None


_readers = _ReaderCache()


def database_path() -> Optional[str]:
    """Read the configured path on every call, not at import.

    The rest of the backend reads configuration with ``os.getenv`` at use
    time; doing the same here keeps this module importable and testable with
    no environment at all.
    """
    path = os.getenv("GEOIP_DATABASE_PATH", "").strip()
    return path or None


def resolve(ip_address: Optional[str]) -> Optional[IpLocation]:
    """Best-effort location for ``ip_address``, or None when unknowable.

    Never raises. A check-in must not fail because a GeoIP file was moved, so
    every failure path degrades to "no information" and the caller omits the
    signal.
    """
    if not ip_address or not _is_locatable(ip_address):
        return None
    path = database_path()
    if not path:
        return None
    reader = _readers.get(path)
    if reader is None:
        return None
    try:
        record = reader.city(ip_address)
    except Exception:
        # AddressNotFoundError is the common, uninteresting case: a legitimate
        # address simply absent from the free database. Debug, not warning.
        logger.debug("no GeoIP record for %s", ip_address)
        return None
    location = getattr(record, "location", None)
    latitude = getattr(location, "latitude", None)
    longitude = getattr(location, "longitude", None)
    if latitude is None or longitude is None:
        return None
    radius_km = getattr(location, "accuracy_radius", None)
    radius = (
        float(radius_km) * 1000.0
        if isinstance(radius_km, (int, float))
        else DEFAULT_ACCURACY_RADIUS_METERS
    )
    return IpLocation(
        latitude=float(latitude),
        longitude=float(longitude),
        accuracy_radius_meters=radius,
        source="geolite2-city",
    )
