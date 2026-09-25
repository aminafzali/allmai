"""Phase 1: refresh_tokens + audit_logs + ai_settings (+cheap-model seeds).

Revision ID: 0002_auth_aux
Revises: 0001_initial

These three tables are intentionally GLOBAL (no RLS):
- refresh_tokens: looked up by token hash, user match checked app-layer.
- audit_logs: admin-only reads, enforced app-layer (require_admin).
- ai_settings: platform model configuration, admin-only writes.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_auth_aux"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

SEEDS = [
    (
        "11111111-1111-4111-8111-111111111111",
        "chat.default",
        '{"provider": "openai_compat", "model": "gpt-4o-mini", '
        '"temperature": 0.7, "max_tokens": 1500}',
    ),
    (
        "22222222-2222-4222-8222-222222222222",
        "embedding.default",
        '{"provider": "openai_compat", "model": "text-embedding-3-small", "dim": 1536}',
    ),
    (
        "33333333-3333-4333-8333-333333333333",
        "audio.transcription",
        '{"provider": "openai_compat", "model": "whisper-1"}',
    ),
    (
        "44444444-4444-4444-8444-444444444444",
        "agent.teacher_lesson_planner",
        '{"model": "gpt-4o-mini"}',
    ),
    (
        "55555555-5555-4555-8555-555555555555",
        "agent.student_academic_coach",
        '{"model": "gpt-4o-mini"}',
    ),
]


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("token_hash", sa.String(128), unique=True, index=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("action", sa.String(100), nullable=False, index=True),
        sa.Column("entity", sa.String(100), server_default=""),
        sa.Column("entity_id", sa.String(100), server_default=""),
        sa.Column("meta", sa.JSON(), server_default="{}"),
        sa.Column("ip", sa.String(64), server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "ai_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(100), unique=True, index=True, nullable=False),
        sa.Column("value", sa.JSON(), server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    for seed_id, key, value_json in SEEDS:
        op.execute(
            sa.text(
                "INSERT INTO ai_settings (id, key, value, updated_at) "
                "VALUES (:id, :key, CAST(:value AS jsonb), NOW()) "
                "ON CONFLICT (key) DO NOTHING"
            ).bindparams(id=seed_id, key=key, value=value_json)
        )


def downgrade() -> None:
    op.drop_table("ai_settings")
    op.drop_table("audit_logs")
    op.drop_table("refresh_tokens")
