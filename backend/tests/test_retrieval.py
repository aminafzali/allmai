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
                         rerank_fn=lambda q, hs, top_k=None: list(reversed(hs)))
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


# ---------- P1: hardening contract + reranker seam ----------

def _cfg(monkeypatch, hybrid=None, rerank=None):
    import app.ai.settings as S

    def _ls(db=None):
        d = {}
        if hybrid is not None:
            d["retrieval.hybrid"] = hybrid
        if rerank is not None:
            d["retrieval.rerank"] = rerank
        return d

    monkeypatch.setattr(S, "list_settings", _ls)


def test_resolve_limits_single_source_of_truth(caplog):
    from app.knowledge.retrieval.hybrid import _hybrid_cfg, _resolve_limits

    assert _resolve_limits(4, _hybrid_cfg(None)) == (4, 12)
    assert _resolve_limits(None, _hybrid_cfg(None)) == (8, 24)
    bad = dict(_hybrid_cfg(None), retrieval_top_k=99)
    with caplog.at_level("WARNING", logger="app.knowledge.retrieval.hybrid"):
        assert _resolve_limits(None, bad) == (8, 24)  # computed wins, loudly
    assert "inconsistent" in caplog.text


def test_threshold_zero_drops_nothing_high_drops_weak(db, monkeypatch):
    from app.knowledge.retrieval.hybrid import hybrid_search as hs

    seed = _seed(db)
    base = hs(db, seed["wa"], "acids", top_k=5, kb_id=seed["k1"],
              query_vector=[1.0, 0.0, 0.0, 0.0], rerank_fn=None)
    assert base, "baseline must return hits"
    _cfg(monkeypatch, hybrid={"min_score": 0.99})
    thin = hs(db, seed["wa"], "acids", top_k=5, kb_id=seed["k1"],
              query_vector=[1.0, 0.0, 0.0, 0.0], rerank_fn=None)
    assert len(thin) < len(base)


def test_ready_only_filter_default(db, monkeypatch):
    from app.knowledge.retrieval.hybrid import hybrid_search as hs

    seed = _seed(db)
    src = Source(id=uuid.uuid4(), workspace_id=seed["wa"], kb_id=seed["k1"],
                 type="note", filename="pending-note", status="processing")
    db.add(src)
    db.flush()
    doc = Document(id=uuid.uuid4(), workspace_id=seed["wa"], source_id=src.id, title="p")
    db.add(doc)
    db.flush()
    seg = PageSegment(id=uuid.uuid4(), workspace_id=seed["wa"], document_id=doc.id,
                      text="acids in citrus fruits overview")
    db.add(seg)
    db.flush()
    db.add(Chunk(id=uuid.uuid4(), workspace_id=seed["wa"], kb_id=seed["k1"],
                 segment_id=seg.id, content="acids in citrus fruits overview",
                 tokens=5, embedding=[1.0, 0.0, 0.0, 0.0], chunk_metadata={}))
    db.commit()
    qv = [1.0, 0.0, 0.0, 0.0]
    kw = dict(top_k=8, kb_id=seed["k1"], query_vector=qv, rerank_fn=None)
    assert "pending-note" not in [h.source.filename for h in hs(db, seed["wa"], "acids", **kw)]
    _cfg(monkeypatch, hybrid={"ready_only": False})
    assert "pending-note" in [h.source.filename for h in hs(db, seed["wa"], "acids", **kw)]


def test_content_dedup_collapses_reingested_copies(db):
    from app.knowledge.retrieval.hybrid import hybrid_search as hs

    seed = _seed(db)
    for i in range(2):
        src = Source(id=uuid.uuid4(), workspace_id=seed["wa"], kb_id=seed["k1"],
                     type="note", filename=f"copy{i}", status="ready")
        db.add(src)
        db.flush()
        doc = Document(id=uuid.uuid4(), workspace_id=seed["wa"], source_id=src.id, title="c")
        db.add(doc)
        db.flush()
        seg = PageSegment(id=uuid.uuid4(), workspace_id=seed["wa"], document_id=doc.id,
                          text="acids donate protons in water")
        db.add(seg)
        db.flush()
        db.add(Chunk(id=uuid.uuid4(), workspace_id=seed["wa"], kb_id=seed["k1"],
                     segment_id=seg.id, content="acids donate protons in water",
                     tokens=5, embedding=[1.0, 0.0, 0.0, 0.0], chunk_metadata={}))
    db.commit()
    hits = hs(db, seed["wa"], "acids", top_k=8, kb_id=seed["k1"],
              query_vector=[1.0, 0.0, 0.0, 0.0], rerank_fn=None)
    texts = [h.chunk.content for h in hits]
    assert texts.count("acids donate protons in water") == 1


def test_parent_heading_prefixed_for_table_hit(db):
    from types import SimpleNamespace

    from app.knowledge.retrieval.passages import expand_hit_texts

    seed = _seed(db)
    src = Source(id=uuid.uuid4(), workspace_id=seed["wa"], kb_id=seed["k1"],
                 type="pdf", filename="s.pdf", status="ready")
    db.add(src)
    db.flush()
    doc = Document(id=uuid.uuid4(), workspace_id=seed["wa"], source_id=src.id, title="s")
    db.add(doc)
    db.flush()
    seg1 = PageSegment(id=uuid.uuid4(), workspace_id=seed["wa"], document_id=doc.id,
                       text="sec", page_no=1)
    seg2 = PageSegment(id=uuid.uuid4(), workspace_id=seed["wa"], document_id=doc.id,
                       text="sec", page_no=2)
    seg3 = PageSegment(id=uuid.uuid4(), workspace_id=seed["wa"], document_id=doc.id,
                       text="sec", page_no=3)
    db.add_all([seg1, seg2, seg3])
    db.flush()
    head = Chunk(id=uuid.uuid4(), workspace_id=seed["wa"], kb_id=seed["k1"],
                 segment_id=seg1.id, content="واکنش‌های شیمیایی", tokens=3,
                 embedding=[0.0] * 4,
                 chunk_metadata={"kind": "heading", "element_id": "e1"})
    filler = Chunk(id=uuid.uuid4(), workspace_id=seed["wa"], kb_id=seed["k1"],
                   segment_id=seg2.id, content="متن میانی", tokens=3,
                   embedding=[0.0] * 4,
                   chunk_metadata={"kind": "paragraph", "element_id": "e9"})
    tbl = Chunk(id=uuid.uuid4(), workspace_id=seed["wa"], kb_id=seed["k1"],
                segment_id=seg3.id, content="| a | b |", tokens=3,
                embedding=[0.0] * 4,
                chunk_metadata={"kind": "table", "element_id": "e2",
                                "parent_id": "e1", "section": "واکنش‌های شیمیایی"})
    db.add_all([head, filler, tbl])
    db.commit()
    hit = SimpleNamespace(chunk=tbl, segment=seg3, source=src, score=0.5)
    expanded = expand_hit_texts(db, [hit])
    assert expanded[str(tbl.id)].startswith("[واکنش‌های شیمیایی]")


def test_rerank_none_and_unknown_disable(monkeypatch):
    from app.knowledge.retrieval.rerank import get_rerank_fn

    _cfg(monkeypatch, rerank={"method": "none"})
    assert get_rerank_fn(None) is None
    _cfg(monkeypatch, rerank={"method": "bogus"})
    assert get_rerank_fn(None) is None


def test_avalai_adapter_maps_by_id_threads_top_n_and_fails_open(monkeypatch):
    from types import SimpleNamespace

    import httpx

    from app.knowledge.retrieval.rerank import AvalAIReranker

    def _hit(cid, text):
        chunk = SimpleNamespace(id=cid, content=text)
        return SimpleNamespace(chunk=chunk, source=None, score=0.01)

    hits = [_hit("c1", "one"), _hit("c2", "two"), _hit("c3", "three")]
    seen = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            # live deployment shape: `data` key, index mapping, no id echo
            return {"data": [
                {"index": 2, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.1},
            ]}

    def _fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, payload=json)
        return _Resp()

    monkeypatch.setattr(httpx, "post", _fake_post)
    fn = AvalAIReranker(model="qwen3-rerank", base_url="https://x",
                        api_key="k", timeout_s=5)
    out = fn("q", hits, top_k=2)
    assert seen["url"] == "https://x/rerank"
    assert seen["payload"]["model"] == "qwen3-rerank"
    assert seen["payload"]["top_n"] == 2
    assert seen["payload"]["documents"] == ["one", "two", "three"]
    assert [h.chunk.id for h in out] == ["c3", "c1"]  # mapped by index, cut to final_k
    assert out[0].rerank_score == 0.9

    def _boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", _boom)
    assert fn("q", hits, top_k=2) == hits  # fail-open keeps fused order
    assert AvalAIReranker(api_key="")("q", hits) == hits  # no key, no HTTP


def test_local_reranker_fail_closed():
    from app.knowledge.retrieval.rerank import LocalReranker

    import pytest

    with pytest.raises(NotImplementedError):
        LocalReranker()("q", [])
