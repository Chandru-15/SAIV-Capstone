"""The check-in pipeline — the heart of the system.

Ordering matters here. Cheap, certain rejections (window closed, not enrolled,
already checked in) run before expensive risk assessment, so a student who is
simply early does not burn a face-match call.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func, select

from ..models import (
    AttendanceSession,
    AuditAction,
    CheckIn,
    CheckInAttempt,
    CheckInStatus,
    RiskSignalType,
    SessionStatus,
    User,
    UserRole,
)
from ..models.base import utcnow
from ..repositories import (
    AttendanceSessionRepository,
    CheckInRepository,
    CourseRepository,
    DeviceRepository,
    EnrollmentRepository,
    Page,
    RiskSignalRepository,
)
from .audit_service import AuditService
from .base import BaseService, transactional
from .browser_verification import BrowserProof, BrowserProofError, BrowserVerificationService
from .exceptions import (
    AuthenticationError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from .face_service import FaceService
from .ip_geolocation import resolve as resolve_ip_location
from .network_service import detect_vpn_proxy, network_signal_known
from . import liveness_challenge
from .risk_service import CheckInContext, RiskAssessment, RiskService
from .staff_scope import require_session_visible, student_group_ids, visible_session_ids
from .user_service import UserService

# PII retention window for check-in records.
RETENTION_DAYS = 30

# A fix this far outside the fence is not a bad GPS reading, it is somewhere
# else. The API spec makes this an outright rejection.
HARD_REJECT_DISTANCE_MULTIPLIER = 2.0

# Module 3 recommends the passive challenge: it reads 3D depth cues from a
# single frame and needs no user interaction.
DEFAULT_LIVENESS_CHALLENGE = "passive"

_REVIEWERS = (UserRole.ADMIN, UserRole.INSTRUCTOR, UserRole.TA)

# One store per process. Built lazily so importing this module never tries to
# reach Redis, which would make `import app.main` fail when Redis is down.
_CHALLENGE_STORE = None


def _shared_challenge_store():
    global _CHALLENGE_STORE
    if _CHALLENGE_STORE is None:
        _CHALLENGE_STORE = liveness_challenge.build_store()
    return _CHALLENGE_STORE


class CheckInService(BaseService):
    def __init__(self, session, face_service: Optional[FaceService] = None,
                 challenge_store=None) -> None:
        super().__init__(session)
        self.faces = face_service or FaceService(session)
        self.challenges = challenge_store or _shared_challenge_store()
        self.checkins = CheckInRepository(session)
        self.sessions = AttendanceSessionRepository(session)
        self.courses = CourseRepository(session)
        self.enrollments = EnrollmentRepository(session)
        self.devices = DeviceRepository(session)
        self.signals = RiskSignalRepository(session)
        self.audit = AuditService(session)
        self.risk = RiskService()

    # -- submission ---------------------------------------------------------

    @transactional
    def submit(
        self,
        student: User,
        session_id: str,
        *,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        location_accuracy_meters: Optional[float] = None,
        device_fingerprint: Optional[str] = None,
        image: Optional[str] = None,
        challenge_id: Optional[str] = None,
        challenge_frames: Optional[List[str]] = None,
        liveness_passed: Optional[bool] = None,
        liveness_score: Optional[float] = None,
        liveness_challenge_type: Optional[str] = None,
        face_match_passed: Optional[bool] = None,
        face_match_score: Optional[float] = None,
        face_embedding_hash: Optional[str] = None,
        qr_code_secret: Optional[str] = None,
        retry: bool = False,
        browser_proof: Optional[BrowserProof] = None,
        ip_address: Optional[str] = None,
        ip_is_forwarded: bool = False,
        user_agent: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> CheckIn:
        # Keep the pilot policy and account version stable until the attempt
        # commits. Authentication may have read the account before a policy
        # change, so refresh it while taking the shared lock.
        version = student.token_version
        account = self.session.get(User, student.id, with_for_update={"read": True}, populate_existing=True)
        if account is None or not account.is_active or account.token_version != version:
            raise AuthenticationError("Your account changed. Sign in again before checking in.")
        student = account
        verify_browser = student.browser_verification_required or browser_proof is not None
        if verify_browser:
            if browser_proof is None or not device_fingerprint:
                raise BrowserProofError("This check-in requires proof from an enrolled browser key.")
            # Different valid nonces (and different browsers) must still
            # serialize the attempt lookup and update for this attendance row.
            self.session.execute(select(func.pg_advisory_xact_lock(
                func.hashtextextended(f"saiv:browser-checkin:{student.id}:{session_id}", 0)
            )))
        moment = now or utcnow()
        session = self._load_session(session_id)
        course = self.courses.find_by_id(session.course_id)

        self._assert_can_check_in(student, session, moment, allow_retry=retry)
        previous = self._resolve_attempt(student, session, moment, retry)
        attempt_number = 1 if previous is None else self._attempt_number(previous) + 1

        device = (
            self.devices.find_for_user(student.id, device_fingerprint, lock=verify_browser)
            if device_fingerprint
            else None
        )
        if now is None and utcnow() > session.checkin_closes_at:
            raise BusinessRuleError("The check-in window for this session is closed")
        if verify_browser:
            BrowserVerificationService(self.session).verify_checkin(student, session.id, device, browser_proof)

        # Consent gates the data, not the check-in: a student who withheld
        # location consent may still check in, but the missing signal counts
        # against their risk score rather than being treated as a pass.
        if not student.geolocation_consent:
            latitude = longitude = location_accuracy_meters = None
        if not student.camera_consent:
            image = challenge_frames = None
            liveness_passed = liveness_score = None
            face_match_passed = face_match_score = face_embedding_hash = None

        if challenge_frames:
            (
                liveness_passed, liveness_score, liveness_challenge_type,
                face_match_passed, face_match_score, face_embedding_hash,
            ) = self._verify_challenge(
                student, session, course, challenge_frames, challenge_id,
            )
        elif image is not None:
            # Derive the biometric signals from the image by calling module 3,
            # and DISCARD whatever the client claimed. A client-supplied
            # liveness_score is worthless in an anti-spoofing system: the
            # cheating student is the one filling in the form.
            (
                liveness_passed, liveness_score, liveness_challenge_type,
                face_match_passed, face_match_score, face_embedding_hash,
            ) = self._verify_biometrics(
                student, session, course, image,
                challenge_type=liveness_challenge_type,
            )

        ctx = self._build_context(
            session, course, device,
            latitude=latitude, longitude=longitude,
            location_accuracy_meters=location_accuracy_meters,
            liveness_passed=liveness_passed, liveness_score=liveness_score,
            liveness_challenge_type=liveness_challenge_type,
            face_match_passed=face_match_passed, face_match_score=face_match_score,
            attempt_number=attempt_number,
            **self._network_flags(ip_address, user_agent, ip_is_forwarded),
            **self._geo_history_flags(student, latitude, longitude, moment),
            **self._ip_location_flags(ip_address, ip_is_forwarded),
        )
        assessment = self.risk.assess(ctx)
        status = self._decide(assessment, ctx, session, course)

        # On a retry the existing row is updated in place: one attendance
        # outcome per student per session stays the invariant, and
        # uq_checkins_session_student still enforces it. checked_in_at and
        # scheduled_deletion_at keep their first-attempt values, so retrying
        # neither extends the appeal window nor defers PII deletion.
        record = previous or CheckIn(
            session_id=session.id,
            student_id=student.id,
            checked_in_at=moment,
            scheduled_deletion_at=moment + timedelta(days=RETENTION_DAYS),
        )
        record.device_id = device.id if device else None
        record.browser_key_verified = verify_browser
        record.status = status
        record.verified_at = moment if status == CheckInStatus.APPROVED else None
        record.latitude = latitude
        record.longitude = longitude
        record.location_accuracy_meters = location_accuracy_meters
        record.distance_from_venue_meters = assessment.distance_from_venue_meters
        record.liveness_passed = liveness_passed
        record.liveness_score = liveness_score
        record.liveness_challenge_type = liveness_challenge_type
        record.face_match_passed = face_match_passed
        record.face_match_score = face_match_score
        record.face_embedding_hash = face_embedding_hash
        record.risk_score = assessment.risk_score
        record.risk_factors = self._encode_factors(assessment)
        record.qr_code_verified = self._verify_qr(session, qr_code_secret, moment)
        checkin = self.checkins.save(record)

        # Stamp the retry signal with this attempt's time instead of letting
        # the column default to NOW(), so a caller-supplied `now` wins and a
        # reviewer sees when each attempt actually happened.
        for signal in assessment.signals:
            if signal.signal_type == RiskSignalType.RAPID_SUCCESSION:
                signal.detected_at = moment
        # Appended, never replaced: the earlier attempts' signals are the
        # evidence a reviewer needs to see that this pass took five tries.
        self.signals.record_all(checkin.id, assessment.signals)

        # The row above was UPDATED on a retry, so everything the superseded
        # attempt measured is now gone from `checkins`. Snapshot this attempt
        # before returning, including the first: a trail missing the attempt
        # that counted is not a trail.
        self.checkins.record_attempt(CheckInAttempt(
            checkin_id=checkin.id,
            attempt_number=attempt_number,
            status=status,
            risk_score=assessment.risk_score,
            risk_factors=record.risk_factors,
            latitude=latitude,
            longitude=longitude,
            location_accuracy_meters=location_accuracy_meters,
            distance_from_venue_meters=assessment.distance_from_venue_meters,
            liveness_passed=liveness_passed,
            liveness_score=liveness_score,
            face_match_passed=face_match_passed,
            face_match_score=face_match_score,
            device_id=device.id if device else None,
            browser_key_verified=verify_browser,
            attempted_at=moment,
            # Swept with the check-in it belongs to, not on its own clock.
            scheduled_deletion_at=record.scheduled_deletion_at,
        ))

        if device is not None:
            self.devices.touch_seen(device, count_checkin=True)
            self.devices.refresh_trust(device)

        self.audit.record(
            self._audit_action(status),
            user_id=student.id,
            resource_type="checkin",
            resource_id=checkin.id,
            ip_address=ip_address,
            user_agent=user_agent,
            device_id=device.id if device else None,
            details={"session_id": session.id, "risk_score": assessment.risk_score,
                     "status": status.value},
            success=status != CheckInStatus.REJECTED,
        )
        return checkin

    def _verify_challenge(
        self, student: User, session: AttendanceSession, course,
        frames: List[str], challenge_id: Optional[str],
    ):
        """Verify an active liveness challenge: blink, head-turn or colour.

        The challenge must have been issued by this server, to this student,
        for this session, and must not have been used before. That binding is
        the whole point: module 3 can confirm a blink occurred in the frames,
        but only we can confirm it is the blink we just asked for. Without it,
        a client replays one recorded blink for the rest of term.

        For ``colour_reflection`` the binding is stronger, because the thing
        being checked is not a gesture the student could repeat on demand but
        a sequence of colours this server invented seconds ago. Module 3 is
        told what the screen was asked to show; it is never told by the
        client.
        """
        if not challenge_id:
            raise BusinessRuleError(
                "challenge_id is required when submitting challenge_frames; "
                "request one from POST /checkins/liveness-challenge")

        challenge = self.challenges.take(challenge_id)
        if challenge is None:
            # Covers unknown, expired and already-used in one message: telling
            # a caller which of the three it was only helps an attacker probe.
            raise BusinessRuleError("Liveness challenge is invalid, expired or already used")
        if challenge.student_id != student.id or challenge.session_id != session.id:
            raise PermissionDeniedError("This liveness challenge was issued for a different check-in")
        if len(frames) < challenge.min_frames:
            raise ValidationError(
                f"A {challenge.challenge_type} challenge needs at least "
                f"{challenge.min_frames} frames, got {len(frames)}")

        colour_sequence = None
        if challenge.challenge_type == "colour_reflection":
            # EXACT, not a minimum. The frames carry no labels, so the colour
            # each one was taken under is recovered from its position in the
            # list; a short submission would mean guessing which steps were
            # dropped, and every wrong guess scores frames against a colour
            # they were never lit by. Asking the client to label them instead
            # would hand it the answer key.
            if len(frames) != challenge.expected_frames:
                raise ValidationError(
                    f"A colour_reflection challenge needs exactly "
                    f"{challenge.expected_frames} frames, got {len(frames)}")
            colour_sequence = challenge.colours_per_frame

        # Server's type and server's sequence, never the client's.
        liveness = self.faces.try_liveness_frames(
            frames, challenge.challenge_type, colour_sequence)
        liveness_passed = liveness.liveness_passed if liveness else None
        liveness_score = liveness.liveness_score if liveness else None
        face_embedding_hash = liveness.face_embedding_hash if liveness else None

        # An active challenge that was NOT completed is a hard liveness
        # failure, whatever the image quality score says.
        if liveness is not None and liveness.challenge_passed is False:
            liveness_passed = False
            liveness_score = 0.0

        # No verdict is not a failed verdict, and it is not a pass either.
        #
        # The colour challenge can come back undecided: the screen was too
        # dim, the room too bright, the face too far away. Recording that as
        # a liveness failure would accuse a student of spoofing over their
        # lighting -- the same mistake as reading an absent attestation
        # verdict as a failed one. Recording it as a pass is worse: a tablet
        # held far enough back to wash out the reflection produces exactly
        # this, with a clean, sharp, high-quality image.
        #
        # So neither. The capture is unusable, which is the same situation as
        # too few frames, and the student is told what to change and given a
        # new challenge. The consumed one is already spent, so this cannot be
        # retried into a pass.
        if (liveness is not None
                and challenge.challenge_type in liveness_challenge.ACTIVE_CHALLENGE_TYPES
                and liveness.challenge_passed is None):
            raise BusinessRuleError(
                liveness.inconclusive_reason
                or "The liveness challenge could not be measured. Request a "
                   "new challenge and try again.")

        face_match_passed = face_match_score = None
        wants_face_match = session.require_face_match or (
            course.require_face_recognition if course else False)
        if wants_face_match and liveness is not None:
            verification = self.faces.try_verify(student, frames[0])
            if verification is not None:
                face_match_passed = verification.match_passed
                face_match_score = verification.match_score
                if verification.current_template_hash:
                    face_embedding_hash = verification.current_template_hash

        if face_embedding_hash and len(face_embedding_hash) != 64:
            face_embedding_hash = None

        return (liveness_passed, liveness_score, challenge.challenge_type,
                face_match_passed, face_match_score, face_embedding_hash)

    @staticmethod
    def _network_flags(ip_address, user_agent, forwarded: bool) -> dict:
        """Detect VPN/proxy from the connection, never from the request body.

        A client-reported `vpn_detected` is worthless: its only effect is to
        raise the reporting client's own risk score, so nobody dishonest sets
        it. Implements API-SPECIFICATION.md Step 6 server-side.

        When the only address available is a private direct peer (the Docker
        gateway, every request in this deployment), there is no basis for a
        judgement, so all three flags stay False AND RiskService is told the
        signal is unknown - it then omits the network component entirely and
        renormalises, rather than recording a confident "no VPN".
        """
        if not network_signal_known(ip_address, forwarded=forwarded):
            return {"network_signal_known": False}
        is_vpn, confidence = detect_vpn_proxy(ip_address, user_agent, forwarded=forwarded)
        return {
            "network_signal_known": True,
            # The heuristic cannot tell a VPN from a proxy, so the stronger
            # label is not claimed; `tor_detected` needs an exit-node list we
            # do not have, and is left to a future IP-intelligence lookup.
            "vpn_detected": is_vpn and confidence >= 0.7,
            "proxy_detected": False,
            "tor_detected": False,
        }

    def _geo_history_flags(
        self, student: User, latitude, longitude, moment: datetime
    ) -> dict:
        """Where this student last was, for the impossible-travel speed test.

        The baseline is the most recent APPROVED positioned attempt, not simply
        the most recent one: see CheckInRepository.find_last_positioned_attempt
        for why a position the engine rejected cannot be used to accuse the
        next one, and what that deliberately gives up.

        Skipped entirely when the current submission has no coordinates: with
        nothing to compare against there is no speed to compute, and the
        missing fix is already charged full geo risk by RiskService._geo.
        """
        if latitude is None or longitude is None:
            return {}
        previous = self.checkins.find_last_positioned_attempt(student.id, moment)
        if previous is None:
            return {}
        return {
            "previous_latitude": previous.latitude,
            "previous_longitude": previous.longitude,
            "seconds_since_previous_fix":
                (moment - previous.attempted_at).total_seconds(),
        }

    @staticmethod
    def _ip_location_flags(ip_address, forwarded: bool) -> dict:
        """Apparent location of the source address, when it is knowable.

        Gated on ``forwarded`` for the same reason _network_flags is: without
        X-Forwarded-For the address is the container bridge we are talking to,
        not the student's. Locating the bridge and comparing it to a GPS fix
        would manufacture a contradiction out of our own deployment topology.

        Returns {} whenever the address cannot be located, which is the normal
        case here -- RiskService then omits the signal instead of recording
        that the two agreed. See services/ip_geolocation.py.
        """
        if not forwarded:
            return {}
        location = resolve_ip_location(ip_address)
        if location is None:
            return {}
        return {
            "ip_latitude": location.latitude,
            "ip_longitude": location.longitude,
            "ip_accuracy_radius_meters": location.accuracy_radius_meters,
        }

    # -- biometrics (module 3) ----------------------------------------------

    def _verify_biometrics(
        self, student: User, session: AttendanceSession, course,
        image: str, challenge_type: Optional[str] = None,
    ):
        """Call module 3 for liveness and, when required, a face match.

        Degrades rather than failing: if module 3 is unreachable the scores
        come back None, which RiskService treats as full risk, so the check-in
        is recorded as ``flagged`` for human review. Failing closed would lock
        a whole lecture theatre out of attendance because one container died;
        failing open would wave through exactly the submissions the service
        exists to catch.
        """
        challenge_type = challenge_type or DEFAULT_LIVENESS_CHALLENGE

        liveness = self.faces.try_liveness(image, challenge_type)
        liveness_passed = liveness.liveness_passed if liveness else None
        liveness_score = liveness.liveness_score if liveness else None
        # The hash comes from module 3, never from the client: it is the only
        # thing about the image that is allowed to be persisted.
        face_embedding_hash = liveness.face_embedding_hash if liveness else None

        face_match_passed = face_match_score = None
        wants_face_match = session.require_face_match or (
            course.require_face_recognition if course else False)
        if wants_face_match:
            verification = self.faces.try_verify(student, image)
            if verification is not None:
                face_match_passed = verification.match_passed
                face_match_score = verification.match_score
                if verification.current_template_hash:
                    face_embedding_hash = verification.current_template_hash

        if face_embedding_hash and len(face_embedding_hash) != 64:
            # CHECK (char_length = 64) on the column; drop rather than fail the
            # whole check-in over a malformed hash from a dependency.
            face_embedding_hash = None

        return (liveness_passed, liveness_score, challenge_type,
                face_match_passed, face_match_score, face_embedding_hash)

    @transactional
    def issue_liveness_challenge(
        self, student: User, session_id: str, now: Optional[datetime] = None,
    ):
        """Mint a challenge for this student and session.

        Runs the same gates as submit() first, so a student who is not
        enrolled, or whose window is shut, finds out before turning on their
        camera rather than after.
        """
        moment = now or utcnow()
        session = self._load_session(session_id)
        self._assert_can_check_in(student, session, moment, allow_retry=True)
        # A retry needs a fresh challenge, so an unfinished check-in must not
        # block one. A settled outcome still does.
        previous = self.checkins.find_by_session_and_student(session.id, student.id)
        if previous is not None and not self._is_retryable(previous):
            raise BusinessRuleError("You have already checked in to this session")
        if not student.camera_consent:
            raise BusinessRuleError("Camera consent is required for a liveness challenge")

        challenge = liveness_challenge.issue(student.id, session.id)
        self.challenges.put(challenge, liveness_challenge.CHALLENGE_TTL_SECONDS)
        self.audit.record(
            "liveness_challenge_issued", user_id=student.id,
            resource_type="session", resource_id=session.id,
            details={"challenge_type": challenge.challenge_type},
        )
        return challenge

    # -- gates --------------------------------------------------------------

    def issue_browser_challenge(self, student: User, session_id: str, fingerprint: str) -> dict:
        session = self._load_session(session_id)
        self._assert_can_check_in(student, session, utcnow(), allow_retry=True)
        previous = self.checkins.find_by_session_and_student(session.id, student.id)
        if previous is not None and not self._is_retryable(previous):
            raise BusinessRuleError("You have already checked in to this session")
        return BrowserVerificationService(self.session).issue_checkin(student, session.id, fingerprint)

    def _load_session(self, session_id: str) -> AttendanceSession:
        session = self.sessions.find_by_id(session_id)
        if session is None:
            raise NotFoundError(f"No session with id {session_id}")
        return session

    def _assert_can_check_in(
        self, student: User, session: AttendanceSession, moment: datetime,
        *, allow_retry: bool = False,
    ) -> None:
        """Cheap, certain refusals, before any expensive work."""
        if session.status != SessionStatus.ACTIVE:
            raise BusinessRuleError(f"Session is {session.status.value}, not active")
        if not (session.checkin_opens_at <= moment <= session.checkin_closes_at):
            raise BusinessRuleError("The check-in window for this session is closed")
        if not self.enrollments.is_enrolled(student.id, session.course_id):
            raise PermissionDeniedError("You are not enrolled in this course")
        # A tutorial / lab session is only for its own group.
        if session.group_link is not None and \
                session.group_link.group_id not in student_group_ids(self.session, student.id):
            raise PermissionDeniedError("This session is for a different tutorial or lab group")
        if not allow_retry and self.checkins.has_checked_in(session.id, student.id):
            # 400, not 409. API-SPECIFICATION.md lists "already checked in"
            # under 400 Bad Request with the other submission gates, and the
            # public test asserts 400. 409 is more conventional REST, but the
            # documented contract wins.
            #
            # allow_retry defers the decision to _resolve_attempt, which can
            # tell a retryable failure from a settled outcome.
            raise BusinessRuleError("You have already checked in to this session")

    @staticmethod
    def _is_retryable(checkin: CheckIn) -> bool:
        """True while the outcome is still unsettled.

        What settles a check-in is a PERSON, which is what reviewed_by_id
        records - not the `rejected` status by itself. The risk engine rejects
        automatically on a failed face match or a high score, and that is the
        ordinary failure a student should be able to simply try again: a dark
        room or a missed blink is not grounds for an appeal. Treating every
        rejection as final left the only real failure case with no way back,
        and the session list showing "Already Checked In" for good.

        An approved check-in is finished. An appeal has asked for human review,
        so retrying would undercut it. Anything a reviewer has touched is
        appealed rather than re-rolled - otherwise a student could overwrite an
        instructor's decision simply by submitting again.

        Retrying does not reopen the appeal window: ``appeal_deadline`` is set
        when the record is created and the retry path leaves it alone.
        """
        return (
            checkin.status not in (CheckInStatus.APPROVED, CheckInStatus.APPEALED)
            and checkin.reviewed_by_id is None
        )

    def _retry_signals(self, checkin: CheckIn) -> List:
        return [s for s in self.signals.find_by_checkin(checkin.id)
                if s.signal_type == RiskSignalType.RAPID_SUCCESSION]

    def _attempt_number(self, checkin: CheckIn) -> int:
        """How many attempts this check-in has had so far.

        Derived from the rapid-succession signals rather than a counter
        column, which keeps retries out of the schema entirely: one signal is
        written per retry, and a first attempt writes none.
        """
        return 1 + len(self._retry_signals(checkin))

    def _resolve_attempt(
        self, student: User, session: AttendanceSession,
        moment: datetime, retry: bool,
    ) -> Optional[CheckIn]:
        """Return the check-in being retried, or None for a first attempt.

        Attempts are not capped here and do NOT raise the risk score: a
        passing attempt is approved however many it took, and the attempt
        count is recorded as a rapid_succession signal plus a
        checkin_attempts row for after-the-fact review.

        Pacing is the rate limiter's job, not this method's. The published
        budget is 10 check-ins per minute per user (see
        docs/API-SPECIFICATION.md "Rate Limiting"), enforced in
        checkin_controller before the request reaches this service.
        """
        if not retry:
            return None
        previous = self.checkins.find_by_session_and_student(session.id, student.id, lock=True)
        if previous is None:
            # Nothing to retry; treat it as the first attempt rather than an
            # error, so a client that always sets the flag still works.
            return None
        if previous.status == CheckInStatus.APPROVED:
            raise BusinessRuleError(
                "This check-in was approved; there is nothing to retry")
        if not self._is_retryable(previous):
            raise BusinessRuleError(
                "This check-in has already been decided by staff; appeal it "
                "instead of retrying")
        return previous

    # -- decision -----------------------------------------------------------

    def _decide(
        self, assessment: RiskAssessment, ctx: CheckInContext,
        session: AttendanceSession, course,
    ) -> CheckInStatus:
        """The status table from the API specification.

            liveness failed              -> rejected
            GPS beyond 2x the geofence   -> rejected
            impossible travel            -> flagged
            risk >= threshold            -> flagged
            otherwise                    -> approved
        """
        # --- hard rejections (the spec's table) ---------------------------
        if ctx.require_liveness and ctx.liveness_passed is False:
            return CheckInStatus.REJECTED

        distance = assessment.distance_from_venue_meters
        radius = ctx.geofence_radius_meters
        if distance is not None and radius and distance > radius * HARD_REJECT_DISTANCE_MULTIPLIER:
            return CheckInStatus.REJECTED

        # --- a required check that did not pass cannot be APPROVED --------
        # The weighted score alone is not enough. A failed face match is only
        # 25% of the score, so a submission with everything else clean lands
        # around 0.25-0.40 and sails under a 0.5 threshold - meaning a session
        # that REQUIRES face matching would approve check-ins whose face match
        # explicitly failed, making the requirement decorative.
        #
        # Each component is judged against ITS OWN threshold, not against
        # risk_threshold: module 3 calibrates liveness at 0.60 and face match
        # at 0.70 and hands back the booleans, while risk_threshold is a policy
        # budget on a risk scale. Comparing a confidence score to a risk
        # threshold would also invert - lowering risk_threshold to tighten
        # policy would LOWER the confidence bar.
        #
        # `is not True` deliberately covers None as well as False: if a
        # required check could not be performed at all (module 3 down, or no
        # enrolled template) then it has not passed, and approving would claim
        # a verification that never happened.
        threshold = self._threshold(session, course)

        unmet = self._unmet_requirements(ctx, threshold)
        if unmet:
            return CheckInStatus.FLAGGED

        # --- a self-contradicting location claim cannot be APPROVED -------
        # Geolocation carries 15% of the score and the speed test can only
        # raise that component to 1.0, so on its own it moves the score by at
        # most 0.15-0.18 - never enough to cross a 0.5 threshold. Left to the
        # average, "this student was 40 km away eight seconds ago" would be
        # recorded as a signal on an APPROVED check-in that nobody ever looks
        # at, which is the same mistake the required-checks rule above fixes.
        #
        # Flagged rather than rejected, deliberately. A cached or stale fix
        # from the OS can manufacture a jump the student had no part in, and
        # the penalty for a false positive here is an attendance mark withheld
        # from someone who did attend. A human reads the two positions and the
        # elapsed time; the evidence is on the record either way.
        if any(s.signal_type == RiskSignalType.IMPOSSIBLE_TRAVEL
               for s in assessment.signals):
            return CheckInStatus.FLAGGED

        if assessment.risk_score >= threshold:
            return CheckInStatus.FLAGGED
        return CheckInStatus.APPROVED

    @staticmethod
    def _unmet_requirements(ctx: CheckInContext, threshold: float) -> List[str]:
        """Required verifications that did not demonstrably pass.

        Two bounds, both on the risk scale so there is nothing to invert:

          weighted mean of all component risks  < threshold   (overall budget)
          this component's own risk             < threshold   (no single
                                                               catastrophic
                                                               signal hidden by
                                                               averaging)

        A component's risk is ``1 - score``, so the second bound is equivalent
        to ``score > 1 - threshold``: tightening the threshold raises the
        confidence bar, which is the right direction. (Comparing a confidence
        directly against the threshold would invert it — a stricter policy
        would accept weaker biometrics.)

        The bar is the STRICTER of module 3's calibration and the policy bound.
        At the default threshold 0.5 the policy bound is 0.5, which is looser
        than module 3's 0.60 liveness calibration, so module 3's verdict rules;
        at threshold 0.3 the policy bound becomes 0.7 and takes over.

        Scoped to components the session actually REQUIRES. Applying the
        per-component bound to device, geolocation and network as well was
        measured and rejected: at threshold 0.5 it flags an unregistered device
        (risk 1.0) and a student who declined geolocation consent (risk 1.0),
        i.e. every first-ever check-in and every privacy-conscious student. The
        review queue would be the whole class, which is the same as having no
        queue. Those signals are better served by the weighted mean, where they
        contribute proportionally.
        """
        unmet: List[str] = []

        if ctx.require_liveness:
            if ctx.liveness_passed is not True:
                # Covers None: a check that could not run has not passed.
                unmet.append("liveness")
            elif ctx.liveness_challenge_type not in liveness_challenge.ACTIVE_CHALLENGE_TYPES:
                # A verdict the server did not challenge for does not satisfy a
                # session that requires liveness, whatever it says. Until this
                # existed, `require_liveness_check` was an instruction to a
                # cooperative client: our own frontend reads the flag and asks
                # for a challenge, but anything that skipped that call and
                # posted a single `image` got a passing passive verdict and an
                # APPROVED attendance record. The flag now binds the backend.
                #
                # Not a hard rejection: liveness was not FAILED, it was not
                # performed, and a 422 would turn away the submission without
                # recording the attempt. Flagged keeps the evidence and puts it
                # in front of a human.
                unmet.append("liveness")
            elif ctx.liveness_score is not None and (1.0 - ctx.liveness_score) >= threshold:
                unmet.append("liveness")

        if ctx.require_face_match:
            if ctx.face_match_passed is not True:
                unmet.append("face_match")
            elif ctx.face_match_score is not None and (1.0 - ctx.face_match_score) >= threshold:
                unmet.append("face_match")

        return unmet

    @staticmethod
    def _threshold(session: AttendanceSession, course) -> float:
        """Session overrides course; course defaults to 0.5."""
        if session.risk_threshold is not None:
            return session.risk_threshold
        if course is not None and course.risk_threshold is not None:
            return course.risk_threshold
        return 0.5

    def _build_context(self, session, course, device, **submitted) -> CheckInContext:
        """Resolve every session-overrides-course fallback in one place."""
        venue_lat = session.venue_latitude if session.venue_latitude is not None else (
            course.venue_latitude if course else None)
        venue_lon = session.venue_longitude if session.venue_longitude is not None else (
            course.venue_longitude if course else None)
        radius = session.geofence_radius_meters
        if radius is None:
            radius = course.geofence_radius_meters if course else 100.0

        return CheckInContext(
            venue_latitude=venue_lat,
            venue_longitude=venue_lon,
            geofence_radius_meters=radius,
            require_liveness=session.require_liveness_check,
            require_face_match=session.require_face_match or (
                course.require_face_recognition if course else False),
            require_device_binding=course.require_device_binding if course else True,
            device_known=device is not None,
            device_is_emulator=bool(device and device.is_emulator),
            device_is_rooted=bool(device and device.is_rooted_jailbroken),
            # `last_attestation_at` is the marker that a ceremony ran at all;
            # the verdict is only admissible once it has. See CheckInContext.
            attestation_known=bool(device and device.last_attestation_at is not None),
            attestation_passed=bool(device and device.attestation_passed),
            **submitted,
        )

    def _verify_qr(self, session, secret, moment) -> bool:
        import secrets as _secrets

        if not secret or not session.qr_code_secret:
            return False
        if session.qr_code_expires_at and session.qr_code_expires_at < moment:
            return False
        return _secrets.compare_digest(session.qr_code_secret, secret)

    @staticmethod
    def _encode_factors(assessment: RiskAssessment) -> Optional[str]:
        import json

        if not assessment.signals:
            return None
        return json.dumps([s.signal_type.value for s in assessment.signals])

    @staticmethod
    def _audit_action(status: CheckInStatus) -> AuditAction:
        return {
            CheckInStatus.APPROVED: AuditAction.CHECKIN_APPROVED,
            CheckInStatus.FLAGGED: AuditAction.CHECKIN_FLAGGED,
            CheckInStatus.REJECTED: AuditAction.CHECKIN_REJECTED,
        }.get(status, AuditAction.CHECKIN_ATTEMPTED)

    # -- review -------------------------------------------------------------

    def get(self, actor: User, checkin_id: str) -> CheckIn:
        checkin = self.checkins.find_with_signals(checkin_id)
        if checkin is None:
            raise NotFoundError(f"No check-in with id {checkin_id}")
        if checkin.student_id == actor.id:
            return checkin
        if actor.role not in _REVIEWERS:
            raise PermissionDeniedError("You may only view your own check-ins")
        require_session_visible(self.session, actor, self._load_session(checkin.session_id))
        return checkin

    def review_queue(self, actor: User, course_id: Optional[str] = None,
                     limit: int = 50, offset: int = 0) -> Page[CheckIn]:
        UserService.require_role(actor, *_REVIEWERS)
        return self.checkins.find_review_queue(
            course_id=course_id, limit=limit, offset=offset,
            session_ids=visible_session_ids(self.session, actor))

    def search_for(self, actor: User, **filters) -> Page[CheckIn]:
        """``GET /checkins/`` scoped to the caller's own sessions."""
        UserService.require_role(actor, *_REVIEWERS)
        return self.checkins.search(session_ids=visible_session_ids(self.session, actor),
                                    **filters)

    def for_session(self, actor: User, session_id: str) -> List[CheckIn]:
        """The attendance register for one session the caller is responsible for."""
        UserService.require_role(actor, *_REVIEWERS)
        require_session_visible(self.session, actor, self._load_session(session_id))
        return self.checkins.find_by_session(session_id)

    def my_checkins(self, student: User, limit: int = 50, offset: int = 0) -> Page[CheckIn]:
        return self.checkins.find_by_student(student.id, limit=limit, offset=offset)

    @transactional
    def review(
        self, actor: User, checkin_id: str, approve: bool,
        notes: Optional[str] = None,
    ) -> CheckIn:
        # Approving or rejecting (including appeals) is an instructor's
        # decision; TAs can see flagged check-ins but not resolve them.
        UserService.require_role(actor, UserRole.ADMIN, UserRole.INSTRUCTOR)
        checkin = self.checkins.lock_for_update(checkin_id)
        if checkin is None:
            raise NotFoundError(f"No check-in with id {checkin_id}")
        require_session_visible(self.session, actor, self._load_session(checkin.session_id))
        if approve:
            self.checkins.approve(checkin, reviewer_id=actor.id, notes=notes)
        else:
            self.checkins.reject(checkin, reviewer_id=actor.id, notes=notes)
        if checkin.device_id:
            device = self.devices.find_by_id(checkin.device_id)
            if device is not None:
                self.devices.refresh_trust(device)
        self.audit.record(
            AuditAction.CHECKIN_REVIEWED, user_id=actor.id, resource_type="checkin",
            resource_id=checkin.id,
            details={"decision": "approved" if approve else "rejected"},
        )
        return checkin

    @transactional
    def appeal(self, student: User, checkin_id: str, reason: str) -> CheckIn:
        checkin = self.checkins.lock_for_update(checkin_id)
        if checkin is None:
            raise NotFoundError(f"No check-in with id {checkin_id}")
        if checkin.student_id != student.id:
            raise PermissionDeniedError("You may only appeal your own check-ins")
        if checkin.status not in (CheckInStatus.FLAGGED, CheckInStatus.REJECTED):
            raise BusinessRuleError("Only flagged or rejected check-ins can be appealed")
        # One appeal per check-in: once an appeal has been decided, the
        # decision is final. Without this a rejected appeal could be re-filed
        # forever, since a rejected check-in is itself appealable.
        if checkin.appealed_at is not None:
            raise BusinessRuleError(
                "This check-in has already been appealed; the decision on it is final")
        deadline = checkin.appeal_deadline
        if deadline is not None and utcnow() > deadline:
            raise BusinessRuleError(
                f"The appeal window for this check-in closed on {deadline:%d %b %Y}")
        if not (reason or "").strip():
            raise BusinessRuleError("Give a reason for the appeal")
        self.checkins.appeal(checkin, reason.strip())
        self.audit.record(
            AuditAction.CHECKIN_APPEALED, user_id=student.id, resource_type="checkin",
            resource_id=checkin.id,
        )
        return checkin

    def statistics(self, session_id: str) -> Dict[str, int]:
        return self.checkins.count_by_status(session_id)
