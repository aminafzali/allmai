"""RLS v3: identity-aware policies (live-run findings).

Revision ID: 0005_rls_identity
Revises: 0004_rls_write

Three session GUCs (set by the app, reset on release):
- app.workspace_id: claimed workspace (path id / created id)
- app.user_id:      authenticated user (JWT)
- app.is_admin:     'true' for global admins

- workspace_members: visible when it is YOUR row, YOUR workspace context,
  or you are admin. Same rule for writes.
- workspaces: visible when claimed, member (via subselect), or admin.
  INSERT stays open (creation precedes membership); UPDATE/DELETE scoped.
- all other tenant tables: claimed workspace OR admin.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_rls_identity"
down_revision = "0004_rls_write"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "knowledge_bases",
    "sources",
    "documents",
    "page_segments",
    "chunks",
    "concepts",
    "relations",
    "memory_facts",
    "conversation_summaries",
    "agents",
    "conversations",
    "messages",
    "lesson_plans",
]

WS = "NULLIF(current_setting('app.workspace_id', true), '')::uuid"
USR = "NULLIF(current_setting('app.user_id', true), '')::uuid"
ADM = "current_setting('app.is_admin', true) = 'true'"


def upgrade() -> None:
    for pol in ("workspaces_select", "workspaces_insert", "workspaces_update",
                "workspaces_delete", "workspaces_workspace_isolation"):
        op.execute(sa.text(f"DROP POLICY IF EXISTS {pol} ON workspaces"))
    op.execute(sa.text("DROP POLICY IF EXISTS workspace_members_workspace_isolation ON workspace_members"))
    for table in TENANT_TABLES:
        op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_workspace_isolation ON {table}"))

    op.execute(sa.text(
        f"CREATE POLICY workspace_members_workspace_isolation ON workspace_members FOR ALL "
        f"USING (workspace_id = {WS} OR user_id = {USR} OR {ADM}) "
        f"WITH CHECK (workspace_id = {WS} OR user_id = {USR} OR {ADM})"
    ))
    op.execute(sa.text(
        f"CREATE POLICY workspaces_select ON workspaces FOR SELECT USING "
        f"(id = {WS} OR id IN (SELECT workspace_id FROM workspace_members WHERE user_id = {USR}) OR {ADM})"
    ))
    op.execute(sa.text(
        "CREATE POLICY workspaces_insert ON workspaces FOR INSERT WITH CHECK (true)"
    ))
    op.execute(sa.text(
        f"CREATE POLICY workspaces_update ON workspaces FOR UPDATE "
        f"USING (id = {WS} OR {ADM}) WITH CHECK (id = {WS} OR {ADM})"
    ))
    op.execute(sa.text(
        f"CREATE POLICY workspaces_delete ON workspaces FOR DELETE USING (id = {WS} OR {ADM})"
    ))
    for table in TENANT_TABLES:
        op.execute(sa.text(
            f"CREATE POLICY {table}_workspace_isolation ON {table} FOR ALL "
            f"USING (workspace_id = {WS} OR {ADM}) WITH CHECK (workspace_id = {WS} OR {ADM})"
        ))


def downgrade() -> None:
    for pol in ("workspaces_select", "workspaces_insert", "workspaces_update", "workspaces_delete"):
        op.execute(sa.text(f"DROP POLICY IF EXISTS {pol} ON workspaces"))
    op.execute(sa.text("DROP POLICY IF EXISTS workspace_members_workspace_isolation ON workspace_members"))
    for table in TENANT_TABLES:
        op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_workspace_isolation ON {table}"))
    op.execute(sa.text(
        f"CREATE POLICY workspace_members_workspace_isolation ON workspace_members "
        f"USING (workspace_id = {WS})"
    ))
    op.execute(sa.text(
        f"CREATE POLICY workspaces_workspace_isolation ON workspaces USING (id = {WS})"
    ))
    for table in TENANT_TABLES:
        op.execute(sa.text(
            f"CREATE POLICY {table}_workspace_isolation ON {table} "
            f"USING (workspace_id = {WS}) WITH CHECK (workspace_id = {WS})"
        ))
