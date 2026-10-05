"""Agent search tools: provider chains, client-tool specs/routing,
lead persistence/export, and the global-KB toggle.

Server-side web/maps execution is INTENTIONALLY absent: searches run in
the user's browser (client flow); these tests pin that contract plus the
swappable provider architecture.
"""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 (registers Lead in metadata)
from app.agents import leads as leads_mod
from app.agents.client_tools import (
    MAX_CLIENT_ROUNDS,
    specs_for,
    validate_call,
)
from app.agents.runtime.langgraph_runner import (
    node_build_prompt,
    node_retrieve_knowledge,
)
from app.agents.tools import TOOLS
from app.common.base import Base

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


class _Deps:
    def __init__(self):
        self.db = object()
        self.embed_fn = lambda texts: [[0.0] for _ in texts]  # noqa: E731
        self.generate_fn = None


def _state(message, tools, definition_id=None):
    return {
        "workspace_id": "ws", "user_id": "u", "agent_key": "k",
        "agent_id": "a", "definition_id": definition_id,
        "spec": {"goal": "Help.", "tools": list(tools)},
        "instructions": "", "agent_state": {}, "conv_state": {},
        "instance_kb_ids": [], "message": message, "kb_id": None,
        "history": [], "facts": [], "memory_hits": [], "summary": "",
    }


def test_tools_registry_has_new_ids():
    for tid in ("global_knowledge_search", "web_search", "maps_search",
                "knowledge_search", "memory_search", "memory_facts",
                "excel_query"):
        assert callable(TOOLS[tid]), tid


def test_client_specs_and_validation():
    specs = specs_for(["web_search", "maps_search", "knowledge_search"])
    assert [s["function"]["name"] for s in specs] == ["web_search", "maps_search"]
    assert specs_for([]) == []
    good = validate_call("maps_search", {"query": "  رستوران تهران  "})
    assert good == {"name": "maps_search",
                    "arguments": {"query": "رستوران تهران"}}
    assert validate_call("nope", {"query": "x"}) is None
    assert validate_call("web_search", {"query": "   "}) is None
    assert validate_call("web_search", "not-a-dict") is None
    assert MAX_CLIENT_ROUNDS == 2


def test_request_client_calls_single_and_failopen(monkeypatch):
    from app.agents import client_tools as ct

    def fake_ok(self, messages, tools, model=None, **kw):
        assert model == "m"
        return {"text": "", "calls": [
            {"id": "1", "name": "maps_search", "arguments": {"query": "q1"}},
            {"id": "2", "name": "web_search", "arguments": {"query": "q2"}},
        ]}

    monkeypatch.setattr("app.ai.openai_compat.OpenAICompatProvider.generate_with_tools",
                        fake_ok)
    out = ct.request_client_calls("hi", [], ["web_search", "maps_search"], "m")
    assert out == [{"name": "maps_search", "arguments": {"query": "q1"}}]  # max one

    def fake_boom(self, messages, tools, model=None, **kw):
        raise RuntimeError("down")

    monkeypatch.setattr("app.ai.openai_compat.OpenAICompatProvider.generate_with_tools",
                        fake_boom)
    assert ct.request_client_calls("hi", [], ["web_search"], "m") == []
    assert ct.request_client_calls("hi", [], [], "m") == []


def test_validate_places():
    from app.ai.places_providers import validate_place_results

    assert validate_place_results("nope") == []
    out = validate_place_results([
        {"name": "R", "address": "A", "phone": "021", "website": "https://r.test",
         "lat": "35.7", "lng": 51.4, "extra_tag": "x" * 900,
         "maps_uri": "javascript:alert(1)"},
        {"name": "", "address": "ghost"},
        "junk",
        {"name": "Bad", "website": "ftp://f.test/x"},
    ])
    assert [o["name"] for o in out] == ["R", "Bad"]
    assert out[0]["lat"] == 35.7 and out[0]["lng"] == 51.4
    assert out[0]["maps_uri"] == ""  # non-http(s) dropped
    assert out[1]["website"] == ""
    assert out[0]["raw"]["extra_tag"].startswith("x")  # bounded raw kept


def test_validate_web():
    from app.ai.web_providers import validate_web_results

    assert validate_web_results(None) == []
    out = validate_web_results([
        {"title": "T", "uri": "https://t.test/a", "snippet": "s"},
        {"title": "X", "uri": "notaurl", "snippet": ""},
    ])
    assert out[0] == {"title": "T", "uri": "https://t.test/a", "snippet": "s"}
    assert out[1]["uri"] == ""


def test_chain_fallback_order():
    from app.ai.places_providers import PlaceResult, ProviderChain

    class _P:
        def __init__(self, name, out=None, boom=False):
            self.name, self.side, self._out, self._boom = name, "server", out, boom

        def search(self, query, max_results=6, timeout=30.0):
            if self._boom:
                raise RuntimeError("down")
            return self._out or []

    chain = ProviderChain([_P("a"), _P("b", [PlaceResult(name="R")]), _P("c")])
    out, served = chain.run("q")
    assert served == "b" and out[0].name == "R"
    assert ProviderChain([_P("a"), _P("b", boom=True)]).run("q") == ([], "")
    # client-side links never run here
    assert ProviderChain([_P("x")]).run("q") == ([], "")
    chain.providers[0].side = "client"
    out, served = ProviderChain(chain.providers).run("q")
    assert served == "b"


def test_default_chains_are_client_only(monkeypatch):
    import app.ai.places_providers as pp
    import app.ai.web_providers as wp

    def settings(side="client", web="ddg", places="overpass"):
        return type("S", (), {"WEB_SEARCH_PROVIDER": web, "WEB_SEARCH_FALLBACKS": "",
                              "WEB_SEARCH_SIDE": side, "PLACES_PROVIDER": places,
                              "PLACES_FALLBACKS": ""})()

    # providers resolve settings lazily from app.core.config
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings())
    assert pp.get_places_chain().providers == []
    assert wp.get_web_chain().providers == []
    # unknown names ignored, fail-open
    monkeypatch.setattr("app.core.config.get_settings",
                        lambda: settings(places="nope"))
    assert pp.get_places_chain().providers == []


def test_default_chains_serve():
    """Shipped defaults: GapGPT web (server) + overpass-server fallback."""
    import app.ai.places_providers as pp
    import app.ai.web_providers as wp

    assert [p.name for p in wp.get_web_chain().providers] == ["gapgpt", "ddg"]
    assert [p.name for p in pp.get_places_chain().providers] == ["overpass-server"]


def test_gapgpt_provider_parse(monkeypatch):
    import httpx as _httpx
    import app.ai.web_providers as wp

    def fake_post(url, headers=None, json=None, timeout=120.0):
        assert url.endswith("/responses")
        assert json["tools"] == [{"type": "web_search"}]

        class _R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"output": [
                    {"type": "openrouter:web_search", "action": {
                        "query": "q",
                        "sources": [{"url": "https://a.test", "title": "A"}]}},
                    {"type": "message", "content": [
                        {"type": "output_text", "text": "answer!",
                         "annotations": [
                             {"type": "url_citation", "url": "https://b.test",
                              "title": "B", "content": "snip"}]}]},
                ]}

        return _R()

    monkeypatch.setattr(_httpx, "post", fake_post)
    monkeypatch.setattr("app.ai.gapgpt_search.get_settings",
                        lambda: type("S", (), {
                            "OPENAI_COMPAT_BASE_URL": "https://x.test/v1",
                            "OPENAI_COMPAT_API_KEY": "k",
                            "GAPGPT_SEARCH_MODEL": "m"})())
    out, served = wp.ProviderChain([wp.PROVIDERS["gapgpt"]]).run("q")
    assert served == "gapgpt"
    assert out[0].snippet == "answer!"
    assert {c.uri for c in out} >= {"https://a.test", "https://b.test"}


def test_overpass_server_search(monkeypatch):
    import httpx as _httpx
    import app.ai.places_providers as pp

    def fake_post(url, content=None, headers=None, timeout=60.0):
        import urllib.parse as _up

        assert "overpass" in url
        assert "around:" in _up.unquote(content.decode())

        class _R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"elements": [
                    {"type": "node", "id": 1, "lat": 30.28, "lon": 57.06,
                     "tags": {"name": "R", "phone": "+983400000000",
                              "addr:road": "خ ولیعصر"}},
                    {"type": "node", "id": 2, "lat": 30.29, "lon": 57.07,
                     "tags": {"name:fa": "کافه", "website": "https://k.test"}},
                    {"type": "node", "id": 3, "lat": 30.3, "lon": 57.08,
                     "tags": {}},
                ]}

        return _R()

    monkeypatch.setattr(_httpx, "post", fake_post)
    out, served = pp.ProviderChain([pp.PROVIDERS["overpass-server"]]).run(
        "رستوران کرمان")
    assert served == "overpass-server"
    assert [p.name for p in out] == ["R", "کافه"]
    assert out[0].phone == "+983400000000"
    assert out[0].address == "خ ولیعصر"
    assert out[1].website == "https://k.test"


def test_retrieve_never_searches_externally(monkeypatch):
    import app.agents.runtime.langgraph_runner as runner

    def _boom(*a, **k):
        raise AssertionError("must not run server-side search")

    monkeypatch.setattr(runner, "knowledge_search", lambda *a, **k: [])
    monkeypatch.setattr("app.agents.query_rewrite.rewrite_queries",
                        lambda m, h=None: [])
    out = node_retrieve_knowledge(
        _state("نزدیک‌ترین رستوران کجاست؟",
               ["knowledge_search", "web_search", "maps_search"]), _Deps())
    assert out["web_chunks"] == [] and out["maps_chunks"] == []
    assert out["chunks"] == []


def test_global_toggle_standalone(monkeypatch):
    import app.agents.runtime.langgraph_runner as runner

    seen = {}
    monkeypatch.setattr(runner, "_assigned_global_ids", lambda deps, s: ["g1"])
    monkeypatch.setattr(
        runner, "global_knowledge_search",
        lambda ctx, q, top_k=4: seen.update(kids=list(ctx.global_kb_ids)) or [{
            "content": "g", "chunk_id": "9", "source": "gf",
            "page_no": None, "start_ms": None, "end_ms": None, "score": 0.8}])
    out = node_retrieve_knowledge(
        _state("سلام", ["global_knowledge_search"], definition_id="d"),
        _Deps())
    assert seen["kids"] == ["g1"]
    assert out["global_chunks"] and out["chunks"]


def test_global_legacy_via_knowledge_search(monkeypatch):
    import app.agents.runtime.langgraph_runner as runner

    called = []
    monkeypatch.setattr(runner, "_assigned_global_ids", lambda deps, s: ["g1"])
    monkeypatch.setattr(runner, "knowledge_search",
                        lambda ctx, q, kb_id=None, top_k=4: [])
    monkeypatch.setattr(runner, "global_knowledge_search",
                        lambda ctx, q, top_k=4: called.append(1) or [])
    monkeypatch.setattr("app.agents.query_rewrite.rewrite_queries",
                        lambda m, h=None: [])
    node_retrieve_knowledge(
        _state("سلام", ["knowledge_search"], definition_id="d"), _Deps())
    assert called == [1]


def test_server_tool_impls_fail_open(monkeypatch):
    import app.agents.tools as tools_mod

    monkeypatch.setattr("app.ai.gemini.GeminiProvider.generate_grounded",
                        lambda self, q, **kw: (_ for _ in ()).throw(
                            RuntimeError("no key")))
    monkeypatch.setattr("app.ai.maps.search_places",
                        lambda q, **kw: (_ for _ in ()).throw(
                            RuntimeError("no key")))
    ctx = tools_mod.ToolContext(db=None, workspace_id=None, user_id=None)
    assert tools_mod.web_search(ctx, "q") == []
    assert tools_mod.maps_search(ctx, "q") == []


def test_grounded_payload_uses_google_search_tool(monkeypatch):
    from app.ai.gemini import GeminiProvider

    captured = {}

    def fake_post(self, path, payload, timeout=120.0):
        captured.update(payload)
        return {"candidates": [{
            "content": {"parts": [{"text": "پاسخ"}]},
            "groundingMetadata": {
                "webSearchQueries": ["q"],
                "groundingChunks": [
                    {"web": {"uri": "https://x.test/a", "title": "A"}}],
                "groundingSupports": [],
            }}]}

    monkeypatch.setattr(GeminiProvider, "_post", fake_post)
    monkeypatch.setattr("app.ai.gemini.get_settings",
                        lambda: type("S", (), {"WEB_SEARCH_MODEL": "m"})())
    p = GeminiProvider.__new__(GeminiProvider)
    res = p.generate_grounded("سوال؟")
    assert captured["tools"] == [{"google_search": {}}]
    assert res["text"] == "پاسخ"
    assert res["chunks"] == [{"title": "A", "uri": "https://x.test/a"}]


def test_maps_request_headers(monkeypatch):
    import httpx as _httpx
    import app.ai.maps as maps_mod

    captured = {}

    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"places": [{
                "displayName": {"text": "R"}, "formattedAddress": "Addr",
                "rating": 4.5, "userRatingCount": 10,
                "location": {"latitude": 35.7, "longitude": 51.4},
                "types": ["restaurant"],
                "googleMapsUri": "https://maps.test/r",
                "nationalPhoneNumber": "021"}]}

    def fake_post(url, headers=None, json=None, timeout=30.0):
        captured.update(headers or {})
        assert "searchText" in url
        return _R()

    monkeypatch.setattr(_httpx, "post", fake_post)
    monkeypatch.setattr("app.ai.maps.get_settings",
                        lambda: type("S", (), {"GOOGLE_MAPS_API_KEY": "k"})())
    out = maps_mod.search_places("رستوران")
    assert captured["X-Goog-Api-Key"] == "k"
    assert "places.displayName" in captured["X-Goog-FieldMask"]
    assert out[0]["name"] == "R" and out[0]["rating"] == 4.5


def test_prompt_has_web_maps_sections():
    s = _state("q", [])
    s.update({
        "chunks": [{"content": "w", "chunk_id": "web:answer",
                    "source": "Google Search", "page_no": None,
                    "start_ms": None, "end_ms": None, "score": 1.0}],
        "workspace_chunks": [], "global_chunks": [], "excel_chunks": [],
        "web_chunks": [{"content": "w", "chunk_id": "web:answer",
                        "source": "Google Search", "page_no": None,
                        "start_ms": None, "end_ms": None, "score": 1.0}],
        "maps_chunks": [{"content": "R\nAddr", "chunk_id": "maps:0",
                         "source": "R", "page_no": None,
                         "start_ms": None, "end_ms": None, "score": 0.95}],
    })
    prompt = node_build_prompt(s, None)["prompt"]
    assert "[S1]" in prompt and "[M1]" in prompt


def test_leads_save_list_export(db):
    from app.workspaces.models import Workspace

    ws_id = uuid.uuid4()
    db.add(Workspace(id=ws_id, name="W", type="shared",
                     owner_user_id=uuid.uuid4()))
    db.commit()
    rows = leads_mod.save_leads(
        db, ws_id, None, None, "رستوران تهران",
        [{"name": "R", "address": "A", "phone": "021", "hours": "",
          "website": "https://r.test", "lat": 35.7, "lng": 51.4,
          "raw": {"amenity": "restaurant"}},
         {"name": "", "address": "ghost"},
         "junk"],
        "overpass-de")
    assert len(rows) == 1
    assert rows[0].status == "new" and rows[0].source == "overpass-de"
    assert rows[0].raw == {"amenity": "restaurant"}
    listed = leads_mod.list_leads(db, ws_id)
    assert len(listed) == 1 and listed[0].name == "R"
    csv_bytes = leads_mod.leads_to_csv(listed)
    assert csv_bytes.startswith("\ufeff".encode("utf-8"))
    assert "R".encode("utf-8") in csv_bytes and b"021" in csv_bytes
    xlsx = leads_mod.leads_to_xlsx(listed)
    assert xlsx[:2] == b"PK"
    assert leads_mod.set_status(db, ws_id, [rows[0].id], "contacted") == 1
    assert leads_mod.list_leads(db, ws_id, status="contacted")[0].id == rows[0].id


def test_leads_conversation_filter(db):
    from app.workspaces.models import Workspace

    ws_id = uuid.uuid4()
    db.add(Workspace(id=ws_id, name="W", type="shared",
                     owner_user_id=uuid.uuid4()))
    db.commit()
    conv_a, conv_b = uuid.uuid4(), uuid.uuid4()
    base = {"address": "", "phone": "", "hours": "", "website": "",
            "lat": None, "lng": None, "raw": {}}
    leads_mod.save_leads(db, ws_id, None, conv_a, "q",
                         [{**base, "name": "A"}], "overpass-de")
    leads_mod.save_leads(db, ws_id, None, conv_b, "q",
                         [{**base, "name": "B"}], "overpass-de")
    assert len(leads_mod.list_leads(db, ws_id)) == 2
    only_a = leads_mod.list_leads(db, ws_id, conversation_id=conv_a)
    assert [r.name for r in only_a] == ["A"]
    csv_a = leads_mod.leads_to_csv(only_a)
    assert "A".encode("utf-8") in csv_a and "B".encode("utf-8") not in csv_a


def test_pause_server_first_for_web(monkeypatch):
    import app.agents.service as svc

    monkeypatch.setattr(
        "app.agents.client_tools.request_client_calls",
        lambda msg, hist, tools, model: [
            {"name": "web_search", "arguments": {"query": "q"}}])
    import app.ai.web_providers as wp

    monkeypatch.setattr(
        wp, "get_web_chain",
        lambda: wp.ProviderChain([wp.PROVIDERS["gapgpt"]]))
    monkeypatch.setattr(
        "app.ai.gapgpt_search.gapgpt_grounded_search",
        lambda q, **kw: {"text": "ans", "chunks": [
            {"title": "T", "uri": "https://t.test", "snippet": "s"}],
            "queries": []})
    conv = type("C", (), {"state": {}, "id": uuid.uuid4()})()
    events, stashed = svc._maybe_pause_for_client_tools(
        None, conv, [], None, {"tools": ["web_search"]}, "m", "q", None)
    assert events is None  # answered server-side: no browser round
    assert stashed and stashed[0]["chunk_id"] == "websrv:0"


def test_save_deduplicates(db):
    from app.agents import leads as leads_mod
    from app.workspaces.models import Workspace

    ws_id = uuid.uuid4()
    db.add(Workspace(id=ws_id, name="W", type="shared",
                     owner_user_id=uuid.uuid4()))
    db.commit()
    items = [{"name": "R", "address": "A", "phone": "1", "hours": "",
              "website": "", "lat": None, "lng": None, "raw": {}}]
    assert len(leads_mod.save_leads(db, ws_id, None, None, "q", items, "s")) == 1
    assert leads_mod.save_leads(db, ws_id, None, None, "q", items, "s") == []
    assert len(leads_mod.list_leads(db, ws_id)) == 1
