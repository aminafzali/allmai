"""KB file tools: complete inventory, named-file full text, intents."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.agents import kb_files as kf
from app.common.base import Base
from app.knowledge.models import Chunk, Document, KnowledgeBase, PageSegment, Source
from app.workspaces.models import Workspace

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


def _ws_kb(db, scope="workspace", ws_id=None, title="KB"):
    ws_id = ws_id or uuid.uuid4()
    if scope == "workspace":
        db.add(Workspace(id=ws_id, name="W", type="shared",
                         owner_user_id=uuid.uuid4()))
        db.flush()
    kb = KnowledgeBase(id=uuid.uuid4(),
                       workspace_id=None if scope == "global" else ws_id,
                       title=title, scope=scope)
    db.add(kb)
    db.commit()
    return ws_id, kb


def _src(db, ws_id, kb_id, filename, texts, global_=False):
    src = Source(id=uuid.uuid4(), workspace_id=None if global_ else ws_id,
                 kb_id=kb_id, type="note", filename=filename, status="ready")
    db.add(src)
    db.flush()
    doc = Document(id=uuid.uuid4(), workspace_id=None if global_ else ws_id,
                   source_id=src.id, title=filename)
    db.add(doc)
    db.flush()
    for i, t in enumerate(texts):
        seg = PageSegment(id=uuid.uuid4(), workspace_id=None if global_ else ws_id,
                          document_id=doc.id, page_no=i + 1, text=t)
        db.add(seg)
        db.flush()
        db.add(Chunk(id=uuid.uuid4(), workspace_id=None if global_ else ws_id,
                     kb_id=kb_id, segment_id=seg.id, content=t, tokens=3,
                     embedding=[1.0, 0.0], chunk_metadata={}))
    db.commit()
    return src


def test_wants_file_list():
    assert kf.wants_file_list("چه فایلهایی در این پایگاه دانش دارم؟")
    assert kf.wants_file_list("لیست فایل‌ها را بگو")
    assert kf.wants_file_list("list all files please")
    # user's exact reported phrasings (regression)
    assert kf.wants_file_list("فایلهای این پایگاه دانش اسماشون بگو")
    assert kf.wants_file_list("نام ببر چه فایلهایی داری")
    # bare name-asking without file context must NOT trigger
    assert not kf.wants_file_list("اسماشون بگو")
    assert not kf.wants_file_list("اسیدها را توضیح بده")
    assert not kf.wants_file_list("Executive Summary را ترجمه کن")


def test_wants_verbatim():
    assert kf.wants_verbatim("عین متن Executive Summary را بفرست")
    assert kf.wants_verbatim("متن کامل فایل را بده")
    assert kf.wants_verbatim("send me the exact text please")
    assert not kf.wants_verbatim("این فایل را خلاصه کن")
    assert not kf.wants_verbatim("سلام چطوری")


def test_build_verbatim_answer(db):
    wid, kb = _ws_kb(db)
    _src(db, wid, kb.id, "Executive Summary.docx", ["الف. ", "ب. "])
    out = kf.build_verbatim_answer(db, wid, [kb.id], [], "عین متن Executive Summary را بفرست")
    assert out is not None
    assert out["answer"].startswith("متن کامل «Executive Summary.docx»")
    assert "الف." in out["answer"] and "ب." in out["answer"]
    assert out["truncated"] is False
    assert kf.build_verbatim_answer(db, wid, [kb.id], [], "سلام") is None
    assert kf.build_verbatim_answer(db, wid, [kb.id], [], "متن کامل فایل ناموجود") is None


def test_build_verbatim_truncates(db):
    wid, kb = _ws_kb(db)
    _src(db, wid, kb.id, "big.pdf", ["کلمه " * 20000])
    out = kf.build_verbatim_answer(db, wid, [kb.id], [], "کل متن big را بفرست")
    assert out is not None and out["truncated"] is True
    assert len(out["answer"]) <= 15000 + 200


def test_service_verbatim_scoping(db):
    from app.agents import service as svc

    wid, kb = _ws_kb(db)
    _src(db, wid, kb.id, "a.pdf", ["متن"])
    other_kb = _ws_kb(db, title="KB2")[1]
    agent = type("A", (), {"id": uuid.uuid4()})()
    ok = svc._verbatim_answer(db, type("W", (), {"id": wid})(), agent,
                              None, kb.id, "متن کامل a.pdf را بفرست")
    assert ok is not None and "متن" in ok["answer"]
    scoped = svc._verbatim_answer(db, type("W", (), {"id": wid})(), agent,
                                  None, other_kb.id, "متن کامل a.pdf را بفرست")
    assert scoped is None


def test_translate_flow_pieces_and_intents():
    from app.agents.translate_flow import (
        is_continue,
        is_translate_request,
        split_pieces,
        translate_prompt,
    )

    assert is_translate_request("این را ترجمه کن")
    assert is_translate_request("translate this please")
    assert not is_translate_request("خلاصه کن")
    assert not is_translate_request("سلام")
    assert is_continue("ادامه بده")
    assert is_continue("continue")
    assert not is_continue("ادامه بده لطفا کل متن را دوباره بخوان چون طولانی است و نیاز به بررسی دارد")
    assert not is_continue("سلام")
    paras = [f"پاراگراف {i} " + "متن " * 400 for i in range(5)]
    pieces = split_pieces("\n\n".join(paras))
    assert len(pieces) >= 2
    assert all(len(p) <= 10000 + 1 for p in pieces)
    assert split_pieces("") == []
    assert split_pieces("کوتاه") == ["کوتاه"]
    assert "بخش 2 از 5" in translate_prompt("x", 2, 5, "f.pdf")


def test_translate_turn_start_and_continue(db, monkeypatch):
    from app.agents import service as svc
    from app.agents.models import Agent
    from app.conversations.models import Conversation
    from app.users.models import User
    from app.workspaces.models import Workspace

    u = User(id=uuid.uuid4(), email="t@x.com",
             password_hash="x")
    w = Workspace(id=uuid.uuid4(), name="W", type="shared", owner_user_id=u.id)
    a = Agent(id=uuid.uuid4(), workspace_id=w.id, key="k", type="agent")
    db.add_all([u, w, a])
    db.commit()
    from app.knowledge.models import KnowledgeBase

    kb = KnowledgeBase(id=uuid.uuid4(), workspace_id=w.id, title="TKB",
                       scope="workspace")
    db.add(kb)
    db.commit()
    long_text = "جمله آزمایشی برای ترجمه. " * 800  # ~20k chars
    _src(db, w.id, kb.id, "long.pdf", [long_text])
    conv = Conversation(id=uuid.uuid4(), workspace_id=w.id, agent_id=a.id,
                        user_id=u.id, state={})
    db.add(conv)
    db.commit()

    calls = []

    class FakeProvider:
        def generate(self, prompt, **kw):
            calls.append((prompt, kw))
            return "ترجمه بخش"

    monkeypatch.setattr(svc, "_provider_for", lambda cfg: FakeProvider())
    ws = type("W", (), {"id": w.id})()
    out = svc._translate_turn(db, ws, a, None, kb.id,
                              "long.pdf را ترجمه کن", conv,
                              {"provider": "x"})
    assert out is not None
    answer, remaining = out
    assert answer.startswith("ترجمه بخش") and "بخش ۱ از" in answer
    assert remaining > 0
    assert calls and calls[0][1].get("max_tokens") == 4000
    # continue serves the next piece
    out2 = svc._translate_turn(db, ws, a, None, kb.id, "ادامه بده", conv,
                               {"provider": "x"})
    assert out2 is not None
    assert out2[1] == remaining - 1
    # short text never enters chunked mode
    out3 = svc._translate_turn(db, ws, a, None, kb.id, "سلام", conv,
                               {"provider": "x"})
    assert out3 is None


def test_translate_uses_effective_generate_fn(db, monkeypatch):
    """The turn's effective generate_fn wins over chat_cfg provider."""
    from app.agents import service as svc
    from app.agents.models import Agent
    from app.conversations.models import Conversation
    from app.users.models import User
    from app.workspaces.models import Workspace

    u = User(id=uuid.uuid4(), email="g@x.com", password_hash="x")
    w = Workspace(id=uuid.uuid4(), name="W", type="shared", owner_user_id=u.id)
    a = Agent(id=uuid.uuid4(), workspace_id=w.id, key="k", type="agent")
    db.add_all([u, w, a])
    db.commit()
    from app.knowledge.models import KnowledgeBase

    kb = KnowledgeBase(id=uuid.uuid4(), workspace_id=w.id, title="K",
                       scope="workspace")
    db.add(kb)
    db.commit()
    _src(db, w.id, kb.id, "long.pdf", ["متن طولانی. " * 2000])
    conv = Conversation(id=uuid.uuid4(), workspace_id=w.id, agent_id=a.id,
                        user_id=u.id, state={})
    db.add(conv)
    db.commit()

    def boom(cfg):
        raise AssertionError("_provider_for must not be consulted")

    monkeypatch.setattr(svc, "_provider_for", boom)
    ws = type("W", (), {"id": w.id})()
    out = svc._translate_turn(
        db, ws, a, None, kb.id, "long.pdf را ترجمه کن", conv, {},
        generate_fn=lambda prompt, **kw: "ترجمه دستی")
    assert out is not None and out[0].startswith("ترجمه دستی")


def test_list_complete_workspace_and_global(db):
    wid, kb = _ws_kb(db)
    _src(db, wid, kb.id, "a.pdf", ["x"])
    _src(db, wid, kb.id, "b.pdf", ["y"])
    gkb_id = _ws_kb(db, scope="global")[1].id
    _src(db, None, gkb_id, "g.pdf", ["z"], global_=True)
    mine = kf.list_kb_files(db, wid, [kb.id], [])
    assert sorted(f["filename"] for f in mine) == ["a.pdf", "b.pdf"]
    both = kf.list_kb_files(db, wid, [kb.id], [gkb_id])
    assert sorted(f["filename"] for f in both) == ["a.pdf", "b.pdf", "g.pdf"]
    # unscoped (no kb at all): whole workspace, never global rows
    all_ws = kf.list_kb_files(db, wid, [], [])
    assert sorted(f["filename"] for f in all_ws) == ["a.pdf", "b.pdf"]
    rec = both[0]
    assert {"id", "filename", "type", "status", "size_bytes"} <= set(rec)


def test_find_named_file():
    files = [{"filename": "Executive Summary.docx"},
             {"filename": "تئوری شناخت.pdf"}, {"filename": "n.pdf"}]
    assert kf.find_named_file(files, "Executive Summary متنش رو ترجمه کن")[
        "filename"] == "Executive Summary.docx"
    assert kf.find_named_file(files, "تئوری شناخت چیست؟")[
        "filename"] == "تئوری شناخت.pdf"
    assert kf.find_named_file(files, "سلام چطوری") is None
    assert kf.find_named_file([], "anything") is None


def test_read_named_file_full_text(db):
    wid, kb = _ws_kb(db)
    _src(db, wid, kb.id, "Executive Summary.docx",
         ["part one. ", "part two. "])
    out = kf.read_named_file(db, wid, [kb.id], [], "Executive Summary ترجمه")
    assert out is not None and out["filename"] == "Executive Summary.docx"
    assert "part one." in out["text"] and "part two." in out["text"]
    assert out["truncated"] is False
    assert kf.read_named_file(db, wid, [kb.id], [], "سلام") is None


def test_read_truncates_long(db):
    wid, kb = _ws_kb(db)
    big = "کلمه " * 20000
    _src(db, wid, kb.id, "big.pdf", [big])
    out = kf.read_named_file(db, wid, [kb.id], [], "big چیست")
    assert out is not None and out["truncated"] is True
    assert len(out["text"]) <= 30050


def test_node_wires_inventory_and_file_text(db):
    from app.agents.runtime import langgraph_runner as R

    wid, kb = _ws_kb(db)
    _src(db, wid, kb.id, "a.pdf", ["متن درباره اسیدها"])
    deps = type("D", (), {"db": db, "generate_fn": None})()
    deps.embed_fn = lambda t: [[1.0, 0.0] for _ in t]

    def state(msg, tools):
        return {"workspace_id": str(wid), "user_id": str(uuid.uuid4()),
                "agent_key": "k", "agent_id": str(uuid.uuid4()),
                "definition_id": None, "spec": {"goal": "g", "tools": tools},
                "instructions": "", "agent_state": {}, "conv_state": {},
                "instance_kb_ids": [str(kb.id)], "message": msg, "kb_id": None,
                "history": [], "facts": [], "memory_hits": [], "summary": ""}

    out = R.node_retrieve_knowledge(
        state("چه فایلهایی دارم؟", ["knowledge_search"]), deps)
    assert [f["filename"] for f in out["file_inventory"]] == ["a.pdf"]
    assert out["file_text"] is None

    out2 = R.node_retrieve_knowledge(
        state("a.pdf را خلاصه کن", ["knowledge_search"]), deps)
    assert out2["file_inventory"] == []
    assert out2["file_text"] is not None
    assert out2["file_text"]["filename"] == "a.pdf"


def test_node_picked_kb_wins_over_instance_kbs(db):
    """Locked scoping: with a picked kb_id, deterministic file tools must
    not see the agent's other (instance-assigned) KBs."""
    from app.agents.runtime import langgraph_runner as R

    wid, kb_a = _ws_kb(db, title="A")
    kb_b = KnowledgeBase(id=uuid.uuid4(), workspace_id=wid, title="B",
                         scope="workspace")
    db.add(kb_b)
    db.commit()
    _src(db, wid, kb_a.id, "a.pdf", ["متن الف"])
    _src(db, wid, kb_b.id, "b.pdf", ["متن ب"])
    deps = type("D", (), {"db": db, "generate_fn": None})()
    deps.embed_fn = lambda t: [[1.0, 0.0] for _ in t]

    def state(msg, kb_id):
        return {"workspace_id": str(wid), "user_id": str(uuid.uuid4()),
                "agent_key": "k", "agent_id": str(uuid.uuid4()),
                "definition_id": None, "spec": {"goal": "g", "tools": ["knowledge_search"]},
                "instructions": "", "agent_state": {}, "conv_state": {},
                "instance_kb_ids": [str(kb_a.id), str(kb_b.id)],
                "message": msg, "kb_id": kb_id,
                "history": [], "facts": [], "memory_hits": [], "summary": ""}

    out = R.node_retrieve_knowledge(
        state("چه فایلهایی دارم؟", str(kb_a.id)), deps)
    assert [f["filename"] for f in out["file_inventory"]] == ["a.pdf"]

    out2 = R.node_retrieve_knowledge(
        state("b.pdf را خلاصه کن", str(kb_a.id)), deps)
    assert out2["file_text"] is None  # b.pdf lives in the other KB

    out3 = R.node_retrieve_knowledge(
        state("b.pdf را خلاصه کن", str(kb_b.id)), deps)
    assert out3["file_text"] is not None
    assert out3["file_text"]["filename"] == "b.pdf"


def test_prompt_renders_file_sections():
    from app.agents.runtime.langgraph_runner import node_build_prompt

    s = {"message": "q", "instructions": "", "spec": {"goal": "g"},
         "agent_state": {}, "conv_state": {}, "summary": "", "facts": [],
         "memory_hits": [], "history": [],
         "chunks": [{"content": "c", "chunk_id": "1", "source": "a.pdf",
                     "page_no": 1, "start_ms": None, "end_ms": None,
                     "score": 0.9}],
         "workspace_chunks": [], "global_chunks": [], "excel_chunks": [],
         "web_chunks": [], "maps_chunks": [], "fulltext": None,
         "file_inventory": [{"filename": "a.pdf", "type": "pdf",
                             "status": "ready"}],
         "file_text": {"filename": "a.pdf", "type": "pdf", "text": "FULL",
                       "truncated": False}}
    prompt = node_build_prompt(s, None)["prompt"]
    assert "COMPLETE inventory" in prompt and "a.pdf (pdf, ready)" in prompt
    assert "TASK (do exactly this now" in prompt and "FULL" in prompt
    assert "OWN uploaded files" in prompt  # anti-evasion line


def _empty_state(tools):
    return {"message": "q", "instructions": "",
            "spec": {"goal": "g", "tools": tools},
            "agent_state": {}, "conv_state": {}, "summary": "", "facts": [],
            "memory_hits": [], "history": [],
            "chunks": [], "workspace_chunks": [], "global_chunks": [],
            "excel_chunks": [], "web_chunks": [], "maps_chunks": [],
            "fulltext": None, "file_inventory": [], "file_text": None,
            "searched_kbs": [{"title": "KB A", "scope": "workspace"},
                             {"title": "G", "scope": "global"}]}


def test_prompt_smart_empty_names_kbs():
    from app.agents.runtime.langgraph_runner import node_build_prompt

    prompt = node_build_prompt(_empty_state(["knowledge_search"]), None)["prompt"]
    assert "NOTHING" in prompt and "KB A" in prompt and "G" in prompt
    assert "access limitations" in prompt


def test_prompt_empty_without_knowledge_tools_unchanged():
    from app.agents.runtime.langgraph_runner import node_build_prompt

    prompt = node_build_prompt(_empty_state(["memory_search"]), None)["prompt"]
    assert "NOTHING" not in prompt
    assert "User: q" in prompt


def test_rewrite_queries_parses_and_failopens(monkeypatch):
    from app.agents import query_rewrite as qr

    class FakeProvider:
        def __init__(self, text):
            self.text = text

        def generate(self, prompt, **kw):
            assert "JSON list" in prompt
            return self.text

    import app.ai.openai_compat as oc

    monkeypatch.setattr(oc, "OpenAICompatProvider",
                        lambda: FakeProvider('["q1", "q1", "q2"]'))
    assert qr.rewrite_queries("hi?") == ["q1", "q2"]
    monkeypatch.setattr(oc, "OpenAICompatProvider",
                        lambda: FakeProvider("no json here"))
    assert qr.rewrite_queries("hi?") == []

    def boom():
        raise RuntimeError("down")

    monkeypatch.setattr(oc, "OpenAICompatProvider", boom)
    assert qr.rewrite_queries("hi?") == []
    assert qr.rewrite_queries("") == []


def _rstate(msg, tools, wsid, kbids):
    return {"workspace_id": str(wsid), "user_id": str(uuid.uuid4()),
            "agent_key": "k", "agent_id": str(uuid.uuid4()),
            "definition_id": None, "spec": {"goal": "g", "tools": tools},
            "instructions": "", "agent_state": {}, "conv_state": {},
            "instance_kb_ids": [str(k) for k in kbids], "message": msg,
            "kb_id": None, "history": [], "facts": [], "memory_hits": [],
            "summary": ""}


def _rdeps(db):
    deps = type("D", (), {"db": db, "generate_fn": None})()
    deps.embed_fn = lambda t: [[1.0, 0.0] for _ in t]
    return deps


def test_rewrite_expands_thin_results(db, monkeypatch):
    import app.agents.query_rewrite as qr
    import app.agents.runtime.langgraph_runner as R

    wid, kb = _ws_kb(db)
    _src(db, wid, kb.id, "a.pdf", ["متن درباره اسیدها"])
    calls = []

    def fake_ks(ctx, q, kb_id=None, top_k=4):
        calls.append(q)
        if q == "سوال کوتاه":
            return []
        return [{"content": "c", "chunk_id": "x", "source": "a.pdf",
                 "source_id": "s", "doc_title": "", "page_no": None,
                 "start_ms": None, "end_ms": None, "score": 0.5}]

    monkeypatch.setattr(R, "knowledge_search", fake_ks)
    monkeypatch.setattr(qr, "rewrite_queries", lambda m, h=None: ["q2"])
    out = R.node_retrieve_knowledge(
        _rstate("سوال کوتاه", ["knowledge_search"], wid, [kb.id]),
        _rdeps(db))
    assert calls[0] == "سوال کوتاه" and "q2" in calls
    assert any(c["chunk_id"] == "x" for c in out["chunks"])


def test_rewrite_skipped_when_hits_enough(db, monkeypatch):
    import app.agents.query_rewrite as qr
    import app.agents.runtime.langgraph_runner as R

    wid, kb = _ws_kb(db)
    _src(db, wid, kb.id, "a.pdf", ["متن"])
    called = []

    def fake_ks(ctx, q, kb_id=None, top_k=4):
        return [{"content": "c", "chunk_id": f"x{i}", "source": "a.pdf",
                 "source_id": "s", "doc_title": "", "page_no": None,
                 "start_ms": None, "end_ms": None, "score": 0.5}
                for i in range(3)]

    monkeypatch.setattr(R, "knowledge_search", fake_ks)
    monkeypatch.setattr(qr, "rewrite_queries",
                        lambda m, h=None: called.append(m) or ["q2"])
    R.node_retrieve_knowledge(
        _rstate("سوال", ["knowledge_search"], wid, [kb.id]), _rdeps(db))
    assert called == []


def test_ambiguity_and_suggestions(db, monkeypatch):
    import app.agents.query_rewrite as qr
    import app.agents.runtime.langgraph_runner as R

    wid, kb = _ws_kb(db)
    _src(db, wid, kb.id, "فروش 1403.pdf", ["متن یک"])
    _src(db, wid, kb.id, "فروش 1404.pdf", ["متن دو"])
    monkeypatch.setattr(R, "knowledge_search",
                        lambda ctx, q, kb_id=None, top_k=4: [])
    monkeypatch.setattr(qr, "rewrite_queries", lambda m, h=None: [])
    out = R.node_retrieve_knowledge(
        _rstate("اون فایل فروش", ["knowledge_search"], wid, [kb.id]),
        _rdeps(db))
    assert out["file_text"] is None
    assert sorted(f["filename"] for f in out["file_ambiguity"]) == [
        "فروش 1403.pdf", "فروش 1404.pdf"]
    pr = R.node_build_prompt({**out, "message": "اون فایل فروش",
                              "instructions": "",
                              "spec": {"goal": "g",
                                       "tools": ["knowledge_search"]},
                              "agent_state": {}, "conv_state": {},
                              "summary": "", "facts": [], "memory_hits": [],
                              "history": []}, None)["prompt"]
    assert "ambiguously" in pr and "1403" in pr

    out2 = R.node_retrieve_knowledge(
        _rstate("سوال کاملا نامرتبط xyz", ["knowledge_search"], wid, [kb.id]),
        _rdeps(db))
    assert out2["file_ambiguity"] == [] and out2["file_text"] is None
    assert len(out2["suggested"]) == 3
    assert "1403" in out2["suggested"][0]


def test_ambiguity_options_and_question():
    files = [{"filename": "گزارش فروش 1403", "type": "note"},
             {"filename": "گزارش فروش 1404", "type": "note"},
             {"filename": "دیگر", "type": "note"}]
    opts = kf.ambiguity_options(files, "اون فایل فروش")
    assert opts is not None
    assert sorted(o["filename"] for o in opts) == [
        "گزارش فروش 1403", "گزارش فروش 1404"]
    q = kf.ambiguity_question(opts)
    assert "کدام فایل" in q and "1." in q and "2." in q
    # clear winner -> None
    assert kf.ambiguity_options(files, "گزارش 1403 دقیق") is None
    # no match -> None
    assert kf.ambiguity_options(files, "سلام") is None
    # same-name duplicates collapse to one -> None (read path instead)
    dupes = [files[0], dict(files[0])]
    assert kf.ambiguity_options(dupes, "فروش 1403") is None


def test_service_ambiguity_shortcircuit(db):
    from app.agents import service as svc

    wid, kb = _ws_kb(db)
    _src(db, wid, kb.id, "گزارش فروش 1403", ["متن یک"])
    _src(db, wid, kb.id, "گزارش فروش 1404", ["متن دو"])
    agent = type("A", (), {"id": uuid.uuid4()})()
    ws = type("W", (), {"id": wid})()
    out = svc._ambiguity_answer(db, ws, agent, None, kb.id, "اون فایل فروش")
    assert out is not None and "کدام فایل" in out and "1404" in out
    assert svc._ambiguity_answer(db, ws, agent, None, kb.id,
                                 "گزارش 1403 دقیق") is None
    # content question sharing a token: must NOT hijack
    assert svc._ambiguity_answer(db, ws, agent, None, kb.id,
                                 "فروش خوب بود؟") is None
