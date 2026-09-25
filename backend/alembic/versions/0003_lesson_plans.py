"""Phase audit: lesson_plans as structured, queryable data (tenant RLS).

Revision ID: 0003_lesson_plans
Revises: 0002_auth_aux
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_lesson_plans"
down_revision = "0002_auth_aux"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lesson_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kb_id", sa.Uuid(), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("chapter", sa.String(300), nullable=False),
        sa.Column("grade", sa.String(100), server_default=""),
        sa.Column("duration_minutes", sa.Integer(), server_default="45"),
        sa.Column("teaching_style", sa.String(200), server_default=""),
        sa.Column("instructions", sa.Text(), server_default=""),
        sa.Column("plan", sa.JSON(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(sa.text("ALTER TABLE lesson_plans ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE lesson_plans FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            "CREATE POLICY lesson_plans_workspace_isolation ON lesson_plans "
            "USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS lesson_plans_workspace_isolation ON lesson_plans"))
    op.execute(sa.text("ALTER TABLE lesson_plans NO ROW LEVEL SECURITY"))
    op.drop_table("lesson_plans")
