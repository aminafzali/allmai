"""Streaming tests: real SSE path, token assembly, persistence, E2E."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.common.base import Base
from app.core.database import get_db
from app.core.security import hash_password
from app.knowledge.models import Chunk, Document, KnowledgeBase, PageSegment, Source
from app.knowledge.retrieval.hybrid import get_embed_fn, get_stream_fn
from app.main import app
from app.users.models import User
from app.workspaces.models import Workspace, WorkspaceMember

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)


async def fake_stream(prompt, **kw):
    for tok in ["پاسخ ", "تستی", " [1]"]:
        yield tok


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
    src = Source(id=uuid.uuid4(), workspace_id=wa, kb_id=kb, type="note",
                 filename="n.pdf", status="ready")
    db.add(src)
    db.flush()
    doc = Document(id=uuid.uuid4(), workspace_id=wa, source_id=src.id, title="d")
    db.add(doc)
    db.flush()
    seg = PageSegment(id=uuid.uuid4(), workspace_id=wa, document_id=doc.id, page_no=2,
                      text="acids donate protons")
    db.add(seg)
    db.flush()
    chunk = Chunk(id=uuid.uuid4(), workspace_id=wa, kb_id=kb, segment_id=seg.id,
                  content="acids donate protons", tokens=4,
                  embedding=[1.0, 0.0], chunk_metadata={})
    db.add(chunk)
    db.commit()
    return {"wa": wa, "wb": wb, "kb": kb, "chunk": chunk}


@pytest.fixture()
def client(db):
    _seed(db)

    def override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_embed_fn] = lambda: (lambda texts: [[1.0, 0.0] for _ in texts])
    app.dependency_overrides[get_stream_fn] = lambda: fake_stream
    yield TestClient(app)
    app.dependency_overrides.clear()


def _token(c, email):
    return c.post("/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]


def _h(t):
    return {"Authorization": f"Bearer {t}"}


def _frames(response) -> list:
    assert response.status_code == 200, response.text
    assert "text/event-stream" in response.headers["content-type"]
    events = []
    for line in response.text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            events.append({"type": "[DONE]"})
        else:
            events.append(json.loads(data))
    return events


def test_agent_chat_stream_tokens_then_persisted(client):
    c = client
    ta = _token(c, "a@x.com")
    with TestingSession() as s:
        wa = s.query(Workspace).filter(Workspace.name == "A").one().id
        kb = s.query(KnowledgeBase).filter(KnowledgeBase.title == "KB").one().id
        chunk = s.query(Chunk).first()
    aid = c.post(f"/workspaces/{wa}/agents",
                 json={"key": "teacher_lesson_planner"}, headers=_h(ta)).json()["id"]

    r = c.post(f"/workspaces/{wa}/agents/{aid}/chat/stream", headers=_h(ta),
               json={"message": "Explain acids", "kb_id": str(kb)})
    events = _frames(r)
    assert events[0]["type"] == "meta"
    assert events[0]["citations"][0]["chunk_id"] == str(chunk.id)
    assert events[0]["citations"][0]["page_no"] == 2
    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert tokens == ["پاسخ ", "تستی", " [1]"]
    assert events[-2]["type"] == "done"
    assert events[-1] == {"type": "[DONE]"}
    conv_id = events[0]["conversation_id"]

    # full answer persisted as one assistant message (streaming is transport only)
    msgs = c.get(f"/workspaces/{wa}/conversations/{conv_id}/messages", headers=_h(ta)).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "پاسخ تستی [1]"
    assert msgs[1]["citations"]["citations"][0]["chunk_id"] == str(chunk.id)

    # continue the same conversation over stream (stateful E2E)
    r2 = c.post(f"/workspaces/{wa}/agents/{aid}/chat/stream", headers=_h(ta),
                json={"message": "More?", "conversation_id": conv_id})
    assert _frames(r2)[0]["conversation_id"] == conv_id


def test_coach_chat_stream_with_memory_and_plan(client):
    c = client
    ta = _token(c, "a@x.com")
    with TestingSession() as s:
        wa = s.query(Workspace).filter(Workspace.name == "A").one().id
    aid = c.post(f"/workspaces/{wa}/agents",
                 json={"key": "student_academic_coach"}, headers=_h(ta)).json()["id"]
    c.post(f"/workspaces/{wa}/agents/{aid}/profile", headers=_h(ta),
           json={"facts": {"grade": "9th"}})

    r = c.post(f"/workspaces/{wa}/agents/{aid}/coach/chat/stream", headers=_h(ta),
               json={"message": "Help me plan"})
    events = _frames(r)
    assert events[0]["type"] == "meta" and events[0]["has_plan"] is False
    assert "".join(e["text"] for e in events if e["type"] == "token") == "پاسخ تستی [1]"
    conv_id = events[0]["conversation_id"]

    msgs = c.get(f"/workspaces/{wa}/conversations/{conv_id}/messages", headers=_h(ta)).json()
    assert len(msgs) == 2 and msgs[1]["content"] == "پاسخ تستی [1]"

    # rolling summary written, bound to the right user+workspace
    from app.memory.models import ConversationSummary

    with TestingSession() as s:
        rows = s.query(ConversationSummary).all()
        assert len(rows) == 1
        assert "Help me plan" in rows[0].summary


def test_stream_rejects_empty_and_foreign(client):
    c = client
    ta, tb = _token(c, "a@x.com"), _token(c, "b@x.com")
    with TestingSession() as s:
        wa = s.query(Workspace).filter(Workspace.name == "A").one().id
    aid = c.post(f"/workspaces/{wa}/agents",
                 json={"key": "teacher_lesson_planner"}, headers=_h(ta)).json()["id"]
    assert c.post(f"/workspaces/{wa}/agents/{aid}/chat/stream", headers=_h(ta),
                  json={"message": "  "}).status_code == 422
    assert c.post(f"/workspaces/{wa}/agents/{aid}/chat/stream", headers=_h(tb),
                  json={"message": "hi"}).status_code == 404
    sid = c.post(f"/workspaces/{wa}/agents",
                 json={"key": "student_academic_coach"}, headers=_h(ta)).json()["id"]
    assert c.post(f"/workspaces/{wa}/agents/{sid}/coach/chat/stream", headers=_h(tb),
                  json={"message": "hi"}).status_code == 404
