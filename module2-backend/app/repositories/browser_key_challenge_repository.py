"""Challenge persistence and atomic redemption across API workers."""

from datetime import timedelta

from sqlalchemy import delete, exists, func, or_, select, update

from ..models.browser_key_challenge import BrowserKeyChallenge
from ..models.user import User
from .base import BaseRepository

ENROLLMENT_ATTEMPT_WINDOW = timedelta(minutes=15)
DB_NOW = func.timezone("UTC", func.clock_timestamp())


class BrowserKeyChallengeRepository(BaseRepository[BrowserKeyChallenge]):
    model = BrowserKeyChallenge

    def discard_expired(self) -> None:
        expired = (
            select(self.model.id)
            .where(self.model.expires_at <= DB_NOW, or_(
                self.model.used_at.is_(None),
                self.model.used_at <= DB_NOW - ENROLLMENT_ATTEMPT_WINDOW,
            ))
            .order_by(self.model.expires_at)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        self.session.execute(delete(self.model).where(self.model.id.in_(expired)))

    def consume(self, challenge: BrowserKeyChallenge) -> bool:
        current_account = exists().where(
            User.id == challenge.user_id,
            User.is_active.is_(True),
            User.token_version == challenge.token_version,
        )
        claim = (update(self.model).values(used_at=DB_NOW) if challenge.purpose == "enroll"
                 else delete(self.model))
        claimed = self.session.scalar(claim.where(
            self.model.id == challenge.id, self.model.expires_at > DB_NOW,
            self.model.used_at.is_(None), current_account,
        ).returning(self.model.id))
        return claimed is not None

    def enrollment_attempts(self, user_id: str) -> int:
        return self.session.scalar(select(func.count()).select_from(self.model).where(
            self.model.user_id == user_id, self.model.used_at > DB_NOW - ENROLLMENT_ATTEMPT_WINDOW,
        ))
