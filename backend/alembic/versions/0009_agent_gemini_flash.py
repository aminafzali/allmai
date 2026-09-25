"""Agent chat defaults to Gemini Flash (via GapGPT).

Revision ID: 0009_agent_gemini_flash
Revises: 0008_source_parse_meta

Only rows still holding the exact old seed value are updated — admin
customizations are never touched. Fresh installs are covered because
0002 seeds with ON CONFLICT DO NOTHING and this runs after it.
Live-verified: gemini-2.5-flash does chat + structured JSON on the
GapGPT OpenAI-compatible endpoint.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_agent_gemini_flash"
down_revision = "0008_source_parse_meta"
branch_labels = None
depends_on = None

OLD = '{"model": "gpt-4o-mini"}'
NEW = '{"model": "gemini-2.5-flash"}'
KEYS = ("agent.teacher_lesson_planner", "agent.student_academic_coach")


def upgrade() -> None:
    # NOTE: the value column is plain json (see 0002), so compare via ::jsonb.
    for key in KEYS:
        op.execute(
            sa.text(
                "UPDATE ai_settings SET value = CAST(:new AS json), "
                "updated_at = NOW() WHERE key = :key AND value::jsonb = CAST(:old AS jsonb)"
            ).bindparams(key=key, old=OLD, new=NEW)
        )


def downgrade() -> None:
    for key in KEYS:
        op.execute(
            sa.text(
                "UPDATE ai_settings SET value = CAST(:old AS json), "
                "updated_at = NOW() WHERE key = :key AND value::jsonb = CAST(:new AS jsonb)"
            ).bindparams(key=key, old=OLD, new=NEW)
        )
