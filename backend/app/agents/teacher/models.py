"""LessonPlan rows: structured, validated plans as independent data.

The assistant Message keeps a JSON copy for chat rendering, but this table
is the queryable Source of Truth (list view, detail view, future edits).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class LessonPlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "lesson_plans"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kb_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    chapter: Mapped[str] = mapped_column(String(300))
    grade: Mapped[str] = mapped_column(String(100), default="")
    duration_minutes: Mapped[int] = mapped_column(Integer, default=45)
    teaching_style: Mapped[str] = mapped_column(String(200), default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
