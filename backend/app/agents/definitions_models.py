"""Agent Definitions (Phase 1): global, shared blueprints.

An Agent Definition is workspace-independent (no workspace_id, no RLS —
admin-gated like ai_settings). A workspace-scoped Agent Instance
(agents table) points at a Definition and carries per-workspace/per-user
customization + runtime state.

Canonical assignment between Definition and Global Knowledge lives ONLY
in AgentDefinitionKnowledge (many-to-many): one Global KB may serve
several Definitions. knowledge_bases MUST NOT gain a definition_id FK.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow


def _json_default():
    return {}


class AgentDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_definitions"

    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[str] = mapped_column(String(20), default="agent")
    instructions: Mapped[str] = mapped_column(Text, default="")
    behavior_rules: Mapped[dict] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), default=_json_default
    )
    methodology: Mapped[str] = mapped_column(Text, default="")
    capabilities: Mapped[dict] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), default=_json_default
    )
    tools: Mapped[dict] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), default=_json_default
    )
    workflow: Mapped[dict] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), default=_json_default
    )
    model_defaults: Mapped[dict] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), default=_json_default
    )
    safety_rules: Mapped[dict] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), default=_json_default
    )
    output_format: Mapped[dict] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), default=_json_default
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AgentDefinitionKnowledge(Base):
    """Canonical Definition <-> Global KB assignment (the ONLY such link).

    Invariant (enforced in definitions_service + tested): the referenced
    KB must satisfy scope='global' AND workspace_id IS NULL. Workspace KBs
    are rejected with 422. DB-level cross-table enforcement is intentionally
    NOT a trigger (all writes go through one admin-gated service function);
    see service docstring.
    """

    __tablename__ = "agent_definition_knowledge"

    definition_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_definitions.id", ondelete="CASCADE"), primary_key=True
    )
    kb_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class AgentKnowledgeAssignment(Base):
    """Instance-level link: workspace Agent Instance <-> workspace KB.

    Tenant table (workspace_id, RLS-isolated). Global KBs never appear here;
    they are inherited read-only from the Definition.
    """

    __tablename__ = "agent_kb_assignments"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    kb_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
