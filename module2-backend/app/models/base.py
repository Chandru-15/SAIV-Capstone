"""Declarative base, shared column helpers and conventions.

The database schema is owned by ``db/schema.sql``; these models mirror it.
Keep the two in sync — ``tests/test_models_match_schema.py`` style checks are
the cheapest way to catch drift.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, Enum as SAEnum, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for every SAIV model."""


def utcnow() -> datetime:
    """Naive UTC timestamp.

    The schema stores TIMESTAMP WITHOUT TIME ZONE holding UTC, so every value
    written from Python must be naive. ``datetime.utcnow()`` is deprecated in
    3.12, hence the explicit conversion.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_uuid() -> str:
    """Primary keys are UUID strings in VARCHAR(36), not native UUID columns."""
    return str(uuid.uuid4())


# Server-side default matching the DDL, so rows inserted by raw SQL and rows
# inserted by the ORM get timestamps from the same clock.
NOW_UTC = text("(NOW() AT TIME ZONE 'utc')")


def enum_column(py_enum: type[Enum], length: int) -> SAEnum:
    """Map a Python enum onto a VARCHAR + CHECK column.

    ``native_enum=False``   -> VARCHAR(length), matching the DDL.
    ``create_constraint=False`` -> the CHECK lives in schema.sql; don't emit a
                               second, differently-named one.
    ``values_callable``     -> persist the member VALUE ('student'), not its
                               NAME ('STUDENT'), which is what SQLAlchemy would
                               otherwise store and what the CHECK would reject.
    """
    return SAEnum(
        py_enum,
        native_enum=False,
        create_constraint=False,
        length=length,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
    )


def pk_column() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True, default=new_uuid)


def created_at_column() -> Mapped[datetime]:
    return mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=NOW_UTC
    )


def updated_at_column() -> Mapped[datetime]:
    return mapped_column(
        DateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=NOW_UTC,
    )
