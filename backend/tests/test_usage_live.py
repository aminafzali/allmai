"""Live PostgreSQL RLS test for the usage ledger (P3).

Proves: each workspace context sees ONLY its own usage rows, NULL
(system) rows are admin-only, and an empty context sees nothing.
Cleans up after itself. Skips gracefully without PostgreSQL.
"""

import uuid

import pytest
from sqlalchemy import create_engine, text

from app.core.config import get_settings


def _pg():
    url = get_settings().DATABASE_URL
    if not url.startswith("postgresql"):
        pytest.skip("not a postgres DATABASE_URL")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")
    return engine


def test_usage_events_workspace_isolation():
    engine = _pg()
    wa, wb = uuid.uuid4(), uuid.uuid4()
    owner = uuid.uuid4()
    ea, eb, esys = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        with engine.begin() as c:
            c.execute(text("SELECT set_config('app.is_admin', 'true', false)"))
            c.execute(text(
                "INSERT INTO users (id, email, password_hash, created_at) "
                "VALUES (:i, :e, 'x', NOW())"),
                {"i": owner, "e": f"usage-{wa.hex[:8]}@x.com"})
            for wid in (wa, wb):
                c.execute(text(
                    "INSERT INTO workspaces (id, name, type, owner_user_id, created_at) "
                    "VALUES (:i, 'usage', 'shared', :o, NOW())"),
                    {"i": wid, "o": owner})
            for eid, wid in ((ea, wa), (eb, wb), (esys, None)):
                c.execute(text(
                    "INSERT INTO usage_events (id, workspace_id, span, provider, "
                    "model, created_at) VALUES (:i, :w, 'model', 'openai_compat', "
                    "'gpt-4o-mini', NOW())"), {"i": eid, "w": wid})

        def visible_as(wid_or_none, admin=False):
            with engine.connect() as c:
                c.execute(text("SELECT set_config('app.workspace_id', :v, true)"),
                          {"v": "" if wid_or_none is None else str(wid_or_none)})
                c.execute(text("SELECT set_config('app.is_admin', :v, false)"),
                          {"v": "true" if admin else ""})
                rows = c.execute(text("SELECT id FROM usage_events")).fetchall()
                c.rollback()
                return {str(r[0]) for r in rows}

        # Membership properties (superset-safe: the live dev DB may
        # hold rows from earlier benchmark runs).
        got_a, got_b = visible_as(wa), visible_as(wb)
        assert str(ea) in got_a and str(eb) not in got_a
        assert str(esys) not in got_a  # system rows hidden from members
        assert str(eb) in got_b and str(ea) not in got_b
        assert str(esys) not in got_b
        got_none = visible_as(None)
        assert str(esys) not in got_none  # empty context sees no system rows
        assert str(ea) not in got_none and str(eb) not in got_none
        assert {str(ea), str(eb), str(esys)} <= visible_as(None, admin=True)
        assert {str(ea), str(eb), str(esys)} <= visible_as(wa, admin=True)
    finally:
        with engine.begin() as c:
            c.execute(text("SELECT set_config('app.is_admin', 'true', false)"))
            for eid in (ea, eb, esys):
                c.execute(text("DELETE FROM usage_events WHERE id = :i"), {"i": eid})
            for wid in (wa, wb):
                c.execute(text("DELETE FROM workspaces WHERE id = :i"), {"i": wid})
            c.execute(text("DELETE FROM users WHERE id = :i"), {"i": owner})
