"""Live PostgreSQL RLS test: proves the database itself blocks leaks.

Writes two workspaces (+ one knowledge base each) to DATABASE_URL, then
asserts each `SET LOCAL app.workspace_id` context sees ONLY its own rows
— and that an empty context sees nothing. Cleans up after itself.

Skips gracefully when PostgreSQL is unreachable (CI without services).
Run with a live DB: pytest tests/test_rls_live.py
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


def test_rls_blocks_cross_workspace_reads():
    engine = _pg()
    wa, wb = uuid.uuid4(), uuid.uuid4()
    owner = uuid.uuid4()
    ka, kb = uuid.uuid4(), uuid.uuid4()
    try:
        with engine.begin() as c:
            c.execute(text("INSERT INTO users (id, email, password_hash, created_at) VALUES (:i, :e, 'x', NOW())"),
                      {"i": owner, "e": f"rls-{wa.hex[:8]}@x.com"})
            for wid in (wa, wb):
                c.execute(text("INSERT INTO workspaces (id, name, type, owner_user_id, created_at) "
                               "VALUES (:i, 'rls', 'shared', :o, NOW())"), {"i": wid, "o": owner})
            # RLS WITH CHECK requires a bound context even for setup writes.
            for kid, wid in ((ka, wa), (kb, wb)):
                c.execute(text("SELECT set_config('app.workspace_id', :v, true)"), {"v": str(wid)})
                c.execute(text("INSERT INTO knowledge_bases (id, workspace_id, title, created_at) "
                               "VALUES (:i, :w, 'KB', NOW())"), {"i": kid, "w": wid})
            c.execute(text("SELECT set_config('app.workspace_id', '', true)"))

        def visible_as(wid_or_none):
            with engine.connect() as c:
                c.execute(text("SELECT set_config('app.workspace_id', :v, true)"),
                          {"v": "" if wid_or_none is None else str(wid_or_none)})
                rows = c.execute(text("SELECT id FROM knowledge_bases")).fetchall()
                ws = c.execute(text("SELECT id FROM workspaces")).fetchall()
                c.rollback()
                return {str(r[0]) for r in rows}, {str(r[0]) for r in ws}

        got_a, ws_a = visible_as(wa)
        got_b, ws_b = visible_as(wb)
        got_none, ws_none = visible_as(None)

        assert got_a == {str(ka)} and ws_a == {str(wa)}
        assert got_b == {str(kb)} and ws_b == {str(wb)}
        assert got_none == set() and ws_none == set()
    finally:
        with engine.begin() as c:
            for table, has_ws, ids in (
                ("knowledge_bases", True, (ka, kb)),
                ("workspaces", False, (wa, wb)),
                ("users", False, (owner,)),
            ):                # cleanup: bind the owning context per row (RLS deletes are silent no-ops otherwise)
                for i in ids:
                    ctx = None if table == "users" else (wa if i in (ka, wa) else wb)
                    c.execute(text("SELECT set_config('app.workspace_id', :v, true)"),
                              {"v": "" if ctx is None else str(ctx)})
                    if table == "users":
                        c.execute(text("DELETE FROM users WHERE id = :i"), {"i": i})
                    elif has_ws:
                        w = wa if i in (ka, wa) else wb
                        c.execute(text(f"DELETE FROM {table} WHERE id = :i AND workspace_id = :w"),
                                  {"i": i, "w": w})
                    else:
                        c.execute(text(f"DELETE FROM {table} WHERE id = :i"), {"i": i})
            c.execute(text("SELECT set_config('app.workspace_id', '', true)"))
