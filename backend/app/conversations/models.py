"""Conversations + messages. Agent state lives in ``Conversation.state``
(JSONB) so agents (e.g. student coach) stay stateful across turns."""

import uuid

from sqlalchemy import Boolean, ForeignKey, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

MESSAGE_ROLES = ("user", "assistant", "system", "tool")


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    # AI-generated short title (user-editable) + user pin.
    title: Mapped[str] = mapped_column(String(200), default="")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class Message(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "messages"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[dict] = mapped_column(JSON, default=dict)
