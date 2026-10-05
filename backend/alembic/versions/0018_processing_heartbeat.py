"""Stuck-task heartbeat for sources (watchdog support).

Revision ID: 0018_processing_heartbeat
Revises: 0017_normalize_fa

- sources.processing_started_at (nullable timestamptz): set on enqueue /
  task start, cleared on ready/failed. The lazy watchdog (knowledge
  service) marks rows stuck beyond INGEST_STALE_MINUTES as failed with a
  clear Persian message instead of an eternal "processing".
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_processing_heartbeat"
down_revision = "0017_normalize_fa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sources", sa.Column(
        "processing_started_at", sa.DateTime(timezone=True), nullable=True))
    # In-flight rows from before this migration: give them a heartbeat of
    # NOW so the watchdog judges them fairly (rather than failing them
    # instantly for having NULL).
    op.execute(sa.text(
        "UPDATE sources SET processing_started_at = NOW() "
        "WHERE status IN ('processing', 'pending') "
        "AND processing_started_at IS NULL"))


def downgrade() -> None:
    op.drop_column("sources", "processing_started_at")
