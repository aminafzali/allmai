"""Data-extraction assistant definition (lead mining from the internet).

Revision ID: 0013_data_extraction
Revises: 0012_agent_leads

Seeds the `data_extraction_assistant` definition: answers by searching
the public internet IN THE USER'S BROWSER (web_search + maps_search
client tools, both default OFF everywhere else) and persists business
leads into the `leads` table. ON CONFLICT DO NOTHING everywhere, so
admin customizations are never overwritten.
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_data_extraction"
down_revision = "0012_agent_leads"
branch_labels = None
depends_on = None

DEF_SEED = (
    "INSERT INTO agent_definitions "
    "(id, key, title, type, instructions, tools, created_at, updated_at) "
    "VALUES (gen_random_uuid(), 'data_extraction_assistant', "
    "'Data Extraction Assistant', 'agent', "
    "'You extract structured data and business leads from the public "
    "internet. Use web_search for current facts and maps_search for "
    "places/businesses. Always answer in Persian, cite web sources like "
    "[S1] and places like [M1], and report how many leads were saved.', "
    "'{\"tools\": [\"web_search\", \"maps_search\"]}', "
    "NOW(), NOW()) "
    "ON CONFLICT (key) DO NOTHING"
)


def upgrade() -> None:
    op.execute(sa.text(DEF_SEED))
    op.execute(
        sa.text(
            "INSERT INTO ai_settings (id, key, value, updated_at) "
            "VALUES ('77777777-7777-4777-8777-777777777777', "
            "'agent.data_extraction_assistant', "
            "CAST('{\"model\": \"gemini-2.5-flash\"}' AS jsonb), NOW()) "
            "ON CONFLICT (key) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM ai_settings WHERE key = 'agent.data_extraction_assistant'"))
    op.execute(
        sa.text(
            "DELETE FROM agent_definitions WHERE key = 'data_extraction_assistant' "
            "AND NOT EXISTS (SELECT 1 FROM agents WHERE agents.definition_id = agent_definitions.id)"
        )
    )
