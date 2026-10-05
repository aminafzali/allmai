"""Agent lead-mining table (workspace RLS, same pattern as 0003/0004).

Revision ID: 0012_agent_leads
Revises: 0011_note_excel

- leads: extracted business leads from maps_search (client-side
  providers). Workspace-scoped, FORCED RLS, FOR ALL ... WITH CHECK
  (post-0004 style, created directly in the final shape).
- No seed/tool changes: new search tools stay OFF by default; the
  admin enables them per agent definition in Agent Studio.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_agent_leads"
down_revision = "0011_note_excel"
branch_labels = None
depends_on = None

CTX = "NULLIF(current_setting('app.workspace_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("query", sa.Text(), server_default=""),
        sa.Column("name", sa.String(300), server_default=""),
        sa.Column("address", sa.Text(), server_default=""),
        sa.Column("phone", sa.String(100), server_default=""),
        sa.Column("hours", sa.String(300), server_default=""),
        sa.Column("website", sa.String(512), server_default=""),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("source", sa.String(32), server_default=""),
        sa.Column("raw", sa.JSON(), server_default="{}"),
        sa.Column("status", sa.String(20), server_default="new"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_leads_ws_query", "leads", ["workspace_id", "query"])
    op.create_index("ix_leads_ws_status", "leads", ["workspace_id", "status"])
    op.execute(sa.text("ALTER TABLE leads ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE leads FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(
        f"CREATE POLICY leads_workspace_isolation ON leads FOR ALL "
        f"USING (workspace_id = {CTX}) WITH CHECK (workspace_id = {CTX})"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS leads_workspace_isolation ON leads"))
    op.execute(sa.text("ALTER TABLE leads DISABLE ROW LEVEL SECURITY"))
    op.drop_table("leads")
