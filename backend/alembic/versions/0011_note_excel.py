"""Note-taking assistant + excel_query enablement.

Revision ID: 0011_note_excel
Revises: 0010_definition_prompts

1. Seeds the `note_taking_assistant` definition (answers from the user's
   files/notes/links; tools knowledge_search + memory_search + excel_query).
2. Appends `excel_query` to the tools of the two existing seeded
   definitions — only rows still holding the exact seed value are touched,
   so admin customizations are never overwritten.
3. Seeds the `agent.note_taking_assistant` model-defaults row (ON CONFLICT
   DO NOTHING, same pattern as 0002).
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_note_excel"
down_revision = "0010_definition_prompts"
branch_labels = None
depends_on = None

NOTE_SEED = (
    "INSERT INTO agent_definitions "
    "(id, key, title, type, instructions, tools, created_at, updated_at) "
    "VALUES (gen_random_uuid(), 'note_taking_assistant', "
    "'Note Taking Assistant', 'agent', "
    "'Answer in Persian from the user''s files, notes and links. "
    "Help organize study notes. Base every answer strictly on retrieved "
    "context; cite sources like [W1], [X1].', "
    "'{\"tools\": [\"knowledge_search\", \"memory_search\", \"excel_query\"]}', "
    "NOW(), NOW()) "
    "ON CONFLICT (key) DO NOTHING"
)


def upgrade() -> None:
    op.execute(sa.text(NOTE_SEED))
    # Existing seeds gain excel_query (seed values only, never admin edits).
    for key, old_tools in (
        ("teacher_lesson_planner", '{"tools": ["knowledge_search"]}'),
        ("student_academic_coach",
         '{"tools": ["knowledge_search", "memory_search"]}'),
    ):
        op.execute(
            sa.text(
                "UPDATE agent_definitions SET tools = "
                "jsonb_set(tools, '{tools}', "
                "(tools->'tools') || '\"excel_query\"'::jsonb), "
                "updated_at = NOW() WHERE key = :key "
                "AND tools::jsonb = CAST(:old AS jsonb)"
            ).bindparams(key=key, old=old_tools)
        )
    op.execute(
        sa.text(
            "INSERT INTO ai_settings (id, key, value, updated_at) "
            "VALUES ('66666666-6666-4666-8666-666666666666', "
            "'agent.note_taking_assistant', "
            "CAST('{\"model\": \"gemini-2.5-flash\"}' AS jsonb), NOW()) "
            "ON CONFLICT (key) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(sa.text(
        "DELETE FROM agent_definitions WHERE key = 'note_taking_assistant'"))
    op.execute(sa.text(
        "DELETE FROM ai_settings WHERE key = 'agent.note_taking_assistant'"))
    for key in ("teacher_lesson_planner", "student_academic_coach"):
        op.execute(
            sa.text(
                "UPDATE agent_definitions SET tools = "
                "(SELECT jsonb_build_object('tools', "
                "COALESCE((SELECT jsonb_agg(e) FROM jsonb_array_elements_text("
                "tools->'tools') AS e WHERE e <> 'excel_query'), '[]'::jsonb))), "
                "updated_at = NOW() WHERE key = :key "
                "AND (tools->'tools') ? 'excel_query'"
            ).bindparams(key=key)
        )
