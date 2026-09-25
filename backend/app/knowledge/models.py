"""Knowledge Engine models — the system's own Source of Truth.

Chain: KnowledgeBase -> Source -> Document -> PageSegment -> Chunk
(+ Concept / Relation for entities). Raw files live in object storage;
PostgreSQL keeps metadata + structure + embeddings.

Supported MVP source types (video is reserved for the future and is
rejected by the ingestion worker, see parsers/base.py):
pdf, docx, pptx, txt, md, image, audio, url, note
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base import (
    Base,
    EmbeddingVector,
    SearchVector,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    utcnow,
)

# MVP source types. "video" is intentionally NOT listed: the worker rejects it.
SOURCE_TYPES = ("pdf", "docx", "pptx", "txt", "md", "image", "audio", "url", "note")
AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a")
SOURCE_STATUSES = ("pending", "processing", "ready", "failed")


class KnowledgeBase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        CheckConstraint(
            "(scope = 'workspace' AND workspace_id IS NOT NULL) OR "
            "(scope = 'global' AND workspace_id IS NULL)",
            name="ck_kb_scope_ws",
        ),
    )

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        # NULLABLE since Phase 1: global KBs have workspace_id IS NULL
        # + scope='global'. Enforced by CHECK ck_kb_scope_ws (migration 0006):
        #   scope='workspace' -> workspace_id IS NOT NULL
        #   scope='global'    -> workspace_id IS NULL
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[str] = mapped_column(String(20), default="workspace")

KB_SCOPES = ("workspace", "global")


class Source(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sources"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    kb_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(20))  # one of SOURCE_TYPES
    storage_key: Mapped[str] = mapped_column(String(1024), default="")
    filename: Mapped[str] = mapped_column(String(512), default="")
    mime: Mapped[str] = mapped_column(String(128), default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[str] = mapped_column(Text, default="")
    # Phase 3 stabilization: parse/OCR/vision provenance per source
    # ({parser, ocr_*, vision counts...}). Read-only UX + debugging.
    parse_meta: Mapped[dict] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), default=dict
    )


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(500), default="")
    page_count: Mapped[int] = mapped_column(default=0)
    duration_ms: Mapped[int] = mapped_column(BigInteger, default=0)


class PageSegment(UUIDPrimaryKeyMixin, Base):
    """A page (documents) or time slice (audio) of a document."""

    __tablename__ = "page_segments"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page_no: Mapped[int | None] = mapped_column(default=None)
    start_ms: Mapped[int | None] = mapped_column(BigInteger, default=None)
    end_ms: Mapped[int | None] = mapped_column(BigInteger, default=None)
    text: Mapped[str] = mapped_column(Text, default="")


class Chunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chunks"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    kb_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    segment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("page_segments.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    tokens: Mapped[int] = mapped_column(default=0)
    embedding: Mapped[list] = mapped_column(EmbeddingVector(dim=1536))
    content_tsv: Mapped[str] = mapped_column(SearchVector(), default="")
    chunk_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB().with_variant(JSON(), "sqlite"), default=dict
    )

    __table_args__ = (
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_chunks_content_tsv",
            "content_tsv",
            postgresql_using="gin",
        ),
    )


class Concept(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "concepts"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    kb_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(300), index=True)
    type: Mapped[str] = mapped_column(String(100), default="entity")


class Relation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "relations"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    from_concept_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("concepts.id", ondelete="CASCADE")
    )
    to_concept_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("concepts.id", ondelete="CASCADE")
    )
    label: Mapped[str] = mapped_column(String(200), default="related_to")
