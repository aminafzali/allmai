"""Phase 1: Agent Definitions + Global Knowledge + instance extensions.

Revision ID: 0006_agent_definitions
Revises: 0005_rls_identity

- New GLOBAL tables (no workspace_id, no RLS, admin-gated):
  agent_definitions, agent_definition_knowledge (canonical link).
- New TENANT table (RLS-isolated): agent_kb_assignments.
- agents += definition_id (SET NULL) + owner_user_id (RESTRICT, never
  SET NULL: deleting a user with personal instances fails) +
  custom_instructions + runtime_state; + indexes + partial unique
  (one personal instance per workspace/definition/user).
- knowledge_bases += scope + CHECK ck_kb_scope_ws
  (workspace->ws NOT NULL / global->ws IS NULL); workspace_id becomes
  nullable on the 7 knowledge tables for global rows.
- RLS: workspace rows unchanged. Global rows (workspace_id IS NULL)
  readable only when assigned to the current definition
  (app.definition_id GUC + agent_definition_knowledge EXISTS with the
  REAL per-table join — no placeholders). Writes unchanged (admin only).
- Seed: teacher_lesson_planner + student_academic_coach definitions.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006_agent_definitions"
down_revision = "0005_rls_identity"
branch_labels = None
depends_on = None

WS = "NULLIF(current_setting('app.workspace_id', true), '')::uuid"
USR = "NULLIF(current_setting('app.user_id', true), '')::uuid"
ND = "NULLIF(current_setting('app.definition_id', true), '')::uuid"
ADM = "current_setting('app.is_admin', true) = 'true'"

ASSIGN = (
    "EXISTS (SELECT 1 FROM agent_definition_knowledge adk "
    "WHERE adk.kb_id = {kb} AND adk.definition_id = " + ND + ")"
)

# Per-table: expression resolving THIS row's kb for the assignment check.
KB_OF = {
    "knowledge_bases": "knowledge_bases.id",
    "sources": "sources.kb_id",
    "documents": "(SELECT s.kb_id FROM sources s WHERE s.id = documents.source_id)",
    "page_segments": (
        "(SELECT s.kb_id FROM documents d "
        "JOIN sources s ON s.id = d.source_id "
        "WHERE d.id = page_segments.document_id)"
    ),
    "chunks": "chunks.kb_id",
    "concepts": "concepts.kb_id",
    "relations": (
        "(SELECT c.kb_id FROM concepts c "
        "WHERE c.id = relations.from_concept_id)"
    ),
}

GLOBAL_TABLES = list(KB_OF)


def _global_read(table: str) -> str:
    kb = KB_OF[table]
    return (
        f"workspace_id IS NULL AND EXISTS (SELECT 1 "
        f"FROM agent_definition_knowledge adk "
        f"WHERE adk.kb_id = {kb} AND adk.definition_id = {ND})"
    )


def upgrade() -> None:
    # --- new global tables ---
    op.create_table(
        "agent_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(100), unique=True, index=True, nullable=False),
        sa.Column("title", sa.String(200), server_default=""),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("type", sa.String(20), server_default="agent"),
        sa.Column("instructions", sa.Text(), server_default=""),
        sa.Column("behavior_rules", JSONB(), server_default="{}"),
        sa.Column("methodology", sa.Text(), server_default=""),
        sa.Column("capabilities", JSONB(), server_default="{}"),
        sa.Column("tools", JSONB(), server_default="{}"),
        sa.Column("workflow", JSONB(), server_default="{}"),
        sa.Column("model_defaults", JSONB(), server_default="{}"),
        sa.Column("safety_rules", JSONB(), server_default="{}"),
        sa.Column("output_format", JSONB(), server_default="{}"),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "agent_definition_knowledge",
        sa.Column("definition_id", sa.Uuid(),
                  sa.ForeignKey("agent_definitions.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("kb_id", sa.Uuid(),
                  sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "agent_kb_assignments",
        sa.Column("agent_id", sa.Uuid(),
                  sa.ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("kb_id", sa.Uuid(),
                  sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("workspace_id", sa.Uuid(),
                  sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- agents extensions ---
    op.add_column("agents", sa.Column("definition_id", sa.Uuid(),
                  sa.ForeignKey("agent_definitions.id", ondelete="SET NULL"),
                  nullable=True))
    op.add_column("agents", sa.Column("owner_user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"),
                  nullable=True))
    op.add_column("agents", sa.Column("custom_instructions", sa.Text(),
                                      server_default=""))
    op.add_column("agents", sa.Column("runtime_state", JSONB(), server_default="{}"))
    op.create_index("ix_agents_definition_id", "agents", ["definition_id"])
    op.create_index("ix_agents_owner_user_id", "agents", ["owner_user_id"])
    op.create_index("ix_agents_ws_owner", "agents", ["workspace_id", "owner_user_id"])
    op.create_index("ix_agents_ws_definition", "agents",
                    ["workspace_id", "definition_id"])
    op.execute(sa.text(
        "CREATE UNIQUE INDEX uq_agents_personal_one_per_user ON agents "
        "(workspace_id, definition_id, owner_user_id) "
        "WHERE owner_user_id IS NOT NULL"
    ))

    # --- knowledge_bases scope + nullable workspace chain ---
    op.add_column("knowledge_bases",
                  sa.Column("scope", sa.String(20), server_default="workspace"))
    for table in ("knowledge_bases", "sources", "documents", "page_segments",
                  "chunks", "concepts", "relations"):
        op.alter_column(table, "workspace_id", existing_type=sa.Uuid(),
                        nullable=True)
    op.execute(sa.text(
        "ALTER TABLE knowledge_bases ADD CONSTRAINT ck_kb_scope_ws CHECK "
        "((scope = 'workspace' AND workspace_id IS NOT NULL) OR "
        " (scope = 'global' AND workspace_id IS NULL))"
    ))

    # --- RLS rebuild: 7 knowledge tables get definition-gated global read ---
    for table in GLOBAL_TABLES:
        op.execute(sa.text(
            f"DROP POLICY IF EXISTS {table}_workspace_isolation ON {table}"))
    op.execute(sa.text("DROP POLICY IF EXISTS agents_workspace_isolation ON agents"))
    for table in GLOBAL_TABLES:
        op.execute(sa.text(
            f"CREATE POLICY {table}_workspace_isolation ON {table} FOR ALL "
            f"USING (workspace_id = {WS} OR {ADM} OR ({_global_read(table)})) "
            f"WITH CHECK (workspace_id = {WS} OR {ADM})"
        ))
    # agents: workspace gate + owner visibility (app-layer 404 is primary;
    # RLS is the second layer on live PG).
    op.execute(sa.text(
        f"CREATE POLICY agents_workspace_isolation ON agents FOR ALL "
        f"USING ((workspace_id = {WS} "
        f"AND (owner_user_id IS NULL OR owner_user_id = {USR})) OR {ADM}) "
        f"WITH CHECK (workspace_id = {WS} OR {ADM})"
    ))
    op.execute(sa.text(
        f"CREATE POLICY agent_kb_assignments_workspace_isolation "
        f"ON agent_kb_assignments FOR ALL "
        f"USING (workspace_id = {WS} OR {ADM}) "
        f"WITH CHECK (workspace_id = {WS} OR {ADM})"
    ))

    # --- seed the two existing catalog entries as definitions ---
    op.execute(sa.text(
        "INSERT INTO agent_definitions "
        "(id, key, title, type, instructions, tools, created_at, updated_at) "
        "VALUES (gen_random_uuid(), 'teacher_lesson_planner', "
        "'Teacher Lesson Planner', 'agent', "
        "'Build a professional lesson plan from a knowledge base and teacher request.', "
        "'{\"tools\": [\"knowledge_search\"]}', NOW(), NOW()) "
        "ON CONFLICT (key) DO NOTHING"
    ))
    op.execute(sa.text(
        "INSERT INTO agent_definitions "
        "(id, key, title, type, instructions, tools, created_at, updated_at) "
        "VALUES (gen_random_uuid(), 'student_academic_coach', "
        "'Student Academic Coach', 'agent', "
        "'Guide study planning, track progress and adjust plans using profile, memory and goals.', "
        "'{\"tools\": [\"knowledge_search\", \"memory_search\"]}', NOW(), NOW()) "
        "ON CONFLICT (key) DO NOTHING"
    ))


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM agent_definitions WHERE key IN "
                       "('teacher_lesson_planner', 'student_academic_coach')"))
    for table in GLOBAL_TABLES:
        op.execute(sa.text(
            f"DROP POLICY IF EXISTS {table}_workspace_isolation ON {table}"))
    op.execute(sa.text("DROP POLICY IF EXISTS agents_workspace_isolation ON agents"))
    op.execute(sa.text("DROP POLICY IF EXISTS "
                       "agent_kb_assignments_workspace_isolation "
                       "ON agent_kb_assignments"))
    for table in GLOBAL_TABLES:
        op.execute(sa.text(
            f"CREATE POLICY {table}_workspace_isolation ON {table} FOR ALL "
            f"USING (workspace_id = {WS} OR {ADM}) "
            f"WITH CHECK (workspace_id = {WS} OR {ADM})"
        ))
    op.execute(sa.text(
        f"CREATE POLICY agents_workspace_isolation ON agents FOR ALL "
        f"USING (workspace_id = {WS} OR {ADM}) "
        f"WITH CHECK (workspace_id = {WS} OR {ADM})"
    ))
    op.execute(sa.text("ALTER TABLE knowledge_bases DROP CONSTRAINT IF EXISTS "
                       "ck_kb_scope_ws"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_agents_personal_one_per_user"))
    op.drop_index("ix_agents_ws_definition", table_name="agents")
    op.drop_index("ix_agents_ws_owner", table_name="agents")
    op.drop_index("ix_agents_owner_user_id", table_name="agents")
    op.drop_index("ix_agents_definition_id", table_name="agents")
    op.drop_column("agents", "runtime_state")
    op.drop_column("agents", "custom_instructions")
    op.drop_column("agents", "owner_user_id")
    op.drop_column("agents", "definition_id")
    op.drop_column("knowledge_bases", "scope")
    op.drop_table("agent_kb_assignments")
    op.drop_table("agent_definition_knowledge")
    op.drop_table("agent_definitions")
