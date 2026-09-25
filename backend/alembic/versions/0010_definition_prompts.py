"""Agent Definition prompt templates (Agent Studio).

Revision ID: 0010_definition_prompts
Revises: 0009_agent_gemini_flash

Adds agent_definitions.prompt_templates JSONB (server_default '{}'):
per-definition prompt overrides (lesson_plan / coach_chat / study_plan).
Empty dict behaves exactly like before (built-in defaults); no data
migration, backward compatible.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010_definition_prompts"
down_revision = "0009_agent_gemini_flash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_definitions",
                  sa.Column("prompt_templates", JSONB(), server_default="{}"))


def downgrade() -> None:
    op.drop_column("agent_definitions", "prompt_templates")
