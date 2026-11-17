"""Risk assessment: turn a check-in submission into signals and a score.

Weights come from the API specification's signal table:

    Liveness      25%   risk = 1 - liveness_score
    Face match    25%   risk = 1 - face_match_score
    Device        20%
    Network       15%
    Geolocation   15%

Only *applicable* components contribute. If a session does not require a face
match, that 25% is not counted as zero risk — it is excluded and the remaining
weights are renormalised. Counting an absent check as "passed" would let a
session with face matching disabled score artificially low and slip under the
threshold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..models import RiskSeverity, RiskSignal, RiskSignalType
from .liveness_challenge import ACTIVE_CHALLENGE_TYPES

WEIGHT_LIVENESS = 0.25
WEIGHT_FACE_MATCH = 0.25
WEIGHT_DEVICE = 0.20
WEIGHT_NETWORK = 0.15
WEIGHT_GEO = 0.15

# Carried on the retry signal as metadata only. Attempts do not affect the
# risk score at all, so this is never summed into anything.
WEIGHT_RETRY = 0.20

# Severity bands, from the API specification.
SEVERITY_BANDS = (
    (0.7, RiskSeverity.CRITICAL),
    (0.5, RiskSeverity.HIGH),
    (0.3, RiskSeverity.MEDIUM),
)

# A GPS accuracy worse than this makes the location claim meaningless.
POOR_ACCURACY_METERS = 100.0
LOW_CONFIDENCE_THRESHOLD = 0.5

# -- Geolocation consistency -------------------------------------------------
# A claimed GPS fix is checked against two things the client does not choose:
# where this student last was, and where their source address appears to be.
# Both are CONSISTENCY checks, not position checks -- they say the claim
# contradicts other evidence, which is a different accusation from "you are
# not at the venue" and is why they live beside the geofence rather than in it.

# Ground speed implied by two consecutive positioned observations of the same
# student. 200 km/h clears any surface transport a student could plausibly be
# on (Singapore's MRT tops out near 80) while staying far below an airliner,
# so a genuine flight between two campuses does not read as fraud.
IMPLAUSIBLE_SPEED_KMH = 200.0

# Below this, a change of position is GPS noise rather than travel. A phone
# indoors wanders tens to hundreds of metres between fixes; divided by the few
# seconds between two retry attempts that noise alone yields an aircraft's
# speed from a student who never left their seat. The floor is what makes the
# speed test safe to apply to rapid retries, which is where it is most useful.
MIN_TRAVEL_DISTANCE_METERS = 1000.0

# How far a GPS fix may sit from the apparent location of the source address
# before the two count as contradicting each other, AFTER the resolver's own
# accuracy radius has been subtracted. Deliberately coarse: carrier egress and
# campus gateways legitimately sit far from the user, so only a gross mismatch
# -- a fix in Singapore from an address that egresses in Europe -- is evidence.
# See ip_geolocation for the full argument.
IP_MISMATCH_DISTANCE_METERS = 150_000.0

# A mismatch is strong but confounded: a VPN produces one from an honest
# student. High enough to matter, not high enough to decide on its own.
IP_MISMATCH_RISK = 0.6

RECOMMENDATIONS = {
    RiskSignalType.LIVENESS_LOW_CONFIDENCE: "Improve lighting and face visibility",
    RiskSignalType.LIVENESS_FAILED: "Improve lighting and face visibility",
    RiskSignalType.REPLAY_SUSPECTED: "Complete the on-screen liveness challenge",
    RiskSignalType.FACE_MATCH_LOW_CONFIDENCE: "Re-enroll face or improve image quality",
    RiskSignalType.FACE_MATCH_FAILED: "Re-enroll face or improve image quality",
    RiskSignalType.VPN_DETECTED: "Disable VPN for check-in",
    RiskSignalType.PROXY_DETECTED: "Disable proxy for check-in",
    RiskSignalType.TOR_DETECTED: "Disable Tor for check-in",
    RiskSignalType.GEO_ACCURACY_LOW: "Enable precise location services",
    RiskSignalType.GEO_OUT_OF_BOUNDS: "Check in from the class venue",
    RiskSignalType.IMPOSSIBLE_TRAVEL:
        "Turn off any location-mocking app and check in from the venue",
    # The commonest innocent cause of a GPS/IP contradiction, so the advice
    # matches the VPN signal's and the two collapse into one line for the
    # student when both fire.
    RiskSignalType.GEO_IP_MISMATCH: "Disable VPN for check-in",
    RiskSignalType.DEVICE_UNKNOWN: "Register this device before checking in",
    RiskSignalType.DEVICE_EMULATOR: "Check in from a physical device",
    RiskSignalType.DEVICE_ROOTED: "Check in from an unmodified device",
    RiskSignalType.RAPID_SUCCESSION:
        "Wait for the cooldown before trying again",
}


@dataclass
class CheckInContext:
    """Everything the risk engine needs, gathered by the check-in service.

    A plain data object rather than the ORM entity: risk assessment must be
    testable without a database.
    """

    # Geolocation
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_accuracy_meters: Optional[float] = None
    venue_latitude: Optional[float] = None
    venue_longitude: Optional[float] = None
    geofence_radius_meters: float = 100.0

    # The student's previous positioned observation, for the speed test. Set
    # by CheckInService from the most recent earlier attempt that carried
    # coordinates -- which may be an earlier attempt at THIS session (seconds
    # ago) or a check-in at another session (hours ago). Both are the same
    # question: could this person have got from there to here in that time?
    previous_latitude: Optional[float] = None
    previous_longitude: Optional[float] = None
    seconds_since_previous_fix: Optional[float] = None

    # Apparent location of the source address, when it is knowable at all.
    # None in the ordinary case -- see ip_geolocation -- and the signal is then
    # omitted rather than scored as agreement.
    ip_latitude: Optional[float] = None
    ip_longitude: Optional[float] = None
    ip_accuracy_radius_meters: Optional[float] = None

    # Liveness / face
    require_liveness: bool = True
    liveness_passed: Optional[bool] = None
    liveness_score: Optional[float] = None
    # HOW the liveness verdict was obtained: "blink" / "head_turn" for a
    # server-issued challenge, "passive" for a single still image, None when no
    # biometric evidence was submitted at all. Part of the decision, not
    # metadata: a verdict only evidences liveness if the server chose the
    # challenge, so see _liveness below and CheckInService._unmet_requirements.
    liveness_challenge_type: Optional[str] = None
    require_face_match: bool = False
    face_match_passed: Optional[bool] = None
    face_match_score: Optional[float] = None

    # Device. `attestation_known` is False until an attestation ceremony has
    # actually run for this device, which today is always: nothing in the
    # codebase can produce a platform attestation verdict from a browser, so
    # every device sits at the column default. The sub-signal is then OMITTED
    # rather than scored as a failure -- the same rule `network_signal_known`
    # applies to the network component, and for the same reason. Treating
    # "never attested" as "attestation failed" made registering a device
    # strictly worse than owning none, and made attestation_failed the most
    # frequently recorded signal in the system.
    require_device_binding: bool = True
    device_known: bool = False
    device_is_emulator: bool = False
    device_is_rooted: bool = False
    attestation_known: bool = False
    attestation_passed: bool = False

    # Network. `network_signal_known` is False when the backend has no view of
    # the real client address (a private direct peer behind a container bridge
    # or load balancer). The component is then OMITTED rather than scored 0.0,
    # so "we could not tell" does not masquerade as "no VPN".
    network_signal_known: bool = True
    vpn_detected: bool = False
    proxy_detected: bool = False
    tor_detected: bool = False
    suspicious_ip: bool = False

    # Which verification attempt this is, counting from 1. Retries are
    # allowed without limit but are not free: see _retries below.
    attempt_number: int = 1


@dataclass
class RiskAssessment:
    risk_score: float
    signals: List[RiskSignal] = field(default_factory=list)
    breakdown: Dict[str, float] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)
    distance_from_venue_meters: Optional[float] = None

    @property
    def has_critical(self) -> bool:
        return any(s.severity == RiskSeverity.CRITICAL for s in self.signals)


def severity_for(score: float) -> RiskSeverity:
    for floor, severity in SEVERITY_BANDS:
        if score >= floor:
            return severity
    return RiskSeverity.LOW


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance. Uses geopy when available, else a local fallback
    so risk assessment never depends on an optional import."""
    try:
        from geopy.distance import geodesic

        return float(geodesic((lat1, lon1), (lat2, lon2)).meters)
    except Exception:  # pragma: no cover - fallback path
        from math import asin, cos, radians, sin, sqrt

        r = 6371008.8
        dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        return 2 * r * asin(sqrt(a))


class RiskService:
    """Pure computation — no session, no database, no I/O."""

    def assess(self, ctx: CheckInContext) -> RiskAssessment:
        signals: List[RiskSignal] = []
        components: List[Tuple[str, float, float]] = []  # (name, risk, weight)

        distance = self._geo(ctx, signals, components)
        self._liveness(ctx, signals, components)
        self._face_match(ctx, signals, components)
        self._device(ctx, signals, components)
        self._network(ctx, signals, components)

        total_weight = sum(w for _, _, w in components)
        score = (
            sum(risk * weight for _, risk, weight in components) / total_weight
            if total_weight
            else 0.0
        )
        score = round(min(1.0, max(0.0, score)), 4)
        self._retries(ctx, signals)

        breakdown = {name: round(risk, 4) for name, risk, _ in components}

        return RiskAssessment(
            risk_score=score,
            signals=signals,
            breakdown=breakdown,
            recommendations=sorted(
                {RECOMMENDATIONS[s.signal_type] for s in signals
                 if s.signal_type in RECOMMENDATIONS}
            ),
            distance_from_venue_meters=distance,
        )

    # -- components ---------------------------------------------------------

    @staticmethod
    def _retries(ctx, signals) -> None:
        """Record how many attempts this took, WITHOUT scoring it.

        Needing a second go is not evidence of wrongdoing: the usual
        reason is that the verification itself misfired - a dark room, a
        stale GPS fix, a blink the detector missed. So an attempt is
        judged on its own merits exactly as a first attempt would be, and
        the history travels as a signal for the dashboard rather than as
        risk. What bounds grinding at the verifier is the published rate
        limit -- 10 check-ins per minute per user, enforced in
        checkin_controller -- plus review in the dashboard.

        Risk 0.0 keeps the severity at `low` and the score untouched: the
        signal is there to be read, not to decide anything.
        """
        if ctx.attempt_number <= 1:
            return
        signals.append(
            RiskService._signal(
                RiskSignalType.RAPID_SUCCESSION, 0.0, WEIGHT_RETRY,
                details={"attempt_number": ctx.attempt_number},
            )
        )

    def _geo(self, ctx, signals, components) -> Optional[float]:
        if ctx.latitude is None or ctx.longitude is None:
            # No consent, or no fix. Absent location is unverifiable rather than
            # innocent, so it carries full geo risk.
            components.append(("geolocation", 1.0, WEIGHT_GEO))
            return None

        # Consistency first, because neither check needs the venue: they ask
        # whether this fix contradicts other evidence, not whether it is in the
        # right place. A session with no coordinates still gets them.
        consistency = max(
            self._impossible_travel(ctx, signals),
            self._ip_mismatch(ctx, signals),
        )

        if ctx.venue_latitude is None or ctx.venue_longitude is None:
            # Venue unknown: no distance to score. A contradiction found above
            # is still real evidence, so it carries the component on its own
            # rather than being discarded with the geofence.
            if consistency > 0.0:
                components.append(("geolocation", consistency, WEIGHT_GEO))
            return None

        distance = haversine_meters(
            ctx.latitude, ctx.longitude, ctx.venue_latitude, ctx.venue_longitude
        )
        radius = ctx.geofence_radius_meters or 100.0
        # Risk ramps from 0 at the venue to 1 at the fence edge.
        risk = min(1.0, distance / radius) if radius else 1.0

        if distance > radius:
            signals.append(self._signal(
                RiskSignalType.GEO_OUT_OF_BOUNDS, risk, WEIGHT_GEO,
                {"distance_meters": round(distance, 1), "radius_meters": radius},
            ))
        if ctx.location_accuracy_meters and ctx.location_accuracy_meters > POOR_ACCURACY_METERS:
            signals.append(self._signal(
                RiskSignalType.GEO_ACCURACY_LOW, 0.5, WEIGHT_GEO,
                {"accuracy_meters": ctx.location_accuracy_meters},
            ))
        # The worse of "wrong place" and "impossible claim". A fix standing
        # exactly on the venue scores 0 on distance, which is precisely what a
        # spoofer submits, so the contradiction must not be averaged away by
        # the geofence agreeing with the lie.
        components.append(("geolocation", max(risk, consistency), WEIGHT_GEO))
        return distance

    def _impossible_travel(self, ctx, signals) -> float:
        """Speed implied by the move from the student's last known fix.

        Returns the geo risk this contributes: 1.0 when the implied speed is
        impossible, 0.0 otherwise. Reported as a signal rather than a
        rejection -- a stale or cached fix can manufacture a jump the student
        had no part in, so this earns human review, not an automatic refusal
        (see CheckInService._decide).
        """
        if ctx.previous_latitude is None or ctx.previous_longitude is None:
            return 0.0
        elapsed = ctx.seconds_since_previous_fix
        if elapsed is None or elapsed < 0:
            # A negative interval is a clock or ordering fault in our own data.
            # Absent evidence, not evidence of fraud.
            return 0.0

        distance = haversine_meters(
            ctx.previous_latitude, ctx.previous_longitude,
            ctx.latitude, ctx.longitude,
        )
        if distance < MIN_TRAVEL_DISTANCE_METERS:
            return 0.0  # GPS noise, not travel

        # Zero elapsed with a real distance is infinite speed: two positions at
        # the same instant. Kept separate so the metadata stays valid JSON.
        speed_kmh = None if elapsed == 0 else (distance / elapsed) * 3.6
        if speed_kmh is not None and speed_kmh <= IMPLAUSIBLE_SPEED_KMH:
            return 0.0

        signals.append(self._signal(
            RiskSignalType.IMPOSSIBLE_TRAVEL, 1.0, WEIGHT_GEO,
            {
                "distance_meters": round(distance, 1),
                "elapsed_seconds": round(elapsed, 1),
                "implied_speed_kmh":
                    round(speed_kmh, 1) if speed_kmh is not None else None,
                "limit_kmh": IMPLAUSIBLE_SPEED_KMH,
            },
        ))
        return 1.0

    def _ip_mismatch(self, ctx, signals) -> float:
        """Does the claimed fix contradict where the source address appears?

        The resolver's own accuracy radius is subtracted before comparing, so
        a record that admits to being vague cannot produce a mismatch on its
        own vagueness. Returns 0.0 whenever the address could not be located,
        which is the normal case -- see ip_geolocation.
        """
        if ctx.ip_latitude is None or ctx.ip_longitude is None:
            return 0.0

        distance = haversine_meters(
            ctx.latitude, ctx.longitude, ctx.ip_latitude, ctx.ip_longitude
        )
        radius = max(0.0, ctx.ip_accuracy_radius_meters or 0.0)
        margin = distance - radius
        if margin <= IP_MISMATCH_DISTANCE_METERS:
            return 0.0

        signals.append(self._signal(
            RiskSignalType.GEO_IP_MISMATCH, IP_MISMATCH_RISK, WEIGHT_GEO,
            {
                "distance_meters": round(distance, 1),
                "ip_accuracy_radius_meters": round(radius, 1),
                "excess_meters": round(margin - IP_MISMATCH_DISTANCE_METERS, 1),
                "limit_meters": IP_MISMATCH_DISTANCE_METERS,
            },
        ))
        return IP_MISMATCH_RISK

    def _liveness(self, ctx, signals, components) -> None:
        """Score the liveness verdict, and first ask whether it is admissible.

        A verdict produced WITHOUT a server-issued challenge carries no
        evidence of liveness, however high its score. The passive path scores
        detection confidence, image quality and an inferred depth value -- all
        three measure "is there a clear, well-lit face here", which a sharp
        printed photo satisfies better than a real one, not worse. Only the
        active path is bound to a challenge this server chose, for this
        student, seconds ago, once (see liveness_challenge).

        So an unchallenged verdict is scored as full risk, exactly as a missing
        one is, rather than being trusted at its face value. The verdict itself
        is still reported on the record; what changes is that it no longer
        satisfies a session that requires liveness.
        """
        if not ctx.require_liveness:
            return

        # `is not None` deliberately: a type of None means nothing biometric
        # was submitted, which `liveness_passed is not True` already covers.
        # Calling that a suspected replay would point a reviewer at an attack
        # when the real story is usually a student who submitted no image, or a
        # module 3 outage.
        unchallenged = (
            ctx.liveness_challenge_type is not None
            and ctx.liveness_challenge_type not in ACTIVE_CHALLENGE_TYPES
        )
        if unchallenged:
            signals.append(self._signal(
                RiskSignalType.REPLAY_SUSPECTED, 1.0, WEIGHT_LIVENESS,
                {"liveness_challenge_type": ctx.liveness_challenge_type,
                 "reason": "no_server_issued_challenge"},
            ))

        if ctx.liveness_passed is False:
            signals.append(self._signal(RiskSignalType.LIVENESS_FAILED, 1.0, WEIGHT_LIVENESS))
            components.append(("liveness", 1.0, WEIGHT_LIVENESS))
            return
        if unchallenged or ctx.liveness_score is None:
            components.append(("liveness", 1.0, WEIGHT_LIVENESS))
            return
        risk = 1.0 - ctx.liveness_score
        if ctx.liveness_score < LOW_CONFIDENCE_THRESHOLD:
            signals.append(self._signal(
                RiskSignalType.LIVENESS_LOW_CONFIDENCE, risk, WEIGHT_LIVENESS,
                {"liveness_score": ctx.liveness_score},
            ))
        components.append(("liveness", risk, WEIGHT_LIVENESS))

    def _face_match(self, ctx, signals, components) -> None:
        if not ctx.require_face_match:
            return
        if ctx.face_match_passed is False:
            signals.append(self._signal(RiskSignalType.FACE_MATCH_FAILED, 1.0, WEIGHT_FACE_MATCH))
            components.append(("face_match", 1.0, WEIGHT_FACE_MATCH))
            return
        if ctx.face_match_score is None:
            components.append(("face_match", 1.0, WEIGHT_FACE_MATCH))
            return
        risk = 1.0 - ctx.face_match_score
        if ctx.face_match_score < LOW_CONFIDENCE_THRESHOLD:
            signals.append(self._signal(
                RiskSignalType.FACE_MATCH_LOW_CONFIDENCE, risk, WEIGHT_FACE_MATCH,
                {"face_match_score": ctx.face_match_score},
            ))
        components.append(("face_match", risk, WEIGHT_FACE_MATCH))

    def _device(self, ctx, signals, components) -> None:
        risk = 0.0
        if ctx.device_is_emulator:
            signals.append(self._signal(RiskSignalType.DEVICE_EMULATOR, 1.0, WEIGHT_DEVICE))
            risk = 1.0
        if ctx.device_is_rooted:
            signals.append(self._signal(RiskSignalType.DEVICE_ROOTED, 0.8, WEIGHT_DEVICE))
            risk = max(risk, 0.8)
        if not ctx.device_known:
            severity_risk = 1.0 if ctx.require_device_binding else 0.5
            signals.append(self._signal(RiskSignalType.DEVICE_UNKNOWN, severity_risk, WEIGHT_DEVICE))
            risk = max(risk, severity_risk)
        if ctx.attestation_known and not ctx.attestation_passed:
            # Only a ceremony that RAN and FAILED is evidence. "No verdict" is
            # the normal state of a browser and says nothing about the device;
            # an unregistered device is already covered by DEVICE_UNKNOWN above.
            signals.append(self._signal(RiskSignalType.ATTESTATION_FAILED, 0.7, WEIGHT_DEVICE))
            risk = max(risk, 0.7)
        components.append(("device", risk, WEIGHT_DEVICE))

    def _network(self, ctx, signals, components) -> None:
        if not ctx.network_signal_known:
            # No basis for a judgement: contribute nothing and let the weighted
            # average renormalise over the components that do apply.
            return
        risk = 0.0
        for flag, signal_type, value in (
            (ctx.tor_detected, RiskSignalType.TOR_DETECTED, 1.0),
            (ctx.vpn_detected, RiskSignalType.VPN_DETECTED, 0.7),
            (ctx.proxy_detected, RiskSignalType.PROXY_DETECTED, 0.7),
            (ctx.suspicious_ip, RiskSignalType.SUSPICIOUS_IP, 0.6),
        ):
            if flag:
                signals.append(self._signal(signal_type, value, WEIGHT_NETWORK))
                risk = max(risk, value)
        components.append(("network", risk, WEIGHT_NETWORK))

    @staticmethod
    def _signal(signal_type, risk: float, weight: float, details=None) -> RiskSignal:
        import json

        return RiskSignal(
            signal_type=signal_type,
            severity=severity_for(risk),
            confidence=1.0,
            weight=round(weight, 4),
            details=json.dumps(details, default=str) if details else None,
        )
