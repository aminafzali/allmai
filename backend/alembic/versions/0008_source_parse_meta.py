"""Persist parse/OCR/vision provenance on sources (stabilization).

Revision ID: 0008_source_parse_meta
Revises: 0007_definition_delete_restrict

Adds sources.parse_meta JSONB (server_default '{}') so the admin UX can
show per-document pipeline state (parser, OCR, vision counts) without
re-parsing. No RLS change (column on an already-gated table), no data
migration (default fills existing rows), backward compatible.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008_source_parse_meta"
down_revision = "0007_definition_delete_restrict"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("parse_meta", JSONB(), server_default="{}"))


def downgrade() -> None:
    op.drop_column("sources", "parse_meta")
