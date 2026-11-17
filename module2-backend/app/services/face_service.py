"""Face enrolment and verification, backed by the Module 3 service.

Division of responsibility: module 3 does the computer vision and returns
hashes and scores; module 2 owns consent, persistence and the audit trail.
Module 3 is stateless with respect to users — it never learns who enrolled —
so the reference template hash lives here, on ``users.face_embedding_hash``.

PRIVACY: the base64 image is held only for the duration of the call. It is
never written to the database, the log, or the audit trail.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional, Sequence

from ..clients import (
    MAX_CHALLENGE_FRAMES,
    FaceClient,
    FaceImageRejected,
    FaceServiceUnavailable,
    FaceVerifyResult,
    LivenessResult,
)
from ..models import (
    AuditAction,
    FaceReEnrollRequest,
    FaceReEnrollStatus,
    User,
    UserRole,
)
from ..models.base import utcnow
from ..repositories import FaceReEnrollRepository, Page, UserRepository
from .audit_service import AuditService
from .base import BaseService, transactional
from .exceptions import (
    BusinessRuleError,
    NotFoundError,
    PermissionDeniedError,
    ServiceUnavailableError,
    ValidationError,
)
from .staff_scope import can_review_student, visible_student_ids
from .user_service import UserService

logger = logging.getLogger(__name__)

# Module 3 refuses a turned head at enrolment and reports the pose it
# measured. Checked again here because this module owns what gets written to
# users.face_embedding_hash: a face service that is older, misconfigured or
# swapped out must not be able to widen the gate silently. A template
# captured off-axis does not match the subject's own frontal face later, so
# a bad enrolment locks the student out rather than letting anyone in.
MAX_ENROLL_YAW = float(os.environ.get("FACE_MAX_ENROLL_YAW", "0.10"))


class FaceService(BaseService):
    def __init__(self, session, client: Optional[FaceClient] = None) -> None:
        super().__init__(session)
        self.users = UserRepository(session)
        self.reenroll_requests = FaceReEnrollRepository(session)
        self.audit = AuditService(session)
        # Injected in tests; constructed from FACE_SERVICE_URL otherwise.
        self.client = client or FaceClient()

    # -- enrolment ----------------------------------------------------------

    @transactional
    def enroll(
        self, user: User, image: str, *, ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> dict:
        """Enrol the user's face for the first time and store only the hash.

        Replacing an existing template goes through :meth:`reenroll`.
        """
        result = self._capture_template(
            user, image, AuditAction.FACE_ENROLLED,
            ip_address=ip_address, user_agent=user_agent)
        self._store_template(user, result)
        self.audit.record(
            AuditAction.FACE_ENROLLED, user_id=user.id, resource_type="user",
            resource_id=user.id, ip_address=ip_address, user_agent=user_agent,
            details=self._template_audit_details(result),
        )
        return {
            "success": True,
            "message": "Face enrolled successfully",
            "face_enrolled": True,
            "quality_score": result.quality_score,
        }

    @transactional
    def reenroll(
        self, user: User, image: str, *,
        ip_address: Optional[str] = None, user_agent: Optional[str] = None,
    ) -> dict:
        """Replace an already-enrolled template, spending a staff approval.

        Separate from :meth:`enroll` on purpose. A first enrolment and the
        destruction of a working template are different acts: this one needs an
        existing template to replace, and it lands in the audit trail under its
        own action so a re-enrolment just before a check-in is visible to
        review rather than indistinguishable from the original.

        Authority is an approved ``face_reenroll_requests`` row, not the
        caller's password -- see
        :class:`~app.schemas.face.FaceReEnrollSubmitRequest` for why that
        changed.

        The replacement is all-or-nothing: if module 3 rejects the new photo the
        old template is still in place, so a student cannot be left with no
        usable enrolment by a failed retry.
        """
        # Enrolment state first: it is the caller's own account, so there is
        # nothing to leak, and "you have nothing to replace" is a more useful
        # answer than "you need approval" to a student who has never enrolled.
        # It also avoids taking a row lock that could not be used anyway.
        if not user.face_enrolled or not user.face_embedding_hash:
            raise BusinessRuleError(
                "No face is enrolled yet; use the enrolment endpoint first")

        approval = self.reenroll_requests.find_spendable_for_user(user.id)
        if approval is None:
            raise PermissionDeniedError(
                "Re-enrolment needs an approved request. Ask an admin "
                "to approve one, then try again.")

        previous_hash = user.face_embedding_hash
        result = self._capture_template(
            user, image, AuditAction.FACE_RE_ENROLLED,
            ip_address=ip_address, user_agent=user_agent)
        self._store_template(user, result)

        # Spent only here, after module 3 has accepted the photo and in the
        # same transaction as the template write. A rejected capture raises
        # above this line, so it never burns the approval: the student can
        # retry without asking anyone again.
        approval.used_at = utcnow()
        self.session.flush()

        details = self._template_audit_details(result)
        # Whether the template actually changed, NOT the old hash itself. A
        # template hash is biometric data; writing it into an append-only audit
        # log would keep a copy that ``unenroll`` could never erase.
        details["template_changed"] = previous_hash != user.face_embedding_hash
        # Which decision authorised this, so the trail reads request ->
        # approval -> replacement.
        details["request_id"] = approval.id
        details["approved_by_id"] = approval.reviewed_by_id
        self.audit.record(
            AuditAction.FACE_RE_ENROLLED, user_id=user.id, resource_type="user",
            resource_id=user.id, ip_address=ip_address, user_agent=user_agent,
            details=details,
        )
        return {
            "success": True,
            "message": "Face re-enrolled successfully",
            "face_enrolled": True,
            "quality_score": result.quality_score,
            "previous_template_replaced": True,
        }

    # -- asking, and deciding ------------------------------------------------

    @transactional
    def request_reenroll(
        self, user: User, reason: Optional[str] = None, *,
        ip_address: Optional[str] = None, user_agent: Optional[str] = None,
    ) -> FaceReEnrollRequest:
        """Ask permission to replace the caller's own template.

        Refused when nothing is enrolled yet -- there would be nothing to
        replace, and a first :meth:`enroll` needs no approval. Refused too
        while the student already has a request pending or an approval they
        have not spent, so one student cannot fill a reviewer's queue with the
        same ask.
        """
        if not user.face_enrolled or not user.face_embedding_hash:
            raise BusinessRuleError(
                "No face is enrolled yet; use the enrolment endpoint first")
        if self.reenroll_requests.find_pending_for_user(user.id) is not None:
            raise BusinessRuleError(
                "You already have a re-enrolment request awaiting a decision")
        if self.reenroll_requests.find_spendable_for_user(user.id) is not None:
            raise BusinessRuleError(
                "Your re-enrolment is already approved; submit a new photo")

        row = self.reenroll_requests.save(FaceReEnrollRequest(
            user_id=user.id, reason=(reason or "").strip() or None))
        # Whether a reason was given, never the text: the audit log is
        # append-only and a student's free text does not belong in it.
        self.audit.record(
            AuditAction.FACE_RE_ENROLL_REQUESTED, user_id=user.id,
            resource_type="face_reenroll_request", resource_id=row.id,
            ip_address=ip_address, user_agent=user_agent,
            details={"has_reason": row.reason is not None},
        )
        return row

    @transactional
    def review_reenroll(
        self, actor: User, request_id: str, approve: bool,
        notes: Optional[str] = None, *,
        ip_address: Optional[str] = None, user_agent: Optional[str] = None,
    ) -> FaceReEnrollRequest:
        """Approve or deny one request.

        Admins only. Replacing the biometric reference every later check-in is
        matched against is an account-level decision, not a course-level one,
        and a student who is not enrolled on any course has no instructor who
        could ever see the request, so it would wait forever.

        A decision is final. Re-deciding an already-reviewed request is refused
        rather than allowed to flip: an approval cannot be withdrawn after the
        student has spent it, and a denial cannot be reopened without the
        student asking again.
        """
        UserService.require_role(actor, UserRole.ADMIN)
        row = self.reenroll_requests.find_by_id(request_id)
        if row is None:
            raise NotFoundError(f"No re-enrolment request with id {request_id}")
        if not can_review_student(self.session, actor, row.user_id):
            raise PermissionDeniedError("This student is not one of yours")
        if actor.id == row.user_id:
            # An admin with an enrolled face must not sign off replacing their
            # own template.
            raise PermissionDeniedError(
                "You cannot decide your own re-enrolment request")
        if row.status != FaceReEnrollStatus.PENDING:
            raise BusinessRuleError(f"This request was already {row.status.value}")

        row.status = (FaceReEnrollStatus.APPROVED if approve
                      else FaceReEnrollStatus.DENIED)
        row.reviewed_by_id = actor.id
        row.reviewed_at = utcnow()
        row.review_notes = (notes or "").strip() or None
        self.session.flush()
        self.audit.record(
            AuditAction.FACE_RE_ENROLL_APPROVED if approve
            else AuditAction.FACE_RE_ENROLL_DENIED,
            user_id=actor.id, resource_type="face_reenroll_request",
            resource_id=row.id, ip_address=ip_address, user_agent=user_agent,
            details={"student_id": row.user_id,
                     "decision": "approved" if approve else "denied"},
        )
        return row

    def my_reenroll_requests(
        self, user: User, limit: int = 50, offset: int = 0,
    ) -> Page[FaceReEnrollRequest]:
        """The caller's own requests, newest first."""
        return self.reenroll_requests.find_for_user(user.id, limit=limit, offset=offset)

    def reenroll_queue(
        self, actor: User, status: Optional[FaceReEnrollStatus] = None,
        limit: int = 50, offset: int = 0,
    ) -> Page[FaceReEnrollRequest]:
        """Every request awaiting or past a decision, oldest first. Admins only,
        matching :meth:`review_reenroll`: only the people who can decide a
        request need to see the queue.
        """
        UserService.require_role(actor, UserRole.ADMIN)
        return self.reenroll_requests.find_queue(
            student_ids=visible_student_ids(self.session, actor),
            status=status, limit=limit, offset=offset)

    # -- shared by enroll and reenroll --------------------------------------

    def _capture_template(
        self, user: User, image: str, action: AuditAction, *,
        ip_address: Optional[str], user_agent: Optional[str],
    ):
        """Send the image to module 3 and validate what comes back.

        Consent is checked here rather than relying on module 3's own check:
        the image should never leave this process if the user has not agreed to
        camera use.
        """
        if not user.camera_consent:
            raise BusinessRuleError(
                "Camera consent is required before enrolling a face")

        try:
            result = self.client.enroll(user.id, image, camera_consent=True)
        except FaceImageRejected as exc:
            # Module 3 looked at the image and said no. That is a 400 about the
            # submission, and its message is safe to pass through.
            self.audit.record_independently(
                action, user_id=user.id, resource_type="user",
                resource_id=user.id, ip_address=ip_address, user_agent=user_agent,
                details={"reason": exc.detail}, success=False,
            )
            raise ValidationError(exc.detail) from exc
        except FaceServiceUnavailable as exc:
            logger.error("face enrolment unavailable for user %s: %s", user.id, exc)
            raise ServiceUnavailableError(
                "The face recognition service is unavailable; please try again"
            ) from exc

        if not result.enrollment_successful or not result.face_template_hash:
            raise ValidationError("Face enrolment did not succeed; try another photo")

        # The column has CHECK (char_length = 64). Catching a malformed hash
        # here gives a clear error instead of an IntegrityError from the DDL.
        if len(result.face_template_hash) != 64:
            raise ServiceUnavailableError(
                "Face service returned a malformed template hash")

        self._assert_forward_facing(result, action, user,
                                    ip_address=ip_address, user_agent=user_agent)
        return result

    def _assert_forward_facing(
        self, result, action: AuditAction, user: User, *,
        ip_address: Optional[str], user_agent: Optional[str],
    ) -> None:
        """Refuse a template captured with the head turned away.

        A face service that does not report ``yaw`` is left to its own gate —
        there is nothing to check here, and failing closed would break
        enrolment entirely against an older module 3.
        """
        raw = result.details.get("yaw") if result.details else None
        if raw is None:
            return
        try:
            yaw = abs(float(raw))
        except (TypeError, ValueError):
            return
        if yaw <= MAX_ENROLL_YAW:
            return
        detail = (f"Head is turned too far from the camera to enrol "
                  f"(yaw {yaw:.2f} > {MAX_ENROLL_YAW}); look straight at the camera")
        self.audit.record_independently(
            action, user_id=user.id, resource_type="user", resource_id=user.id,
            ip_address=ip_address, user_agent=user_agent,
            details={"reason": detail, "yaw": yaw}, success=False,
        )
        raise ValidationError(detail)

    def _store_template(self, user: User, result) -> None:
        user.face_embedding_hash = result.face_template_hash
        user.face_enrolled = True
        user.updated_at = utcnow()
        self.session.flush()

    @staticmethod
    def _template_audit_details(result) -> dict:
        """Quality figures only. Never the template hash — see ``reenroll``."""
        details = {"quality_score": result.quality_score}
        for key in ("yaw", "detection_confidence"):
            if result.details and result.details.get(key) is not None:
                details[key] = result.details[key]
        return details

    @transactional
    def unenroll(self, user: User) -> User:
        """Forget the enrolled face.

        Also reachable by withdrawing camera consent — see
        ``AuthService.update_consent``.
        """
        user.face_embedding_hash = None
        user.face_enrolled = False
        user.updated_at = utcnow()
        self.session.flush()
        self.audit.record(
            "face_unenrolled", user_id=user.id, resource_type="user",
            resource_id=user.id,
        )
        return user

    # -- verification -------------------------------------------------------

    def verify(self, user: User, image: str) -> FaceVerifyResult:
        """Compare a fresh image against the user's enrolled template.

        Read-only, so it is not transactional.
        """
        if not user.face_enrolled or not user.face_embedding_hash:
            raise BusinessRuleError("This user has not enrolled a face yet")
        if not user.camera_consent:
            raise BusinessRuleError("Camera consent is required for face verification")

        try:
            return self.client.verify(image, user.face_embedding_hash)
        except FaceImageRejected as exc:
            raise ValidationError(exc.detail) from exc
        except FaceServiceUnavailable as exc:
            raise ServiceUnavailableError(
                "The face recognition service is unavailable") from exc

    def check_liveness(self, image: str, challenge_type: str = "passive") -> LivenessResult:
        try:
            return self.client.check_liveness(image, challenge_type)
        except FaceImageRejected as exc:
            raise ValidationError(exc.detail) from exc
        except FaceServiceUnavailable as exc:
            raise ServiceUnavailableError(
                "The face recognition service is unavailable") from exc

    def check_liveness_frames(
        self, frames: Sequence[str], challenge_type: str,
        colour_sequence: Optional[Sequence[str]] = None,
    ) -> LivenessResult:
        """Multi-frame active challenge (blink / head turn / colour).

        ``challenge_type`` and ``colour_sequence`` must both come from the
        server-issued challenge, never from the request body — see
        liveness_challenge.issue(). The colour sequence is what module 3
        checks the frames against, so a client-supplied one would let the
        caller mark its own homework.
        """
        if len(frames) > MAX_CHALLENGE_FRAMES:
            raise ValidationError(
                f"At most {MAX_CHALLENGE_FRAMES} frames are allowed")
        try:
            return self.client.check_liveness_frames(frames, challenge_type,
                                                     colour_sequence)
        except FaceImageRejected as exc:
            raise ValidationError(exc.detail) from exc
        except FaceServiceUnavailable as exc:
            raise ServiceUnavailableError(
                "The face recognition service is unavailable") from exc

    def try_liveness_frames(
        self, frames: Sequence[str], challenge_type: str,
        colour_sequence: Optional[Sequence[str]] = None,
    ) -> Optional[LivenessResult]:
        """Degrades to None on outage, like :meth:`try_liveness`."""
        try:
            return self.check_liveness_frames(frames, challenge_type,
                                              colour_sequence)
        except ServiceUnavailableError:
            logger.warning("multi-frame liveness skipped: face service unavailable")
            return None

    # -- degraded-mode helpers used by the check-in pipeline ----------------

    def try_liveness(self, image: str, challenge_type: str = "passive") -> Optional[LivenessResult]:
        """Liveness check that returns None instead of raising when module 3 is
        down.

        Used by check-in, which must not fail closed on a dependency outage:
        the risk engine already treats a missing liveness signal as full risk,
        so the submission lands as ``flagged`` for human review rather than
        locking the student out or waving them through.
        """
        try:
            return self.check_liveness(image, challenge_type)
        except ServiceUnavailableError:
            logger.warning("liveness check skipped: face service unavailable")
            return None

    def try_verify(self, user: User, image: str) -> Optional[FaceVerifyResult]:
        """Face match that degrades to None rather than raising.

        Returns None both when module 3 is down and when the user has no
        enrolled template — in either case there is nothing to compare against,
        which the risk engine scores as unverified.
        """
        if not user.face_enrolled or not user.face_embedding_hash:
            return None
        try:
            return self.verify(user, image)
        except ServiceUnavailableError:
            logger.warning("face match skipped: face service unavailable")
            return None

    def health(self) -> bool:
        return self.client.health()