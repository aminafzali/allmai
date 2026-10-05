"""Extracted business leads (lead-mining output of maps_search).

Workspace-scoped like every other tenant row: RLS
``leads_workspace_isolation`` (migration 0012) + app-layer workspace
resolution. ``raw`` keeps the full provider payload for re-export;
``status`` is a soft workflow flag (new/contacted/archived).
"""

import uuid

from sqlalchemy import JSON, Float, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

LEAD_STATUSES = ("new", "contacted", "archived")


class Lead(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "leads"

    __table_args__ = (
        Index("ix_leads_ws_query", "workspace_id", "query"),
        Index("ix_leads_ws_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="CASCADE"),
        default=None, nullable=True, index=True,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"),
        default=None, nullable=True, index=True,
    )
    query: Mapped[str] = mapped_column(Text, default="")
    name: Mapped[str] = mapped_column(String(300), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    phone: Mapped[str] = mapped_column(String(100), default="")
    hours: Mapped[str] = mapped_column(String(300), default="")
    website: Mapped[str] = mapped_column(String(512), default="")
    lat: Mapped[float | None] = mapped_column(Float, default=None, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, default=None, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="")
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="new")
