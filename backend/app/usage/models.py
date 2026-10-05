"""Usage event rows. Workspace/user attribution is NULLABLE so system
events (global-KB intake, unattributed searches) still record; history
survives workspace/user deletion (SET NULL, unlike operational rows).
Reads are RLS-gated in the API (members: own workspace; admin: all).
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base import Base, UUIDPrimaryKeyMixin, utcnow

# Span names recorded across the system (agent turn, retrieval, rerank,
# model call, extraction). New spans must reuse these constants.
SPAN_AGENT = "agent"
SPAN_RETRIEVAL = "retrieval"
SPAN_RERANK = "rerank"
SPAN_MODEL = "model"
SPAN_EXTRACTION = "extraction"

SPANS = (SPAN_AGENT, SPAN_RETRIEVAL, SPAN_RERANK, SPAN_MODEL, SPAN_EXTRACTION)


class UsageEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "usage_events"

    __table_args__ = (
        Index("ix_usage_ws_created", "workspace_id", "created_at"),
        Index("ix_usage_user_created", "user_id", "created_at"),
    )

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="SET NULL"),
        default=None, nullable=True, index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"),
        default=None, nullable=True, index=True,
    )
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    span: Mapped[str] = mapped_column(String(32), default="", index=True)
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    calls: Mapped[int] = mapped_column(Integer, default=1)
    input_chars: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float | None] = mapped_column(Float, default=None, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(String(500), default="")
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), default=None, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
