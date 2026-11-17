"""Shared response envelopes."""

from __future__ import annotations

from typing import Generic, List, Sequence, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResponse(BaseModel, Generic[T]):
    """The list envelope every collection endpoint returns.

    ``{"items": [...], "total": 150, "limit": 50, "offset": 0}``
    """

    items: List[T]
    total: int
    limit: int
    offset: int

    @classmethod
    def of(cls, page, items: Sequence[T]) -> "PageResponse[T]":
        return cls(items=list(items), total=page.total, limit=page.limit,
                   offset=page.offset)


class ErrorResponse(BaseModel):
    """Uniform error body produced by the ServiceError handler."""

    code: str = Field(examples=["not_found"])
    message: str
    details: object | None = None


class MessageResponse(BaseModel):
    message: str
