"""Engine, session factory and the FastAPI session dependency.

One session per request, which is what makes "one request = one transaction"
true. Services commit; this layer only guarantees the session is created and
closed exactly once.
"""

from __future__ import annotations

import os
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg2://saiv:saiv_password@localhost:5434/saiv"
)

# Pool sizing from DATABASE-SCHEMA.md. pool_pre_ping avoids handing out
# connections the database has already dropped, which otherwise surfaces as a
# random 500 after an idle period.
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session.

    ``expire_on_commit=False`` matters here: without it, every attribute of an
    object returned by a service would trigger a fresh SELECT after the commit,
    and serialising the response would fail once the session is closed.
    """
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
