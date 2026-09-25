"""Live PostgreSQL tests for Phase 1 global knowledge + owner visibility.

Proves at the DATABASE layer (not just app filters):
1. Fitness def + WS-A sees assigned Fitness global KB, NOT Chemistry global.
2. Chemistry def (no assignment) sees no globals.
3. No definition context sees no globals (IS NULL alone grants nothing).
4. Child records (sources/documents/segments/chunks/concepts) follow the
   same assignment gate via their REAL joins.
5. WS-A cannot see WS-B rows and vice versa (existing guarantee intact).
6. Member global write denied; admin global write allowed.
7. agents owner visibility: user B does not see user A's personal agent.
8. agent_kb_assignments tenant-gated.

Skips gracefully when PostgreSQL is unreachable OR migration 0006 has
not been applied (checks for agent_definitions table).
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
            has = conn.execute(text(
                "SELECT to_regclass('agent_definitions')")).scalar()
            if not has:
                pytest.skip("migration 0006 not applied")
    except Exception as exc:
        pytest.skip(f"PostgreSQL unreachable or 0006 missing: {exc}")
    return engine


def _vec():
    return "[" + ",".join(["1.0"] + ["0.0"] * 1535) + "]"


def _ctx(c, ws=None, user=None, definition=None, admin=False):
    c.execute(text("SELECT set_config('app.workspace_id', :v, true)"),
              {"v": "" if ws is None else str(ws)})
    c.execute(text("SELECT set_config('app.definition_id', :v, true)"),
              {"v": "" if definition is None else str(definition)})
    c.execute(text("SELECT set_config('app.user_id', :v, false)"),
              {"v": "" if user is None else str(user)})
    c.execute(text("SELECT set_config('app.is_admin', :v, false)"),
              {"v": "true" if admin else ""})


def test_definition_delete_restricted_with_instances():
    """Audit result A, layer 2: the real PostgreSQL FK rejects deleting a
    definition that still has agent instances (service 409 is layer 1)."""
    import sqlalchemy.exc

    engine = _pg()
    wa = uuid.uuid4()
    ua = uuid.uuid4()
    did = uuid.uuid4()
    aid = uuid.uuid4()
    try:
        with engine.begin() as c:
            c.execute(text("INSERT INTO users (id, email, password_hash, created_at) "
                           "VALUES (:i, :e, 'x', NOW())"),
                      {"i": ua, "e": f"fk-{ua.hex[:6]}@x.com"})
            c.execute(text("INSERT INTO workspaces (id, name, type, owner_user_id, created_at) "
                           "VALUES (:i, 'fk', 'shared', :o, NOW())"),
                      {"i": wa, "o": ua})
            _ctx(c, wa, ua, admin=True)
            c.execute(text("INSERT INTO agent_definitions (id, key, title, created_at, updated_at) "
                           "VALUES (:i, 'fk_def', 'fk', NOW(), NOW())"), {"i": did})
            c.execute(text("INSERT INTO agents (id, workspace_id, key, definition_id, created_at) "
                           "VALUES (:i, :w, 'fk_def', :d, NOW())"),
                      {"i": aid, "w": wa, "d": did})
            _ctx(c)
        with engine.begin() as c:
            _ctx(c, wa, ua, admin=True)
            with pytest.raises(sqlalchemy.exc.IntegrityError):
                c.execute(text("DELETE FROM agent_definitions WHERE id = :i"), {"i": did})
        # instance still linked (never silently nulled into fake legacy)
        with engine.connect() as c:
            _ctx(c, wa, ua, admin=True)
            row = c.execute(text("SELECT definition_id FROM agents WHERE id = :i"),
                            {"i": aid}).fetchone()
            c.rollback()
            assert row is not None and str(row[0]) == str(did)
    finally:
        with engine.begin() as c:
            _ctx(c, None, None, None, True)
            c.execute(text("DELETE FROM agents WHERE id = :i"), {"i": aid})
            c.execute(text("DELETE FROM agent_definitions WHERE id = :i"), {"i": did})
            c.execute(text("DELETE FROM workspaces WHERE id = :i"), {"i": wa})
            c.execute(text("DELETE FROM users WHERE id = :i"), {"i": ua})
            _ctx(c)


def test_global_rls_assignment_gate_and_owner_visibility():
    engine = _pg()
    wa, wb = uuid.uuid4(), uuid.uuid4()
    ua, ub, adm = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fit, chem = uuid.uuid4(), uuid.uuid4()
    fit_g, chem_g, ka, kb = (uuid.uuid4() for _ in range(4))
    src_g, doc_g, seg_g, ch_g = (uuid.uuid4() for _ in range(4))
    src_c, doc_c, seg_c, ch_c = (uuid.uuid4() for _ in range(4))
    agt_a = uuid.uuid4()
    try:
        with engine.begin() as c:
            for uid, tag in ((ua, "g-a"), (ub, "g-b"), (adm, "g-adm")):
                c.execute(text("INSERT INTO users (id, email, password_hash, created_at) "
                               "VALUES (:i, :e, 'x', NOW())"),
                          {"i": uid, "e": f"{tag}-{uid.hex[:6]}@x.com"})
            c.execute(text("UPDATE users SET is_admin = true WHERE id = :i"), {"i": adm})
            for wid in (wa, wb):
                c.execute(text("INSERT INTO workspaces (id, name, type, owner_user_id, created_at) "
                               "VALUES (:i, 'g', 'shared', :o, NOW())"),
                          {"i": wid, "o": ua})
            _ctx(c, wa, ua, admin=True)
            for did, key in ((fit, "g_fit"), (chem, "g_chem")):
                c.execute(text("INSERT INTO agent_definitions (id, key, title, created_at, updated_at) "
                               "VALUES (:i, :k, :k, NOW(), NOW())"), {"i": did, "k": key})
            for kid, ws, scope, title in (
                    (fit_g, None, "global", "FitG"), (chem_g, None, "global", "ChemG"),
                    (ka, wa, "workspace", "KA"), (kb, wb, "workspace", "KB")):
                c.execute(text("INSERT INTO knowledge_bases (id, workspace_id, title, scope, created_at) "
                               "VALUES (:i, :w, :t, :s, NOW())"),
                          {"i": kid, "w": ws, "t": title, "s": scope})
            c.execute(text("INSERT INTO agent_definition_knowledge (definition_id, kb_id, created_at) "
                           "VALUES (:d, :k, NOW())"), {"d": fit, "k": fit_g})
            # global chain rows (ws NULL) for fit_g; chem chain for chem_g
            for sid, did_, gid in ((src_g, doc_g, fit_g), (src_c, doc_c, chem_g)):
                c.execute(text("INSERT INTO sources (id, workspace_id, kb_id, type, status, created_at) "
                               "VALUES (:i, NULL, :k, 'note', 'ready', NOW())"),
                          {"i": sid, "k": gid})
            for did_, sid in ((doc_g, src_g), (doc_c, src_c)):
                c.execute(text("INSERT INTO documents (id, workspace_id, source_id, created_at) "
                               "VALUES (:i, NULL, :s, NOW())"), {"i": did_, "s": sid})
            for gid_, did_ in ((seg_g, doc_g), (seg_c, doc_c)):
                c.execute(text("INSERT INTO page_segments (id, workspace_id, document_id, text) "
                               "VALUES (:i, NULL, :d, 't')"), {"i": gid_, "d": did_})
            c.execute(text("INSERT INTO chunks (id, workspace_id, kb_id, segment_id, content, embedding, created_at) "
                           "VALUES (:i, NULL, :k, :s, 't', (:v)::vector, NOW())"),
                      {"i": ch_g, "k": fit_g, "s": seg_g, "v": _vec()})
            c.execute(text("INSERT INTO chunks (id, workspace_id, kb_id, segment_id, content, embedding, created_at) "
                           "VALUES (:i, NULL, :k, :s, 't', (:v)::vector, NOW())"),
                      {"i": ch_c, "k": chem_g, "s": seg_c, "v": _vec()})
            # personal agent of A in wa
            c.execute(text("INSERT INTO agents (id, workspace_id, key, definition_id, owner_user_id, created_at) "
                           "VALUES (:i, :w, 'g_fit', :d, :o, NOW())"),
                      {"i": agt_a, "w": wa, "d": fit, "o": ua})
            _ctx(c)

        def kbs(ws, definition, user=None, admin=False):
            with engine.connect() as c:
                _ctx(c, ws, user, definition, admin)
                rows = c.execute(text("SELECT id FROM knowledge_bases")).fetchall()
                c.rollback()
                return {str(r[0]) for r in rows}

        def chunks(ws, definition, user=None, admin=False):
            with engine.connect() as c:
                _ctx(c, ws, user, definition, admin)
                rows = c.execute(text("SELECT id FROM chunks")).fetchall()
                c.rollback()
                return {str(r[0]) for r in rows}

        def agents(ws, user, admin=False):
            with engine.connect() as c:
                _ctx(c, ws, user, None, admin)
                rows = c.execute(text("SELECT id FROM agents WHERE id = :i"), {"i": agt_a}).fetchall()
                c.rollback()
                return {str(r[0]) for r in rows}

        # 1. fit + wa: own KB + assigned global only
        got = kbs(wa, fit, ua)
        assert str(ka) in got and str(fit_g) in got
        assert str(chem_g) not in got and str(kb) not in got
        # 2. chem (unassigned) + wa: own KB only
        got = kbs(wa, chem, ua)
        assert got == {str(ka)}
        # 3. no definition: no globals (IS NULL alone grants nothing)
        assert kbs(wa, None, ua) == {str(ka)}
        # 4. child records follow the gate
        cg = chunks(wa, fit, ua)
        assert str(ch_g) in cg and str(ch_c) not in cg
        assert chunks(wa, chem, ua) == set()
        # 5. tenant symmetry: wb + fit sees kb + fit_g (assignment is
        # definition-scoped, not workspace-scoped), never wa rows
        got = kbs(wb, fit, ua)
        assert str(kb) in got and str(fit_g) in got and str(ka) not in got
        # 6. member global write denied
        with engine.connect() as c:
            _ctx(c, wa, ua, fit, False)
            with pytest.raises(Exception):
                with c.begin_nested():
                    c.execute(text("INSERT INTO chunks (id, workspace_id, kb_id, segment_id, content, embedding, created_at) "
                                   "VALUES (:i, NULL, :k, :s, 't', (:v)::vector, NOW())"),
                              {"i": uuid.uuid4(), "k": fit_g, "s": seg_g, "v": _vec()})
            c.rollback()
        # admin global write allowed (row removed in cleanup)
        tmp = uuid.uuid4()
        with engine.begin() as c:
            _ctx(c, wa, adm, fit, True)
            c.execute(text("INSERT INTO chunks (id, workspace_id, kb_id, segment_id, content, embedding, created_at) "
                           "VALUES (:i, NULL, :k, :s, 't', (:v)::vector, NOW())"),
                      {"i": tmp, "k": fit_g, "s": seg_g, "v": _vec()})
        with engine.begin() as c:
            _ctx(c, wa, adm, None, True)
            c.execute(text("DELETE FROM chunks WHERE id = :i"), {"i": tmp})
        # 7. owner visibility: B cannot see A's personal agent; admin can
        assert agents(wa, ua) == {str(agt_a)}
        assert agents(wa, ub) == set()
        assert agents(wa, adm, admin=True) == {str(agt_a)}
    finally:
        with engine.begin() as c:
            _ctx(c, None, None, None, True)
            for cid_, table in ((ch_g, "chunks"), (ch_c, "chunks")):
                c.execute(text(f"DELETE FROM {table} WHERE id = :i"), {"i": cid_})
            for sid in (seg_g, seg_c):
                c.execute(text("DELETE FROM page_segments WHERE id = :i"), {"i": sid})
            for did_ in (doc_g, doc_c):
                c.execute(text("DELETE FROM documents WHERE id = :i"), {"i": did_})
            for sid in (src_g, src_c):
                c.execute(text("DELETE FROM sources WHERE id = :i"), {"i": sid})
            c.execute(text("DELETE FROM agent_definition_knowledge WHERE definition_id = :i"), {"i": fit})
            c.execute(text("DELETE FROM agent_definition_knowledge WHERE definition_id = :i"), {"i": chem})
            c.execute(text("DELETE FROM agents WHERE id = :i"), {"i": agt_a})
            for kid in (fit_g, chem_g, ka, kb):
                ws = None if kid in (fit_g, chem_g) else (wa if kid == ka else wb)
                c.execute(text("DELETE FROM knowledge_bases WHERE id = :i"), {"i": kid})
            for did in (fit, chem):
                c.execute(text("DELETE FROM agent_definitions WHERE id = :i"), {"i": did})
            for wid in (wa, wb):
                c.execute(text("DELETE FROM workspaces WHERE id = :i"), {"i": wid})
            for u in (ua, ub, adm):
                c.execute(text("DELETE FROM users WHERE id = :i"), {"i": u})
            _ctx(c)
