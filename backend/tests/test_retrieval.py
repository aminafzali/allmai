"""Retrieval tests: ranking, kb filter, cross-workspace invisibility, debug."""

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
from app.core.workspace import MissingWorkspaceContextError
from app.knowledge.models import Chunk, Document, KnowledgeBase, PageSegment, Source
from app.knowledge.retrieval.hybrid import get_embed_fn, get_generate_fn, hybrid_search
from app.main import app
from app.users.models import User
from app.workspaces.models import Workspace, WorkspaceMember

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)


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
    """ws A: kb1 (acids, bases) + kb2 (salts); ws B: kb with acids text."""
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
    k1, k2, k3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.add_all([
        KnowledgeBase(id=k1, workspace_id=wa, title="KB1"),
        KnowledgeBase(id=k2, workspace_id=wa, title="KB2"),
        KnowledgeBase(id=k3, workspace_id=wb, title="KB3"),
    ])
    specs = [
        (wa, k1, "acids donate protons in water", [1.0, 0.0, 0.0, 0.0]),
        (wa, k1, "bases accept protons hydroxide", [0.0, 1.0, 0.0, 0.0]),
        (wa, k2, "salts form from neutralization", [0.0, 0.0, 1.0, 0.0]),
        (wb, k3, "acids donate protons in water", [1.0, 0.0, 0.0, 0.0]),
    ]
    for ws, kb, text, vec in specs:
        src = Source(id=uuid.uuid4(), workspace_id=ws, kb_id=kb, type="note",
                     filename="n", status="ready")
        db.add(src)
        db.flush()
        doc = Document(id=uuid.uuid4(), workspace_id=ws, source_id=src.id, title="d")
        db.add(doc)
        db.flush()
        seg = PageSegment(id=uuid.uuid4(), workspace_id=ws, document_id=doc.id, text=text)
        db.add(seg)
        db.flush()
        db.add(Chunk(id=uuid.uuid4(), workspace_id=ws, kb_id=kb, segment_id=seg.id,
                     content=text, tokens=5, embedding=vec, chunk_metadata={}))
    db.commit()
    return {"wa": wa, "wb": wb, "k1": k1, "k2": k2, "ua": ua, "ub": ub}


@pytest.fixture()
def client(db):
    _seed(db)

    def override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_embed_fn] = lambda: (lambda texts: [[1.0, 0.0, 0.0, 0.0] for _ in texts])
    app.dependency_overrides[get_generate_fn] = lambda: (lambda prompt, **kw: "canned answer")
    yield TestClient(app)
    app.dependency_overrides.clear()


def _token(c, email):
    return c.post("/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]


def _h(t):
    return {"Authorization": f"Bearer {t}"}


def test_vector_ranking_and_kb_filter(client, db):
    c = client
    ta = _token(c, "a@x.com")
    wa = db.query(Workspace).filter(Workspace.name == "A").one().id
    k1 = db.query(KnowledgeBase).filter(KnowledgeBase.title == "KB1").one().id
    k2 = db.query(KnowledgeBase).filter(KnowledgeBase.title == "KB2").one().id

    r = c.post(f"/workspaces/{wa}/knowledge/search",
               json={"query": "acids", "top_k": 5}, headers=_h(ta))
    assert r.status_code == 200, r.text
    chunks = r.json()["chunks"]
    assert chunks[0]["content"].startswith("acids")
    assert chunks[0]["vector_rank"] == 0

    r2 = c.post(f"/workspaces/{wa}/knowledge/search",
                json={"query": "acids", "kb_id": str(k2)}, headers=_h(ta))
    assert [x["content"] for x in r2.json()["chunks"]] == ["salts form from neutralization"]

    # same text lives in workspace B but is invisible here
    assert all("KB3" not in x.get("filename", "") for x in chunks)
    assert len(chunks) == 3  # only A's chunks


def test_cross_workspace_search_empty(client, db):
    c = client
    tb = _token(c, "b@x.com")
    wb = db.query(Workspace).filter(Workspace.name == "B").one().id
    r = c.post(f"/workspaces/{wb}/knowledge/search",
               json={"query": "salts"}, headers=_h(tb))
    assert r.status_code == 200
    assert r.json()["chunks"] == [] or all("salts" not in x["content"] for x in r.json()["chunks"])
    # B finds its own acids chunk
    r2 = c.post(f"/workspaces/{wb}/knowledge/search",
                json={"query": "acids"}, headers=_h(tb))
    assert any(x["content"].startswith("acids") for x in r2.json()["chunks"])


def test_fts_fusion_without_vector_match(db):
    seed = _seed(db)
    # query vector orthogonal to everything; FTS (LIKE) must still find "bases"
    hits = hybrid_search(db, seed["wa"], "bases accept", top_k=5,
                         query_vector=[0.0, 0.0, 0.0, 1.0])
    assert any(h.chunk.content.startswith("bases") for h in hits)
    bases = next(h for h in hits if h.chunk.content.startswith("bases"))
    assert bases.fts_rank == 0


def test_rerank_seam_reorders(db):
    seed = _seed(db)
    hits = hybrid_search(db, seed["wa"], "acids", top_k=3,
                         query_vector=[1.0, 0.0, 0.0, 0.0],
                         rerank_fn=lambda q, hs: list(reversed(hs)))
    assert hits[0].chunk.content != "acids donate protons in water"


def test_unscoped_search_raises(db):
    _seed(db)
    with pytest.raises(MissingWorkspaceContextError):
        hybrid_search(db, None, "acids", query_vector=[1.0, 0.0, 0.0, 0.0])


def test_debug_trace_and_role_gate(client, db):
    c = client
    ta = _token(c, "a@x.com")
    wa = db.query(Workspace).filter(Workspace.name == "A").one().id

    r = c.post(f"/workspaces/{wa}/knowledge/debug",
               json={"query": "what are acids?", "top_k": 2}, headers=_h(ta))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "gpt-4o-mini"
    assert len(body["retrieved"]) == 2
    assert "[1]" in body["context"] and "acids" in body["context"]
    assert body["answer"] == "canned answer"
    assert all("score" in x and "source_id" in x for x in body["retrieved"])

    # plain member (not owner/admin) is blocked from the debugger
    import uuid as _uuid

    member = User(id=_uuid.uuid4(), email="m@x.com", password_hash=hash_password("password123"), full_name="M")
    db.add(member)
    db.add(WorkspaceMember(workspace_id=wa, user_id=member.id, role="member"))
    db.commit()
    tm = _token(c, "m@x.com")
    denied = c.post(f"/workspaces/{wa}/knowledge/debug",
                    json={"query": "x"}, headers=_h(tm))
    assert denied.status_code == 403
    # ...but can still search
    ok = c.post(f"/workspaces/{wa}/knowledge/search",
                json={"query": "acids"}, headers=_h(tm))
    assert ok.status_code == 200
