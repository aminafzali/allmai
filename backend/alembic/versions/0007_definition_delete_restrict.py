"""Phase 1 fix (audit result A): Definition deletion must not orphan instances.

Revision ID: 0007_definition_delete_restrict
Revises: 0006_agent_definitions

Problem: agents.definition_id was ON DELETE SET NULL, so deleting a
Definition silently turned its instances into definition_id=NULL rows —
indistinguishable from genuine legacy agents — which wrongly activated
the legacy memory fallback (agent:{key}), dropped the Definition's
instructions/tools/model_defaults, and cut assigned global retrieval.

Fix: ON DELETE RESTRICT. Deletion of a Definition with dependent
instances is rejected (service layer returns 409 first; the FK is the
second layer). Genuine legacy agents (definition_id NULL from birth)
are untouched. agent_definition_knowledge stays ON DELETE CASCADE.
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_definition_delete_restrict"
down_revision = "0006_agent_definitions"
branch_labels = None
depends_on = None

CONSTRAINT = "agents_definition_id_fkey"


def upgrade() -> None:
    conn = op.get_bind()
    orphans = conn.execute(sa.text(
        "SELECT count(*) FROM agents a "
        "WHERE a.definition_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM agent_definitions d WHERE d.id = a.definition_id)"
    )).scalar()
    if orphans:
        raise RuntimeError(
            f"migration 0007 refused: {orphans} agent(s) reference "
            f"missing definitions; resolve them before upgrading"
        )
    op.execute(sa.text(
        f"ALTER TABLE agents DROP CONSTRAINT IF EXISTS {CONSTRAINT}"
    ))
    op.execute(sa.text(
        f"ALTER TABLE agents ADD CONSTRAINT {CONSTRAINT} "
        f"FOREIGN KEY (definition_id) REFERENCES agent_definitions(id) "
        f"ON DELETE RESTRICT"
    ))


def downgrade() -> None:
    op.execute(sa.text(
        f"ALTER TABLE agents DROP CONSTRAINT IF EXISTS {CONSTRAINT}"
    ))
    op.execute(sa.text(
        f"ALTER TABLE agents ADD CONSTRAINT {CONSTRAINT} "
        f"FOREIGN KEY (definition_id) REFERENCES agent_definitions(id) "
        f"ON DELETE SET NULL"
    ))
