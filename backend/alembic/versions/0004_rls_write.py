"""Fix RLS for writes: USING-only policies deny all INSERTs.

Revision ID: 0004_rls_write
Revises: 0003_lesson_plans

- workspaces: SELECT/UPDATE/DELETE scoped to current id; INSERT allowed
  (creation has no context yet; app-layer requires auth).
- every other tenant table: FOR ALL USING (...) WITH CHECK (...) so
  inserts/updates under a bound workspace context succeed while
  cross-tenant writes stay impossible.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_rls_write"
down_revision = "0003_lesson_plans"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "workspace_members",
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

CTX = "NULLIF(current_setting('app.workspace_id', true), '')::uuid"


def _drop_old(table: str) -> None:
    op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_workspace_isolation ON {table}"))


def upgrade() -> None:
    _drop_old("workspaces")
    for table in TENANT_TABLES:
        _drop_old(table)

    op.execute(sa.text(
        f"CREATE POLICY workspaces_select ON workspaces FOR SELECT USING (id = {CTX})"
    ))
    op.execute(sa.text(
        "CREATE POLICY workspaces_insert ON workspaces FOR INSERT WITH CHECK (true)"
    ))
    op.execute(sa.text(
        f"CREATE POLICY workspaces_update ON workspaces FOR UPDATE USING (id = {CTX}) WITH CHECK (id = {CTX})"
    ))
    op.execute(sa.text(
        f"CREATE POLICY workspaces_delete ON workspaces FOR DELETE USING (id = {CTX})"
    ))
    for table in TENANT_TABLES:
        op.execute(sa.text(
            f"CREATE POLICY {table}_workspace_isolation ON {table} FOR ALL "
            f"USING (workspace_id = {CTX}) WITH CHECK (workspace_id = {CTX})"
        ))


def downgrade() -> None:
    for pol in ("workspaces_select", "workspaces_insert", "workspaces_update", "workspaces_delete"):
        op.execute(sa.text(f"DROP POLICY IF EXISTS {pol} ON workspaces"))
    for table in TENANT_TABLES:
        op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_workspace_isolation ON {table}"))
        op.execute(sa.text(
            f"CREATE POLICY {table}_workspace_isolation ON {table} "
            f"USING (workspace_id = {CTX})"
        ))
    op.execute(sa.text(
        f"CREATE POLICY workspaces_workspace_isolation ON workspaces USING (id = {CTX})"
    ))
