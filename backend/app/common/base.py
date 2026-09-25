"""Shared SQLAlchemy base, mixins and portable column types.

pgvector ``VECTOR`` and Postgres ``TSVECTOR`` only exist on PostgreSQL.
To keep Phase-0 unit tests runnable without a live database (SQLite),
both are wrapped in portable ``TypeDecorator``s that compile to
``VECTOR``/``TSVECTOR`` on PostgreSQL and plain JSON/TEXT elsewhere.
"""

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import JSON, TEXT, DateTime
from sqlalchemy.dialects.postgresql import TSVECTOR as _PG_TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class EmbeddingVector(TypeDecorator):
    """Portable embedding column: VECTOR(n) on Postgres, JSON elsewhere."""

    impl = JSON
    cache_ok = True

    def __init__(self, dim: int = 1536) -> None:
        self.dim = dim
        super().__init__()

    def load_dialect_impl(self, dialect):  # type: ignore[override]
        if dialect.name == "postgresql":
            return dialect.type_descriptor(VECTOR(self.dim))
        return dialect.type_descriptor(JSON())


class SearchVector(TypeDecorator):
    """Portable FTS column: TSVECTOR on Postgres, TEXT elsewhere."""

    impl = TEXT
    cache_ok = True

    def load_dialect_impl(self, dialect):  # type: ignore[override]
        if dialect.name == "postgresql":
            return dialect.type_descriptor(_PG_TSVECTOR())
        return dialect.type_descriptor(TEXT())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def coerce_uuid(value: uuid.UUID | str):
    """Return a UUID object when parseable (portable across PG/SQLite).

    Always use this when comparing UUID columns to request/JWT strings.
    """
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return value


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
