"""Conversation titles (AI-generated, user-editable) + pins.

Revision ID: 0016_conversation_titles
Revises: 0015_extraction_prompts

- conversations.title (varchar 200, default ''): set once by the chat
  pipeline from the first user message (cheap LLM call, fail-open);
  editable via PATCH.
- conversations.pinned (bool, default false): user pin, listed first.
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_conversation_titles"
down_revision = "0015_extraction_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations",
                  sa.Column("title", sa.String(200), server_default=""))
    op.add_column("conversations",
                  sa.Column("pinned", sa.Boolean(), server_default="false"))
    op.create_index("ix_conversations_pinned", "conversations", ["pinned"])


def downgrade() -> None:
    op.drop_index("ix_conversations_pinned", table_name="conversations")
    op.drop_column("conversations", "pinned")
    op.drop_column("conversations", "title")
