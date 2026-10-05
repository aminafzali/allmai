"""Agent engine tests: CRUD, chat pipeline, citations, history, isolation."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.agents.runtime.langgraph_runner import node_build_prompt
from app.common.base import Base
from app.core.database import get_db
from app.core.security import hash_password
from app.knowledge.models import Chunk, Document, KnowledgeBase, PageSegment, Source
from app.knowledge.retrieval.hybrid import get_embed_fn, get_generate_fn
from app.main import app
from app.users.models import User
from app.workspaces.models import Workspace, WorkspaceMember

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)
PROMPTS: list[str] = []


def _last_chat_prompt() -> str:
    """Latest chat prompt (skips auto-title prompts appended after turns)."""
    return next(p for p in reversed(PROMPTS)
                if not p.startswith("یک عنوان فارسی"))


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = TestingSession()
    try:
        yield s
    finally:
        s.close()


def _seed(db):
    ua = User(id=uuid.uuid4(), email="a@x.com", password_hash=hash_password("password123"), full_name="A")
    ub = User(id=uuid.uuid4(), email="b@x.com", password_hash=hash_password("password123"), full_name="B")
    db.add_all([ua, ub])
    wa, wb = uuid.uuid4(), uuid.uuid4()
    db.add_all([
        Workspace(id=wa, name="A", type="shared", owner_user_id=ua.id),
        Workspace(id=wb, name="B", type="shared", owner_user_id=ub.id),
    ])
    db.add_all([
        WorkspaceMember(workspace_id=wa, user_id=ua.id, role="owner"),
        WorkspaceMember(workspace_id=wb, user_id=ub.id, role="owner"),
    ])
    kb = uuid.uuid4()
    db.add(KnowledgeBase(id=kb, workspace_id=wa, title="KB"))
    src = Source(id=uuid.uuid4(), workspace_id=wa, kb_id=kb, type="note", filename="n", status="ready")
    db.add(src)
    db.flush()
    doc = Document(id=uuid.uuid4(), workspace_id=wa, source_id=src.id, title="d")
    db.add(doc)
    db.flush()
    seg = PageSegment(id=uuid.uuid4(), workspace_id=wa, document_id=doc.id, page_no=3,
                      text="acids donate protons in water")
    db.add(seg)
    db.flush()
    chunk = Chunk(id=uuid.uuid4(), workspace_id=wa, kb_id=kb, segment_id=seg.id,
                  content="acids donate protons in water", tokens=5,
                  embedding=[1.0, 0.0, 0.0, 0.0], chunk_metadata={})
    db.add(chunk)
    db.commit()
    return {"wa": wa, "wb": wb, "kb": kb, "chunk": chunk}


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
        return "پاسخ تستی [1]"

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_embed_fn] = lambda: (lambda texts: [[1.0, 0.0, 0.0, 0.0] for _ in texts])
    app.dependency_overrides[get_generate_fn] = lambda: fake_generate
    yield TestClient(app), seed
    app.dependency_overrides.clear()


def _token(c, email):
    return c.post("/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]


def _h(t):
    return {"Authorization": f"Bearer {t}"}


def test_agent_crud_and_chat_flow(client):
    c, seed = client
    ta = _token(c, "a@x.com")
    wa = seed["wa"]

    bad = c.post(f"/workspaces/{wa}/agents", json={"key": "Nope!", "type": "agent"}, headers=_h(ta))
    assert bad.status_code == 422
    r = c.post(f"/workspaces/{wa}/agents",
               json={"key": "teacher_lesson_planner", "name": "T",
                     "config": {"instructions": "Be concise."}}, headers=_h(ta))
    assert r.status_code == 201, r.text
    assert r.json()["type"] == "agent"  # registry spec wins
    agent_id = r.json()["id"]
    assert len(c.get(f"/workspaces/{wa}/agents", headers=_h(ta)).json()) == 1

    # memory fact flows into the prompt
    c.post(f"/workspaces/{wa}/memory/facts", headers=_h(ta),
           json={"key": "grade", "value": "9th", "category": "fact"})

    chat = c.post(f"/workspaces/{wa}/agents/{agent_id}/chat", headers=_h(ta),
                  json={"message": "Explain acids", "kb_id": str(seed["kb"])})
    assert chat.status_code == 200, chat.text
    body = chat.json()
    assert body["answer"] == "پاسخ تستی [1]"
    assert body["citations"][0]["chunk_id"] == str(seed["chunk"].id)
    assert body["citations"][0]["page_no"] == 3
    conv_id = body["conversation_id"]
    assert "Be concise." in _last_chat_prompt()  # custom instructions win
    assert "grade" not in _last_chat_prompt()  # teacher spec has no memory tools

    before = c.get(f"/workspaces/{wa}/agents/{agent_id}/conversations", headers=_h(ta)).json()
    preview = c.post(
        f"/workspaces/{wa}/agents/{agent_id}/chat/preview",
        headers=_h(ta),
        json={"message": "خلاصه این جلسه را بده", "kb_id": str(seed["kb"])},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["answer"] == "پاسخ تستی [1]"
    after = c.get(f"/workspaces/{wa}/agents/{agent_id}/conversations", headers=_h(ta)).json()
    assert len(after) == len(before)  # preview is not a chat turn

    # student coach DOES have memory tools: the same fact flows into its prompt
    s = c.post(f"/workspaces/{wa}/agents",
               json={"key": "student_academic_coach"}, headers=_h(ta)).json()
    chat_s = c.post(f"/workspaces/{wa}/agents/{s['id']}/chat", headers=_h(ta),
                    json={"message": "Plan my week"})
    assert chat_s.status_code == 200
    assert "grade" in _last_chat_prompt() and "9th" in _last_chat_prompt()  # memory injected

    # history persists; second turn sees the first
    chat2 = c.post(f"/workspaces/{wa}/agents/{agent_id}/chat", headers=_h(ta),
                   json={"message": "And bases?", "conversation_id": conv_id})
    assert chat2.json()["conversation_id"] == conv_id
    assert "Explain acids" in _last_chat_prompt()

    msgs = c.get(f"/workspaces/{wa}/conversations/{conv_id}/messages", headers=_h(ta)).json()
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    convs = c.get(f"/workspaces/{wa}/agents/{agent_id}/conversations", headers=_h(ta)).json()
    assert len(convs) == 1 and convs[0]["state"]["turns"] == 2

    # empty message rejected
    assert c.post(f"/workspaces/{wa}/agents/{agent_id}/chat", headers=_h(ta),
                  json={"message": "  "}).status_code == 422


def test_chat_cross_workspace_blocked(client):
    c, seed = client
    tb = _token(c, "b@x.com")
    # B creates own agent; A cannot touch it and B cannot touch A's
    ta = _token(c, "a@x.com")
    wa = seed["wa"]
    agent_id = c.post(f"/workspaces/{wa}/agents",
                      json={"key": "student_academic_coach"}, headers=_h(ta)).json()["id"]
    assert c.post(f"/workspaces/{wa}/agents/{agent_id}/chat", headers=_h(tb),
                  json={"message": "hi"}).status_code == 404
    assert c.get(f"/workspaces/{wa}/agents/{agent_id}", headers=_h(tb)).status_code == 404


def test_prompt_has_safety_and_citation_rules():
    out = node_build_prompt(
        {"message": "hi", "spec": {"goal": "Help.", "tools": []}, "chunks": [
            {"source": "f.pdf", "content": "x", "chunk_id": "1",
             "page_no": 1, "start_ms": None, "end_ms": None}]},
        __import__("app.agents.runtime.langgraph_runner", fromlist=["RuntimeDeps"]).RuntimeDeps(db=None),
    )
    assert "Safety rules" in out["prompt"]
    assert "[1]" in out["prompt"]
