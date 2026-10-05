"""Default transcription route -> AvalAI gpt-4o-mini-transcribe.

Revision ID: 0019_avalai_transcription
Revises: 0018_processing_heartbeat

Live-verified 200 on AvalAI /audio/transcriptions (json + text formats).
Only rows still holding the exact old seed value are updated — admin
customizations are never touched. The GapGPT whisper-1 route is NOT
removed: setting the row back to whisper-1 (from /admin/ai-settings)
restores it, since the route is selected purely by model id
(see app/knowledge/parsers/audio.py).
"""

import sqlalchemy as sa
from alembic import op

revision = "0019_avalai_transcription"
down_revision = "0018_processing_heartbeat"
branch_labels = None
depends_on = None

KEY = "audio.transcription"
OLD = '{"provider": "openai_compat", "model": "whisper-1"}'
NEW = '{"provider": "openai_compat", "model": "gpt-4o-mini-transcribe"}'


def upgrade() -> None:
    # NOTE: the value column is plain json (see 0002), so compare via ::jsonb.
    op.execute(
        sa.text(
            "UPDATE ai_settings SET value = CAST(:new AS json), "
            "updated_at = NOW() WHERE key = :key AND value::jsonb = CAST(:old AS jsonb)"
        ).bindparams(key=KEY, old=OLD, new=NEW)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE ai_settings SET value = CAST(:old AS json), "
            "updated_at = NOW() WHERE key = :key AND value::jsonb = CAST(:new AS jsonb)"
        ).bindparams(key=KEY, old=OLD, new=NEW)
    )
