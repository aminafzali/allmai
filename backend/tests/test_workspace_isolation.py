"""Cross-workspace isolation tests (Phase 0).

- App layer: tenant-aware helpers MUST fail loudly without workspace_id,
  and workspace-scoped filters must never return foreign rows (SQLite).
- DB layer: migration 0001 must contain FORCED RLS on every tenant table
  (verified statically here; a live-Postgres RLS test lands with Phase 2).
"""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.common.base import Base
from app.core.workspace import MissingWorkspaceContextError, require_workspace_id, scoped_filter
from app.knowledge.models import KnowledgeBase
from app.workspaces.models import Workspace

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "0001_initial.py"


def test_require_workspace_id_rejects_missing():
    with pytest.raises(MissingWorkspaceContextError):
        require_workspace_id(None)
    with pytest.raises(MissingWorkspaceContextError):
        require_workspace_id("   ")
    assert require_workspace_id("abc") == "abc"


def test_scoped_filter_needs_context():
    with pytest.raises(MissingWorkspaceContextError):
        scoped_filter(KnowledgeBase, None)


def _sqlite_db():
    # Import every model so metadata is complete.
    import app.models  # noqa: F401

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def test_app_layer_isolation_two_workspaces():
    """Same title in two workspaces: each context sees only its own rows."""
    engine = _sqlite_db()
    wa, wb = uuid.uuid4(), uuid.uuid4()
    with Session(engine) as db:
        db.add_all([
            Workspace(id=wa, name="A", type="personal", owner_user_id=uuid.uuid4()),
            Workspace(id=wb, name="B", type="personal", owner_user_id=uuid.uuid4()),
            KnowledgeBase(id=uuid.uuid4(), workspace_id=wa, title="Chemistry"),
            KnowledgeBase(id=uuid.uuid4(), workspace_id=wb, title="Chemistry"),
        ])
        db.commit()
        got_a = db.query(KnowledgeBase).filter(scoped_filter(KnowledgeBase, wa)).all()
        got_b = db.query(KnowledgeBase).filter(scoped_filter(KnowledgeBase, wb)).all()
        assert len(got_a) == 1 and got_a[0].workspace_id == wa
        assert len(got_b) == 1 and got_b[0].workspace_id == wb
        assert {r.id for r in got_a}.isdisjoint({r.id for r in got_b})


def test_migration_enforces_forced_rls():
    import re

    text = MIGRATION.read_text(encoding="utf-8")
    # The migration generates one policy per table in TENANT_TABLES via _enable_rls.
    m = re.search(r"TENANT_TABLES\s*=\s*\[(.*?)\]", text, re.S)
    assert m, "TENANT_TABLES list missing from migration"
    listed = set(re.findall(r'"([a-z_]+)"', m.group(1)))
    for table in [
        "knowledge_bases", "sources", "documents", "page_segments", "chunks",
        "concepts", "relations", "memory_facts", "conversation_summaries",
        "agents", "conversations", "messages", "workspace_members",
    ]:
        assert table in listed, f"missing RLS policy for {table}"
    assert '_enable_rls("workspaces"' in text
    assert "for table in TENANT_TABLES" in text
    assert "FORCE ROW LEVEL SECURITY" in text
    assert "app.workspace_id" in text
    assert "_workspace_isolation" in text
    # Lesson plans (migration 0003) are tenant data too: forced RLS required.
    mig3 = (BACKEND / "alembic" / "versions" / "0003_lesson_plans.py").read_text(encoding="utf-8")
    assert "lesson_plans_workspace_isolation" in mig3
    assert "FORCE ROW LEVEL SECURITY" in mig3
    # Writes (migration 0004): USING+WITH CHECK everywhere, open INSERT only on workspaces.
    mig4 = (BACKEND / "alembic" / "versions" / "0004_rls_write.py").read_text(encoding="utf-8")
    assert "WITH CHECK" in mig4
    assert "FOR INSERT WITH CHECK (true)" in mig4
    assert "lesson_plans" in mig4


def test_migration_0006_phase1_policies():
    """Phase 1 RLS: global rows readable only via definition assignment.

    - No blanket `workspace_id IS NULL` read grant: every global branch is
      AND-ed with an agent_definition_knowledge EXISTS on the REAL
      per-table join (no placeholders).
    - Writes unchanged (admin/owner context only).
    - agents gains owner visibility; agent_kb_assignments is tenant-gated.
    - knowledge_bases CHECK ck_kb_scope_ws; partial unique for personals.
    """
    mig6 = (BACKEND / "alembic" / "versions" / "0006_agent_definitions.py").read_text(encoding="utf-8")
    assert "app.definition_id" in mig6
    assert "ck_kb_scope_ws" in mig6
    assert "uq_agents_personal_one_per_user" in mig6
    assert "agent_kb_assignments_workspace_isolation" in mig6
    assert "agent_definition_knowledge" in mig6
    assert "<row_kb_id>" not in mig6
    # every knowledge table resolves its own kb (direct column or join)
    for table in ["knowledge_bases", "sources", "documents", "page_segments",
                  "chunks", "concepts", "relations"]:
        assert table in mig6
    assert "agent_definition_knowledge adk" in mig6
    # writes stay scoped: WITH CHECK has no definition/NULL branch
    assert "WITH CHECK (workspace_id = " in mig6
