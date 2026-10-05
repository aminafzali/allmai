"""Internal usage ledger for future per-user/workspace billing (P3).

Revision ID: 0020_usage_events
Revises: 0019_avalai_transcription

- usage_events: one row per measured span (agent turn, retrieval, rerank,
  model call, extraction). Token counts + estimated USD cost are stored
  per row so future subscription pricing can aggregate per user or
  per workspace. No billing logic here — only measurement.
- workspace_id NULLABLE (system/global events, e.g. global-KB intake):
  readable by admins only. user_id NULLABLE (unattributed calls, e.g.
  raw search). History survives workspace/user deletion (SET NULL).
- RLS FORCED, same GUC pattern as 0004/0005: members read their own
  workspace rows; admins read everything. The recorder binds the right
  context per row on its own session.
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_usage_events"
down_revision = "0019_avalai_transcription"
branch_labels = None
depends_on = None

WS = "NULLIF(current_setting('app.workspace_id', true), '')::uuid"
ADM = "current_setting('app.is_admin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(),
                  sa.ForeignKey("workspaces.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("trace_id", sa.String(64), nullable=False, index=True,
                  server_default=""),
        sa.Column("span", sa.String(32), nullable=False, index=True,
                  server_default=""),
        sa.Column("provider", sa.String(32), nullable=False,
                  server_default=""),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("input_chars", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("error", sa.String(500), nullable=False, server_default=""),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("meta", sa.JSON(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_usage_ws_created", "usage_events",
                    ["workspace_id", "created_at"])
    op.create_index("ix_usage_user_created", "usage_events",
                    ["user_id", "created_at"])
    op.execute(sa.text("ALTER TABLE usage_events ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE usage_events FORCE ROW LEVEL SECURITY"))
    # Members read their own workspace rows; NULL-workspace (system)
    # rows are admin-only. Writes follow the same rule, and the
    # recorder binds an admin context for system rows.
    op.execute(sa.text(
        f"CREATE POLICY usage_events_isolation ON usage_events FOR ALL "
        f"USING (workspace_id = {WS} OR {ADM}) "
        f"WITH CHECK (workspace_id = {WS} OR {ADM})"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS usage_events_isolation ON usage_events"))
    op.execute(sa.text("ALTER TABLE usage_events DISABLE ROW LEVEL SECURITY"))
    op.drop_table("usage_events")
