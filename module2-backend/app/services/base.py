"""Service-layer foundation: the transaction boundary.

A service owns the unit of work. Repositories flush; services commit. The
:func:`transactional` decorator is the equivalent of Spring's
``@Transactional`` with the default REQUIRED propagation:

* the outermost decorated call commits on success and rolls back on any
  exception;
* a decorated service method calling another one *joins* the existing
  transaction rather than committing halfway through.

That nesting rule matters. Without it, a check-in that writes an audit entry
through another service would commit the audit row even when the check-in
itself later fails, leaving a trail of events that never happened.
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

from sqlalchemy.orm import Session

F = TypeVar("F", bound=Callable[..., Any])

_DEPTH_ATTR = "_saiv_tx_depth"


def transactional(method: F) -> F:
    """Commit when the outermost decorated call returns; roll back on error."""

    @wraps(method)
    def wrapper(self: "BaseService", *args: Any, **kwargs: Any) -> Any:
        session = self.session
        depth = getattr(session, _DEPTH_ATTR, 0)
        setattr(session, _DEPTH_ATTR, depth + 1)
        try:
            result = method(self, *args, **kwargs)
        except Exception:
            if depth == 0:
                session.rollback()
            raise
        else:
            if depth == 0:
                session.commit()
            return result
        finally:
            setattr(session, _DEPTH_ATTR, depth)

    return wrapper  # type: ignore[return-value]


class BaseService:
    """Holds the session every service and repository shares.

    One session per request means one transaction per request, so services
    composed together commit or roll back as a single unit.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    @property
    def in_transaction(self) -> bool:
        return getattr(self.session, _DEPTH_ATTR, 0) > 0
