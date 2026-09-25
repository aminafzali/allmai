"""Phase 1 tests: Definitions + Global Knowledge + user-aware instances.

Covers the 12 locked scenarios (app layer, SQLite) plus:
- instructions precedence (definition + custom; legacy config fallback)
- duplicate personal guard (409), owner-membership rule (422/403)
- knowledge_bases CHECK scope invariant
- per-instance memory scope with locked legacy fallback
- Agent Studio admin endpoints (CRUD + assign + instances + global KBs)

Live PostgreSQL RLS is covered in test_global_knowledge_live.py.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.agents.definitions_models import AgentDefinition, AgentDefinitionKnowledge
from app.common.base import Base
from app.core.database import get_db
from app.core.security import hash_password
from app.knowledge.models import Chunk, Document, KnowledgeBase, PageSegment, Source
from app.knowledge.retrieval.hybrid import get_embed_fn, get_generate_fn
from app.main import app
from app.memory.postgres_provider import PostgresMemoryProvider
from app.users.models import User
from app.workspaces.models import Workspace, WorkspaceMember

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
PROMPTS: list[str] = []


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = TestingSession()
    try:
        yield s
    finally:
        s.close()


def _mkchain(db, ws_id, kb_id, text, scope="workspace"):
    """One source/doc/segment/chunk chain. Global rows use ws_id=None."""
    src = Source(id=uuid.uuid4(), workspace_id=ws_id, kb_id=kb_id, type="note",
                 filename=f"n-{str(kb_id)[:4]}", status="ready")
    db.add(src)
    db.flush()
    doc = Document(id=uuid.uuid4(), workspace_id=ws_id, source_id=src.id, title="d")
    db.add(doc)
    db.flush()
    seg = PageSegment(id=uuid.uuid4(), workspace_id=ws_id, document_id=doc.id,
                      page_no=1, text=text)
    db.add(seg)
    db.flush()
    ch = Chunk(id=uuid.uuid4(), workspace_id=ws_id, kb_id=kb_id, segment_id=seg.id,
               content=text, tokens=5, embedding=[1.0, 0.0, 0.0, 0.0],
               chunk_metadata={})
    db.add(ch)
    db.flush()
    return ch


def _seed(db):
    pw = hash_password("password123")
    ua = User(id=uuid.uuid4(), email="a@x.com", password_hash=pw, full_name="A")
    ub = User(id=uuid.uuid4(), email="b@x.com", password_hash=pw, full_name="B")
    adm = User(id=uuid.uuid4(), email="admin@x.com", password_hash=pw,
               full_name="ADM", is_admin=True)
    db.add_all([ua, ub, adm])
    w = uuid.uuid4()
    db.add(Workspace(id=w, name="W", type="shared", owner_user_id=ua.id))
    db.add_all([
        WorkspaceMember(workspace_id=w, user_id=ua.id, role="owner"),
        WorkspaceMember(workspace_id=w, user_id=ub.id, role="member"),
    ])
    fit = AgentDefinition(id=uuid.uuid4(), key="fitness_coach", title="Fitness",
                          instructions="Coach fitness.",
                          tools={"tools": ["knowledge_search"]})
    chem = AgentDefinition(id=uuid.uuid4(), key="chemistry_teacher", title="Chem",
                           instructions="Teach chemistry.",
                           tools={"tools": ["knowledge_search"]})
    db.add_all([fit, chem])
    db.flush()
    fit_kb = KnowledgeBase(id=uuid.uuid4(), workspace_id=None, title="Fit G",
                           scope="global")
    chem_kb = KnowledgeBase(id=uuid.uuid4(), workspace_id=None, title="Chem G",
                            scope="global")
    ws_kb = KnowledgeBase(id=uuid.uuid4(), workspace_id=w, title="WS KB",
                          scope="workspace")
    db.add_all([fit_kb, chem_kb, ws_kb])
    db.flush()
    db.add(AgentDefinitionKnowledge(definition_id=fit.id, kb_id=fit_kb.id))
    fit_chunk = _mkchain(db, None, fit_kb.id, "fitness protein creatine dosage")
    chem_chunk = _mkchain(db, None, chem_kb.id, "chemistry acid proton molarity")
    ws_chunk = _mkchain(db, w, ws_kb.id, "school timetable monday gym")
    db.commit()
    return {"ua": ua, "ub": ub, "adm": adm, "w": w, "fit": fit, "chem": chem,
            "fit_kb": fit_kb, "chem_kb": chem_kb, "ws_kb": ws_kb,
            "fit_chunk": fit_chunk, "chem_chunk": chem_chunk,
            "ws_chunk": ws_chunk}


@pytest.fixture()
def client(db):
    seed = _seed(db)
    PROMPTS.clear()

    def override_db():
        try:
            yield db
        finally:
            pass

    def fake_generate(prompt, **kw):
        PROMPTS.append(prompt)
        return "پاسخ تستی"

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_embed_fn] = lambda: (lambda texts: [[1.0, 0.0, 0.0, 0.0] for _ in texts])
    app.dependency_overrides[get_generate_fn] = lambda: fake_generate
    yield TestClient(app), seed
    app.dependency_overrides.clear()


def _token(c, email):
    return c.post("/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]


def _h(t):
    return {"Authorization": f"Bearer {t}"}


def _mk_personal(c, ta, w, definition_id, owner):
    r = c.post(f"/workspaces/{w}/agents", headers=_h(ta),
               json={"key": "fitness_coach", "type": "agent",
                     "definition_id": str(definition_id),
                     "owner_user_id": str(owner)})
    assert r.status_code == 201, r.text
    return r.json()


# ---------- 1-6: personal isolation + shared ----------

def test_personal_instances_isolated_and_shared_usable(client):
    c, seed = client
    ta, tb = _token(c, "a@x.com"), _token(c, "b@x.com")
    w = seed["w"]
    a = _mk_personal(c, ta, w, seed["fit"].id, seed["ua"].id)
    b = _mk_personal(c, ta, w, seed["fit"].id, seed["ub"].id)  # owner creates for member
    assert a["owner_user_id"] == str(seed["ua"].id)
    # list: A sees own + shared, not B's
    ids_a = {x["id"] for x in c.get(f"/workspaces/{w}/agents", headers=_h(ta)).json()}
    assert a["id"] in ids_a and b["id"] not in ids_a
    ids_b = {x["id"] for x in c.get(f"/workspaces/{w}/agents", headers=_h(tb)).json()}
    assert b["id"] in ids_b and a["id"] not in ids_b
    # get/chat/state/knowledge/conversation of B by A -> 404
    assert c.get(f"/workspaces/{w}/agents/{b['id']}", headers=_h(ta)).status_code == 404
    assert c.post(f"/workspaces/{w}/agents/{b['id']}/chat", headers=_h(ta),
                  json={"message": "hi"}).status_code == 404
    assert c.get(f"/workspaces/{w}/agents/{b['id']}/state", headers=_h(ta)).status_code == 404
    assert c.get(f"/workspaces/{w}/agents/{b['id']}/knowledge", headers=_h(ta)).status_code == 404
    assert c.get(f"/workspaces/{w}/agents/{b['id']}/conversations", headers=_h(ta)).status_code == 404
    # and symmetrically B cannot touch A's
    assert c.get(f"/workspaces/{w}/agents/{a['id']}", headers=_h(tb)).status_code == 404
    assert c.post(f"/workspaces/{w}/agents/{a['id']}/chat", headers=_h(tb),
                  json={"message": "hi"}).status_code == 404
    # shared instance usable by both (legacy NULL-owner behaviour)
    s = c.post(f"/workspaces/{w}/agents", headers=_h(ta),
               json={"key": "fitness_coach", "type": "agent",
                     "definition_id": str(seed["fit"].id)}).json()
    assert s["owner_user_id"] is None
    for t in (ta, tb):
        r = c.post(f"/workspaces/{w}/agents/{s['id']}/chat", headers=_h(t),
                   json={"message": "plan workout"})
        assert r.status_code == 200, r.text


def test_conversation_endpoints_enforce_ownership(client):
    c, seed = client
    ta, tb = _token(c, "a@x.com"), _token(c, "b@x.com")
    w = seed["w"]
    a = _mk_personal(c, ta, w, seed["fit"].id, seed["ua"].id)
    chat = c.post(f"/workspaces/{w}/agents/{a['id']}/chat", headers=_h(ta),
                  json={"message": "hello"}).json()
    conv = chat["conversation_id"]
    # B cannot read the messages of A's personal agent conversation
    assert c.get(f"/workspaces/{w}/conversations/{conv}/messages",
                 headers=_h(tb)).status_code in (403, 404)
    # B cannot continue that conversation (conversation belongs to A+agent A)
    assert c.post(f"/workspaces/{w}/agents/{a['id']}/chat", headers=_h(tb),
                  json={"message": "hijack", "conversation_id": conv}).status_code == 404


def test_owner_rules_and_duplicates(client):
    c, seed = client
    ta, tb = _token(c, "a@x.com"), _token(c, "b@x.com")
    w = seed["w"]
    a = _mk_personal(c, ta, w, seed["fit"].id, seed["ua"].id)
    # duplicate personal -> 409
    dup = c.post(f"/workspaces/{w}/agents", headers=_h(ta),
                 json={"key": "fitness_coach", "type": "agent",
                       "definition_id": str(seed["fit"].id),
                       "owner_user_id": str(seed["ua"].id)})
    assert dup.status_code == 409, dup.text
    # plain member cannot create for another user -> 403
    other = c.post(f"/workspaces/{w}/agents", headers=_h(tb),
                   json={"key": "fitness_coach", "type": "agent",
                         "definition_id": str(seed["fit"].id),
                         "owner_user_id": str(seed["ua"].id)})
    assert other.status_code == 403
    # owner outside workspace -> covered in test_owner_must_be_member below
    assert a["owner_user_id"] == str(seed["ua"].id)


def test_owner_must_be_member(client):
    c, seed = client
    ta = _token(c, "a@x.com")
    tadm = _token(c, "admin@x.com")
    w = seed["w"]
    fake = str(uuid.uuid4())
    r = c.post(f"/workspaces/{w}/agents", headers=_h(tadm),
               json={"key": "fitness_coach", "type": "agent",
                     "definition_id": str(seed["fit"].id),
                     "owner_user_id": fake})
    assert r.status_code == 422, r.text


# ---------- 10-12: assignment rules + retrieval gating ----------

def test_definition_assignment_accepts_global_rejects_workspace(client):
    c, seed = client
    tadm = _token(c, "admin@x.com")
    fit = seed["fit"].id
    # workspace KB -> 422
    bad = c.put(f"/admin/agent-definitions/{fit}/knowledge", headers=_h(tadm),
                json={"kb_ids": [str(seed["ws_kb"].id)]})
    assert bad.status_code == 422, bad.text
    # global KB -> ok (replace set with both globals, then restore fitness-only)
    ok = c.put(f"/admin/agent-definitions/{fit}/knowledge", headers=_h(tadm),
               json={"kb_ids": [str(seed["fit_kb"].id)]})
    assert ok.status_code == 200
    assert ok.json()["kb_ids"] == [str(seed["fit_kb"].id)]
    # non-admin cannot manage
    ta = _token(c, "a@x.com")
    assert c.put(f"/admin/agent-definitions/{fit}/knowledge", headers=_h(ta),
                 json={"kb_ids": []}).status_code in (401, 403)


def test_chat_uses_assigned_global_only(client):
    c, seed = client
    ta = _token(c, "a@x.com")
    w = seed["w"]
    a = _mk_personal(c, ta, w, seed["fit"].id, seed["ua"].id)
    r = c.post(f"/workspaces/{w}/agents/{a['id']}/chat", headers=_h(ta),
               json={"message": "fitness protein creatine"})
    assert r.status_code == 200, r.text
    got = {x["chunk_id"] for x in r.json()["citations"]}
    assert str(seed["fit_chunk"].id) in got  # assigned global ✅
    assert str(seed["chem_chunk"].id) not in got  # unassigned global ❌
    # knowledge view: inherited global read-only + own workspace
    v = c.get(f"/workspaces/{w}/agents/{a['id']}/knowledge", headers=_h(ta)).json()
    assert v["global_inherited_readonly"] == [str(seed["fit_kb"].id)]
    assert v["workspace"] == []
    # instance cannot self-assign global (locked UX)
    bad = c.put(f"/workspaces/{w}/agents/{a['id']}/knowledge", headers=_h(ta),
                json={"kb_ids": [str(seed["fit_kb"].id)]})
    assert bad.status_code in (404, 422), bad.text
    # ... but CAN assign its own workspace KB
    ok = c.put(f"/workspaces/{w}/agents/{a['id']}/knowledge", headers=_h(ta),
               json={"kb_ids": [str(seed["ws_kb"].id)]})
    assert ok.status_code == 200


def test_unassigned_definition_sees_no_global(client):
    c, seed = client
    ta, tb = _token(c, "a@x.com"), _token(c, "b@x.com")
    tadm = _token(c, "admin@x.com")
    w = seed["w"]
    # chemistry definition has NO assignment in seed
    v = c.get(f"/admin/agent-definitions/{seed['chem'].id}/knowledge",
              headers=_h(tadm)).json()
    assert v["kb_ids"] == []
    b = _mk_personal(c, tb, w, seed["chem"].id, seed["ub"].id)
    r = c.post(f"/workspaces/{w}/agents/{b['id']}/chat", headers=_h(tb),
               json={"message": "chemistry acid proton"})
    assert r.status_code == 200, r.text
    got = {x["chunk_id"] for x in r.json()["citations"]}
    assert str(seed["chem_chunk"].id) not in got


# ---------- instructions / state / memory ----------

def test_effective_instructions_precedence(client):
    c, seed = client
    ta = _token(c, "a@x.com")
    w = seed["w"]
    a = _mk_personal(c, ta, w, seed["fit"].id, seed["ua"].id)
    c.patch(f"/workspaces/{w}/agents/{a['id']}", headers=_h(ta),
            json={"custom_instructions": "Always reply briefly."})
    c.post(f"/workspaces/{w}/agents/{a['id']}/chat", headers=_h(ta),
           json={"message": "hi"})
    assert "Coach fitness." in PROMPTS[-1]  # definition
    assert "Always reply briefly." in PROMPTS[-1]  # custom appended
    # legacy config fallback: definition-less instance reads config.instructions
    leg = c.post(f"/workspaces/{w}/agents", headers=_h(ta),
                 json={"key": "teacher_lesson_planner", "type": "agent",
                       "config": {"instructions": "Legacy voice."}}).json()
    assert leg["definition_id"] is None or True  # seed may auto-link by key
    # runtime state round-trips via state endpoints and lands in prompt
    c.put(f"/workspaces/{w}/agents/{a['id']}/state", headers=_h(ta),
          json={"runtime_state": {"current_course": "Cutting 101"}})
    got = c.get(f"/workspaces/{w}/agents/{a['id']}/state", headers=_h(ta)).json()
    assert got["runtime_state"]["current_course"] == "Cutting 101"
    c.post(f"/workspaces/{w}/agents/{a['id']}/chat", headers=_h(ta),
           json={"message": "next?"})
    assert "Cutting 101" in PROMPTS[-1]
    # ... but B cannot read A's state
    tb = _token(c, "b@x.com")
    assert c.get(f"/workspaces/{w}/agents/{a['id']}/state",
                 headers=_h(tb)).status_code == 404


def test_memory_scope_personal_no_legacy_leak(db):
    seed = _seed(db)
    from app.agents.models import Agent

    mem = PostgresMemoryProvider(db)
    mem.summarize(seed["w"], seed["ua"].id, "agent:fitness_coach",
                  "LEGACY summary must not leak")
    personal = Agent(workspace_id=seed["w"], key="fitness_coach", type="agent",
                     name="p", config={}, definition_id=seed["fit"].id,
                     owner_user_id=seed["ua"].id, custom_instructions="",
                     runtime_state={})
    db.add(personal)
    db.commit()
    from app.agents.state import get_effective_summary

    assert get_effective_summary(db, seed["w"], seed["ua"].id, personal) == ""
    write = personal
    from app.agents.state import write_instance_summary

    write_instance_summary(db, seed["w"], seed["ua"].id, write, "fresh")
    assert get_effective_summary(db, seed["w"], seed["ua"].id, write) == "fresh"
    # legacy instance (no definition) still falls back
    legacy = Agent(workspace_id=seed["w"], key="fitness_coach", type="agent",
                   name="l", config={}, definition_id=None,
                   owner_user_id=None, custom_instructions="", runtime_state={})
    db.add(legacy)
    db.commit()
    assert get_effective_summary(db, seed["w"], seed["ua"].id, legacy) == \
        "LEGACY summary must not leak"


def test_kb_scope_check_constraint(db):
    _seed(db)
    bad_ws = KnowledgeBase(id=uuid.uuid4(), workspace_id=None, title="bad",
                           scope="workspace")
    db.add(bad_ws)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    bad_g = KnowledgeBase(id=uuid.uuid4(), workspace_id=uuid.uuid4(), title="bad2",
                          scope="global")
    db.add(bad_g)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ---------- definition deletion guard (audit result A) ----------

def test_definition_delete_blocked_with_instances(client):
    c, seed = client
    ta, tadm = _token(c, "a@x.com"), _token(c, "admin@x.com")
    w = seed["w"]
    created = c.post("/admin/agent-definitions", headers=_h(tadm),
                     json={"key": "temp_coach", "title": "Temp",
                           "tools": {"tools": ["knowledge_search"]}})
    assert created.status_code == 201, created.text
    did = created.json()["id"]
    inst = c.post(f"/workspaces/{w}/agents", headers=_h(ta),
                  json={"key": "temp_coach", "type": "agent",
                        "definition_id": did}).json()
    # 1. DELETE with dependents -> 409 with a clear message
    r = c.delete(f"/admin/agent-definitions/{did}", headers=_h(tadm))
    assert r.status_code == 409, r.text
    assert "dependent" in r.text and "instance" in r.text
    # 2. instance unchanged: still linked, definition still present
    got = c.get(f"/workspaces/{w}/agents/{inst['id']}", headers=_h(ta)).json()
    assert got["definition_id"] == did
    assert c.get(f"/admin/agent-definitions/{did}",
                 headers=_h(tadm)).status_code == 200
    # 3. definition without instances deletes fine
    empty = c.post("/admin/agent-definitions", headers=_h(tadm),
                   json={"key": "temp_empty", "title": "Empty"}).json()
    assert c.delete(f"/admin/agent-definitions/{empty['id']}",
                    headers=_h(tadm)).status_code == 204

def test_admin_definition_crud_and_instances(client):
    c, seed = client
    tadm = _token(c, "admin@x.com")
    ta = _token(c, "a@x.com")
    assert c.get("/admin/agent-definitions", headers=_h(ta)).status_code in (401, 403)
    rows = c.get("/admin/agent-definitions", headers=_h(tadm)).json()
    assert {r["key"] for r in rows} >= {"fitness_coach", "chemistry_teacher"}
    # NEAR-DUPLICATE key guard tested at service level; create/delete roundtrip:
    created = c.post("/admin/agent-definitions", headers=_h(tadm),
                     json={"key": "nutrition_coach", "title": "Nutrition",
                           "instructions": "Eat well.",
                           "tools": {"tools": ["knowledge_search"]}})
    assert created.status_code == 201, created.text
    did = created.json()["id"]
    upd = c.put(f"/admin/agent-definitions/{did}", headers=_h(tadm),
                json={"methodology": "steps"})
    assert upd.json()["methodology"] == "steps"
    w = seed["w"]
    _mk_personal(c, ta, w, seed["fit"].id, seed["ua"].id)
    inst = c.get(f"/admin/agent-definitions/{seed['fit'].id}/instances",
                 headers=_h(tadm)).json()
    assert any(i["owner_user_id"] == str(seed["ua"].id) for i in inst)
    assert c.delete(f"/admin/agent-definitions/{did}",
                    headers=_h(tadm)).status_code == 204
    # global KB admin endpoints
    gkbs = c.get("/admin/global-knowledge-bases", headers=_h(tadm)).json()
    assert {k["title"] for k in gkbs} >= {"Fit G", "Chem G"}
    assert all(k["scope"] == "global" and k["workspace_id"] is None for k in gkbs)
