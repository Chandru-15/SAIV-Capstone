"""Generic repository, in the spirit of Spring Data's ``JpaRepository<T, ID>``.

Concrete repositories subclass ``BaseRepository`` and bind the entity type::

    class UserRepository(BaseRepository[User]):
        model = User

They then inherit the CRUD surface and add their own finders, the way a Spring
interface adds derived query methods on top of ``JpaRepository``.

Transactions: repositories never call ``commit()``. They ``flush()`` so that
generated values are populated and constraint violations surface at the right
line, but the decision to commit or roll back belongs to the caller — the
service layer, or the FastAPI dependency that owns the session. This is the
same split as Spring's ``@Transactional`` sitting on the service, not the
repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Iterable, List, Optional, Sequence, TypeVar

from sqlalchemy import Select, delete as sql_delete, func, select
from sqlalchemy.orm import Session

from ..models.base import Base

ModelT = TypeVar("ModelT", bound=Base)

# Matches the API specification's pagination defaults.
DEFAULT_LIMIT = 50
MAX_LIMIT = 100


class EntityNotFoundError(LookupError):
    """Raised by ``get_by_id`` when the row does not exist.

    The equivalent of ``findById(id).orElseThrow()``. Map this to a 404 in the
    router layer.
    """

    def __init__(self, entity: str, entity_id: Any) -> None:
        super().__init__(f"{entity} with id {entity_id!r} not found")
        self.entity = entity
        self.entity_id = entity_id


@dataclass(frozen=True)
class Page(Generic[ModelT]):
    """One page of results, shaped like the API's list envelope.

    ``{"items": [...], "total": 150, "limit": 50, "offset": 0}``
    """

    items: List[ModelT]
    total: int
    limit: int
    offset: int

    @property
    def has_next(self) -> bool:
        return self.offset + len(self.items) < self.total

    @property
    def has_previous(self) -> bool:
        return self.offset > 0


def clamp_limit(limit: Optional[int]) -> int:
    """The spec caps page size at 100; anything larger is a client mistake."""
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


class BaseRepository(Generic[ModelT]):
    """CRUD operations shared by every entity."""

    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self.session = session

    # -- create / update ----------------------------------------------------

    def save(self, entity: ModelT) -> ModelT:
        """Persist a new or modified entity (``JpaRepository.save``).

        Flushes so server-side defaults and constraint errors surface now, but
        does not commit.
        """
        self.session.add(entity)
        self.session.flush()
        return entity

    def save_all(self, entities: Iterable[ModelT]) -> List[ModelT]:
        batch = list(entities)
        self.session.add_all(batch)
        self.session.flush()
        return batch

    # -- read ---------------------------------------------------------------

    def find_by_id(self, entity_id: str) -> Optional[ModelT]:
        """``Optional<T> findById(ID id)``."""
        return self.session.get(self.model, entity_id)

    def get_by_id(self, entity_id: str) -> ModelT:
        """``findById(id).orElseThrow()`` — raises :class:`EntityNotFoundError`."""
        entity = self.find_by_id(entity_id)
        if entity is None:
            raise EntityNotFoundError(self.model.__name__, entity_id)
        return entity

    def find_all_by_id(self, entity_ids: Sequence[str]) -> List[ModelT]:
        if not entity_ids:
            return []
        pk = self._pk_column()
        return list(self.session.scalars(select(self.model).where(pk.in_(entity_ids))))

    def find_all(
        self, limit: Optional[int] = None, offset: int = 0
    ) -> List[ModelT]:
        stmt = select(self.model).offset(offset)
        if limit is not None:
            stmt = stmt.limit(clamp_limit(limit))
        return list(self.session.scalars(stmt))

    def find_all_paged(self, limit: int = DEFAULT_LIMIT, offset: int = 0) -> Page[ModelT]:
        return self.paginate(select(self.model), limit, offset)

    def exists_by_id(self, entity_id: str) -> bool:
        pk = self._pk_column()
        stmt = select(func.count()).select_from(self.model).where(pk == entity_id)
        return bool(self.session.scalar(stmt))

    def count(self) -> int:
        return int(self.session.scalar(select(func.count()).select_from(self.model)) or 0)

    # -- delete -------------------------------------------------------------

    def delete(self, entity: ModelT) -> None:
        self.session.delete(entity)
        self.session.flush()

    def delete_by_id(self, entity_id: str) -> bool:
        """Returns True if a row was removed, False if there was nothing to remove."""
        entity = self.find_by_id(entity_id)
        if entity is None:
            return False
        self.delete(entity)
        return True

    def delete_all(self) -> int:
        """Bulk delete. Bypasses ORM cascades — the database's ON DELETE rules
        still apply, but ``cascade="all, delete-orphan"`` does not run."""
        result = self.session.execute(sql_delete(self.model))
        self.session.flush()
        return result.rowcount or 0

    # -- helpers for subclasses --------------------------------------------

    def paginate(
        self, stmt: Select, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> Page[ModelT]:
        """Run a SELECT twice: once for the window, once for the total.

        Subclass finders build a filtered ``select()`` and hand it here rather
        than re-implementing counting and slicing.
        """
        limit = clamp_limit(limit)
        offset = max(0, offset)
        total = int(
            self.session.scalar(
                select(func.count()).select_from(stmt.order_by(None).subquery())
            )
            or 0
        )
        items = list(self.session.scalars(stmt.limit(limit).offset(offset)))
        return Page(items=items, total=total, limit=limit, offset=offset)

    def _pk_column(self):
        return self.model.__mapper__.primary_key[0]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} of {self.model.__name__}>"
