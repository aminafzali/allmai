"""P3 usage ledger tests (hermetic: no network, no live PG).

- pricing math + unknown-model None
- context bind/get/clear + middleware trace seeding
- recorder payload shaping (captured via monkeypatched _emit), never-raises
- summarize_usage aggregation + filters on SQLite
- hooks: provider model event, hybrid retrieval/rerank spans,
  run_agent turn span, ingest extraction span
- endpoints: admin overview + workspace gating
"""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.common.base import Base
from app.core.security import hash_password
from app.knowledge.models import Chunk, KnowledgeBase, PageSegment, Source
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


@pytest.fixture()
def capture(monkeypatch):
    import app.usage.recorder as R

    events: list[dict] = []
    monkeypatch.setattr(R, "_emit", events.append)
    return events


@pytest.fixture(autouse=True)
def _clear_ctx():
    from app.usage import context as C

    C.clear_usage_context()
    yield
    C.clear_usage_context()


def _seed_ws(db, email="u@x.com"):
    pw = hash_password("password123")
    u = User(id=uuid.uuid4(), email=email, password_hash=pw)
    db.add(u)
    w = uuid.uuid4()
    db.add(Workspace(id=w, name="W", type="shared", owner_user_id=u.id))
    db.add(WorkspaceMember(workspace_id=w, user_id=u.id, role="owner"))
    kb = KnowledgeBase(id=uuid.uuid4(), workspace_id=w, title="KB",
                       scope="workspace")
    db.add(kb)
    db.commit()
    return u, w, kb


# ---------- pricing ----------

def test_pricing_math_and_unknown():
    from app.usage.pricing import estimate_cost_usd

    assert estimate_cost_usd("openai_compat", "gpt-4o-mini", 1000, 1000) == \
        round((0.15 + 0.6) / 1000, 6)
    # any-provider fallback on the model id
    assert estimate_cost_usd("something-else", "gemini-2.5-flash",
                             1000, 0) == round(0.3 / 1000, 6)
    assert estimate_cost_usd("avalai", "qwen3-rerank", 10, 10) is None
    assert estimate_cost_usd("openai_compat", None, 10, 10) is None
    assert estimate_cost_usd("openai_compat", "gpt-4o-mini", -5, -5) == 0.0


# ---------- context ----------

def test_context_bind_get_clear():
    from app.usage import context as C

    assert C.current_trace_id() is None
    t = C.bind_usage_context(workspace_id="w1", user_id="u1")
    assert C.current_trace_id() == t
    assert C.current_workspace_id() == "w1" and C.current_user_id() == "u1"
    # None args leave bindings untouched
    C.bind_usage_context()
    assert C.current_workspace_id() == "w1"
    C.clear_usage_context()
    assert C.current_trace_id() is None


def test_middleware_seeds_trace():
    import asyncio

    from app.usage import context as C

    seen = {}

    async def app(scope, receive, send):
        seen["trace"] = C.current_trace_id()

    asyncio.run(C.UsageTraceMiddleware(app)({"type": "http"}, None, None))
    assert seen["trace"]


# ---------- recorder ----------

def test_record_event_payload_and_cost(capture, db):
    from app.usage import recorder as R

    _, w, _ = _seed_ws(db)
    R.record_event("model", provider="openai_compat", model="gpt-4o-mini",
                   prompt_tokens=100, completion_tokens=50,
                   latency_ms=12.5, workspace_id=w, meta={"a": 1})
    assert len(capture) == 1
    ev = capture[0]
    assert ev["span"] == "model" and ev["total_tokens"] == 150
    assert ev["workspace_id"] == w and ev["trace_id"] == ""
    assert ev["cost_usd"] == pytest.approx((100 * 0.00015 + 50 * 0.0006) / 1000)
    assert ev["latency_ms"] == 12.5


def test_record_event_context_fallback_and_unknown_span(capture):
    from app.usage import context as C
    from app.usage import recorder as R

    C.bind_usage_context(workspace_id="w9", user_id="u9", trace_id="t9")
    R.record_event("retrieval", meta={})
    assert capture[0]["workspace_id"] == "w9"
    assert capture[0]["user_id"] == "u9"
    assert capture[0]["trace_id"] == "t9"
    R.record_event("nope")  # unknown spans dropped loudly-in-logs, silently here
    assert len(capture) == 1


def test_record_event_never_raises(monkeypatch):
    import app.usage.recorder as R

    def boom(payload):
        raise RuntimeError("sink down")

    monkeypatch.setattr(R, "_emit", boom)
    R.record_event("model", provider="x", model="y")  # must not raise


# ---------- summarize ----------

def _row(db, **kw):
    from app.usage.models import UsageEvent
    from app.usage.pricing import estimate_cost_usd

    defaults = {"span": "model", "provider": "openai_compat",
                "model": "gpt-4o-mini", "prompt_tokens": 100,
                "completion_tokens": 50, "calls": 1, "ok": True}
    defaults.update(kw)
    if "cost_usd" not in kw:
        defaults["cost_usd"] = estimate_cost_usd(
            defaults.get("provider"), defaults.get("model"),
            defaults.get("prompt_tokens", 0),
            defaults.get("completion_tokens", 0))
    r = UsageEvent(**defaults)
    db.add(r)
    db.commit()
    return r


def test_summarize_totals_breakdown_filters(db):
    from app.usage.service import summarize_usage

    u, w, _ = _seed_ws(db)
    _row(db, workspace_id=w, user_id=u.id, span="model",
         provider="openai_compat", model="gpt-4o-mini",
         prompt_tokens=1000, completion_tokens=500)
    _row(db, workspace_id=w, span="retrieval", provider="", model="",
         prompt_tokens=0, completion_tokens=0)
    _row(db, workspace_id=w, span="model", provider="avalai",
         model="qwen3-rerank", ok=False, error="timeout",
         latency_ms=100.0, prompt_tokens=0, completion_tokens=0)
    out = summarize_usage(db, workspace_id=w)
    assert out["totals"]["events"] == 3
    assert out["totals"]["prompt_tokens"] == 1000
    assert out["totals"]["completion_tokens"] == 500
    assert out["totals"]["errors"] == 1
    assert out["totals"]["cost_usd"] > 0
    assert out["totals"]["avg_latency_ms"] == 100.0
    assert len(out["by_model"]) == 3
    assert len(out["rows"]) == 3
    assert out["rows"][0]["workspace_id"] == str(w)
    only_model = summarize_usage(db, workspace_id=w, span="model")
    assert only_model["totals"]["events"] == 2
    only_user = summarize_usage(db, user_id=u.id)
    assert only_user["totals"]["events"] == 1
    assert summarize_usage(db, workspace_id=uuid.uuid4())["totals"]["events"] == 0


# ---------- hooks ----------

def test_provider_generate_records_model_event(monkeypatch, capture):
    import httpx as _httpx

    from app.ai.openai_compat import OpenAICompatProvider

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "hi"}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3,
                              "total_tokens": 10}}

    monkeypatch.setattr(_httpx, "post",
                        lambda *a, **k: Resp())
    p = OpenAICompatProvider.__new__(OpenAICompatProvider)
    p.base_url = "https://api.gapgpt.app/v1"
    p.api_key = "k"
    p.chat_model = "gpt-4o-mini"
    p.embed_model = "text-embedding-3-small"
    assert p.generate("hello") == "hi"
    assert len(capture) == 1
    ev = capture[0]
    assert ev["span"] == "model" and ev["model"] == "gpt-4o-mini"
    assert ev["total_tokens"] == 10 and ev["ok"] is True


def test_provider_embed_records_tokens(monkeypatch, capture):
    import httpx as _httpx

    from app.ai.openai_compat import OpenAICompatProvider

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"index": 0, "embedding": [0.1]}],
                    "usage": {"prompt_tokens": 4, "total_tokens": 4}}

    monkeypatch.setattr(_httpx, "post", lambda *a, **k: Resp())
    p = OpenAICompatProvider.__new__(OpenAICompatProvider)
    p.base_url = "https://x/v1"
    p.api_key = "k"
    p.embed_model = "text-embedding-3-small"
    assert p.embed(["hello"]) == [[0.1]]
    assert capture[0]["prompt_tokens"] == 4
    assert capture[0]["model"] == "text-embedding-3-small"


def _doc_with_chunks(db, w, kb):
    src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, type="note",
                 filename="n.pdf", status="ready")
    db.add(src)
    db.flush()
    from app.knowledge.models import Document

    doc = Document(id=uuid.uuid4(), workspace_id=w, source_id=src.id,
                   title="n")
    db.add(doc)
    db.flush()
    seg = PageSegment(id=uuid.uuid4(), workspace_id=w, document_id=doc.id,
                      page_no=1, text="اسیدها پروتون می‌دهند")
    db.add(seg)
    db.flush()
    db.add(Chunk(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id,
                 segment_id=seg.id, content="اسیدها پروتون می‌دهند",
                 tokens=5, embedding=[1.0, 0.0], chunk_metadata={}))
    db.commit()


def test_hybrid_search_records_retrieval_and_rerank(db, capture):
    from app.knowledge.retrieval.hybrid import hybrid_search
    from app.knowledge.retrieval.rerank import heuristic_rerank

    _, w, kb = _seed_ws(db)
    _doc_with_chunks(db, w, kb)
    hits = hybrid_search(db, w, "اسیدها", kb_id=kb.id, top_k=2,
                         query_vector=[1.0, 0.0],
                         embed_fn=lambda texts: [[1.0, 0.0] for _ in texts],
                         rerank_fn=heuristic_rerank)
    assert hits
    spans = sorted(e["span"] for e in capture)
    assert spans == ["model", "rerank", "retrieval"] or \
        spans == ["rerank", "retrieval"]
    ret = next(e for e in capture if e["span"] == "retrieval")
    assert ret["workspace_id"] == w and ret["ok"] is True
    rr = next(e for e in capture if e["span"] == "rerank")
    assert rr["ok"] is True and rr["input_chars"] > 0


def test_run_agent_records_turn_span(db, capture, monkeypatch):
    from types import SimpleNamespace

    import app.agents.runtime.langgraph_runner as LR

    _, w, kb = _seed_ws(db)

    def fake_invoke(state, config=None):
        assert config["configurable"]["deps"] is not None
        return {"answer": "ok"}

    monkeypatch.setattr(LR, "_COMPILED", SimpleNamespace(invoke=fake_invoke))
    from app.agents.runtime.langgraph_runner import RuntimeDeps

    out = LR.run_agent({"workspace_id": str(w), "user_id": "u1",
                        "agent_key": "k", "agent_id": "a",
                        "message": "hi"},
                       RuntimeDeps(db=db, model="gemini-2.5-flash"))
    assert out == {"answer": "ok"}
    assert len(capture) == 1
    ev = capture[0]
    assert ev["span"] == "agent" and ev["workspace_id"] == w
    assert ev["user_id"] == "u1" and ev["model"] == "gemini-2.5-flash"
    assert ev["trace_id"] and ev["ok"] is True


def test_run_agent_failure_records_error(db, capture, monkeypatch):
    from types import SimpleNamespace

    import app.agents.runtime.langgraph_runner as LR

    _, w, _ = _seed_ws(db)

    def boom(state, config=None):
        raise RuntimeError("graph down")

    monkeypatch.setattr(LR, "_COMPILED", SimpleNamespace(invoke=boom))
    from app.agents.runtime.langgraph_runner import RuntimeDeps

    with pytest.raises(RuntimeError):
        LR.run_agent({"workspace_id": str(w), "user_id": "u1"},
                     RuntimeDeps(db=db))
    assert capture[0]["ok"] is False and "graph down" in capture[0]["error"]


def test_ingest_records_extraction_event(db, capture, monkeypatch):
    import workers.tasks as T
    from app.core.config import get_settings
    from app.knowledge.parsers.base import ParsedDocument, ParsedPage

    monkeypatch.setattr(get_settings(), "DOCUMENT_VISION_ENABLED", False)
    _, w, kb = _seed_ws(db)

    class FakeStorage:
        def __init__(self):
            self.objects = {}

        def put(self, key, data, content_type=""):
            self.objects[key] = data
            return key

        def get(self, key):
            return self.objects[key]

    st = FakeStorage()
    from workers.tasks import run_ingest

    src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, type="pdf",
                 filename="s.pdf", storage_key="k", status="pending")
    db.add(src)
    db.commit()
    st.put("k", b"%PDF-1.4 fake")
    doc = ParsedDocument(title="s", pages=[ParsedPage(page_no=1, text="hi")])
    doc.parse_meta = {"parser": "gemini-structured", "model": "gemini-2.5-flash",
                      "usage": {"model": "gemini-2.5-flash", "calls": 2,
                                "prompt_tokens": 100, "completion_tokens": 40,
                                "total_tokens": 140}}
    monkeypatch.setattr(T, "parse_source", lambda *a, **k: doc)
    res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                     embed_fn=lambda texts: [[0.01] * 1536 for _ in texts])
    assert res["ok"] is True
    ext = [e for e in capture if e["span"] == "extraction"]
    assert len(ext) == 1
    assert ext[0]["total_tokens"] == 140
    assert ext[0]["model"] == "gemini-2.5-flash"
    assert ext[0]["workspace_id"] == w
    assert ext[0]["meta"]["parser"] == "gemini-structured"


# ---------- endpoints ----------

def _client_with(db):
    from fastapi.testclient import TestClient

    from app.core.database import get_db
    from app.main import app

    def override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def apiclient(db):
    yield from _client_with(db)


def _login(c, email):
    return c.post("/auth/login",
                  json={"email": email, "password": "password123"}).json()["access_token"]


def test_usage_endpoints_gating(db, apiclient):
    from app.workspaces.models import WorkspaceMember as WM

    pw = hash_password("password123")
    adm = User(id=uuid.uuid4(), email="adm@x.com", password_hash=pw,
               full_name="A", is_admin=True)
    own = User(id=uuid.uuid4(), email="o@x.com", password_hash=pw, full_name="O")
    mem = User(id=uuid.uuid4(), email="m@x.com", password_hash=pw, full_name="M")
    db.add_all([adm, own, mem])
    w = uuid.uuid4()
    db.add(Workspace(id=w, name="W", type="shared", owner_user_id=own.id))
    db.add(WM(workspace_id=w, user_id=own.id, role="owner"))
    db.add(WM(workspace_id=w, user_id=mem.id, role="member"))
    db.commit()
    _row(db, workspace_id=w, user_id=own.id)
    ta, to, tm = (_login(apiclient, e) for e in
                  ("adm@x.com", "o@x.com", "m@x.com"))

    def get(url, t):
        return apiclient.get(url, headers={"Authorization": f"Bearer {t}"})

    r = get("/admin/usage/summary", ta)
    assert r.status_code == 200, r.text
    assert r.json()["totals"]["events"] == 1
    assert get("/admin/usage/summary", to).status_code in (401, 403)
    r2 = get(f"/workspaces/{w}/usage/summary", to)
    assert r2.status_code == 200, r2.text
    assert r2.json()["totals"]["events"] == 1
    assert get(f"/workspaces/{w}/usage/summary", tm).status_code in (401, 403)
    outsider = User(id=uuid.uuid4(), email="z@x.com", password_hash=pw)
    db.add(outsider)
    db.commit()
    tz = _login(apiclient, "z@x.com")
    assert get(f"/workspaces/{w}/usage/summary", tz).status_code == 404
