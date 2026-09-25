"""Agent Engine models. Generic: Assistant (simple) vs Agent (workflow,
state, tools). Concrete behaviours (teacher/student/...) are registry
entries + prompts, never hard-coded branches in the engine.

Phase 1: an Agent row is an *Instance* (workspace-scoped, optionally
user-scoped via owner_user_id) pointing at a global AgentDefinition.
Canonical per-instance runtime state lives in ``runtime_state``
(e.g. current_lesson/progress/plan/course); per-conversation/turn state
lives in ``conversations.state`` (see agents/state.py — never mix them).
``config`` is legacy: config["instructions"] is read-only fallback for
pre-Phase-1 rows; new writes go to ``custom_instructions``.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow

AGENT_TYPES = ("assistant", "agent")


class Agent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agents"

    __table_args__ = (
        Index("ix_agents_ws_owner", "workspace_id", "owner_user_id"),
        Index("ix_agents_ws_definition", "workspace_id", "definition_id"),
        # Locked: one personal instance per (workspace, definition, user).
        # Shared rows (owner NULL) are excluded. Portable part: SQLite
        # enforces partial unique indexes too; PG gets the same via 0006.
        Index("uq_agents_personal_one_per_user",
              "workspace_id", "definition_id", "owner_user_id",
              unique=True,
              sqlite_where=text("owner_user_id IS NOT NULL"),
              postgresql_where=text("owner_user_id IS NOT NULL")),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(100), index=True)
    type: Mapped[str] = mapped_column(String(20), default="assistant")
    name: Mapped[str] = mapped_column(String(200), default="")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    definition_id: Mapped[uuid.UUID | None] = mapped_column(
        # RESTRICT (locked, audit result A): deleting a definition with
        # dependent instances must fail — an instance must never silently
        # become definition_id=NULL (indistinguishable from genuine legacy,
        # wrongly activating the legacy memory fallback and dropping the
        # definition's instructions/tools/model). See 0007 + service 409.
        Uuid, ForeignKey("agent_definitions.id", ondelete="RESTRICT"),
        default=None, nullable=True, index=True,
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        # Personal instance owner. NULL = shared workspace instance
        # (legacy behaviour). RESTRICT (not SET NULL): deleting a user
        # with personal instances must fail until the instances are
        # resolved — a personal instance must never silently become shared.
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"),
        default=None, nullable=True, index=True,
    )
    custom_instructions: Mapped[str] = mapped_column(Text, default="")
    runtime_state: Mapped[dict] = mapped_column(JSON, default=dict)
