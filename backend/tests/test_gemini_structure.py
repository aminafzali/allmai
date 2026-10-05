"""Gemini structured PDF extraction tests (no network, no heavy models).

Covers (all hermetic via generate_fn/vision_fn seams + module fakes):
- batch prompt building ([PAGE n] markers, section hint)
- response splitting (markers, missing pages, fenced replies)
- markdown table blocks + section-chained elements with page numbers
- parser: text batches, vision pages, partial degradation, total failure,
  oversize refusal, usage accumulation
- dispatcher: Gemini-first routing, loud flat fallback, flag off, seams
- worker: ready ingest with usage in parse_meta, global sources, errors
- keepers: plain-text structuring, chunking invariants, flat-refill path,
  provider payload shapes, admin intake gating
"""

import io
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.common.base import Base
from app.core.security import hash_password
from app.knowledge.chunking import chunk_parsed
from app.knowledge.models import Chunk, KnowledgeBase, PageSegment, Source
from app.knowledge.parsers.base import ParsedDocument, ParsedElement, ParsedPage
from app.knowledge.parsers.multimodal import NullMultimodalProcessor
from app.users.models import User
from app.workspaces.models import Workspace, WorkspaceMember
from workers.tasks import run_ingest

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = TestingSession()
    try:
        yield s
    finally:
        s.close()


def _seed_ws(db):
    pw = hash_password("password123")
    u = User(id=uuid.uuid4(), email="u@x.com", password_hash=pw)
    db.add(u)
    w = uuid.uuid4()
    db.add(Workspace(id=w, name="W", type="shared", owner_user_id=u.id))
    db.add(WorkspaceMember(workspace_id=w, user_id=u.id, role="owner"))
    kb = KnowledgeBase(id=uuid.uuid4(), workspace_id=w, title="KB", scope="workspace")
    db.add(kb)
    db.commit()
    return u, w, kb


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes, content_type: str = "") -> str:
        self.objects[key] = data
        return key

    def get(self, key: str) -> bytes:
        return self.objects[key]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


def make_pdf(pages: list[str]) -> bytes:
    """Minimal valid 1.x PDF with extractable text (computed xref)."""
    objs: list[bytes] = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{4 + i} 0 R" for i in range(len(pages)))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, text in enumerate(pages):
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
            f"/Contents {4 + len(pages) + i} 0 R /Resources << /Font << /F1 3 0 R >> >> >>".encode()
        )
    for text in pages:
        stream = f"BT /F1 18 Tf 50 250 Td ({text}) Tj ET".encode("latin-1")
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF").encode()
    return bytes(out)


def _blank_pdf(n=2):
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(n):
        w.add_blank_page(200, 200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


# ---------- prompt / response units ----------

def test_build_struct_prompt_markers_and_hint():
    from app.knowledge.parsers.gemini_structure import build_struct_prompt

    p = build_struct_prompt([(1, "متن یک"), (2, "متن دو")], section="فصل ۳")
    assert "[PAGE 1]" in p and "[PAGE 2]" in p
    assert "متن یک" in p and "فصل ۳" in p  # section hint survives
    p2 = build_struct_prompt([(1, "x")])
    assert "[PAGE 1]" in p2


def test_split_structured_response_markers_missing_and_fenced():
    from app.knowledge.parsers.gemini_structure import split_structured_response

    out = split_structured_response(
        "[PAGE 1]\n## تیتر\nمتن\n[PAGE 2]\nپاراگراف", [1, 2, 3])
    assert out[1].startswith("## تیتر") and out[2] == "پاراگراف"
    assert out[3] == ""  # missing page -> flat fallback for exactly it
    fenced = split_structured_response(
        "```markdown\n[PAGE 1]\nسلام\n```", [1])
    assert fenced[1] == "سلام"
    assert split_structured_response("no markers here", [1]) == {1: ""}


def test_split_table_blocks_and_shape():
    from app.knowledge.parsers.gemini_structure import (
        _split_table_blocks,
        _table_shape,
    )

    parts = _split_table_blocks(
        "مقدمه\n\n| ماده | pH |\n| --- | --- |\n| لیمو | ۲ |\n\nنتیجه")
    assert [k for k, _ in parts] == ["text", "table", "text"]
    rows, cols = _table_shape(parts[1][1])
    assert (rows, cols) == (2, 2)
    # a lone pipe-line is not a table
    solo = _split_table_blocks("a | b")
    assert [k for k, _ in solo] == ["text"]


def test_markdown_section_chain_persian():
    from app.knowledge.parsers.gemini_structure import _ElementBuilder

    b = _ElementBuilder()
    b.add_page(1, "## فصل ۳\n\nواکنش‌های شیمیایی در این فصل می‌آیند و متن طولانی است.\n\n"
                  "| ماده | pH |\n| --- | --- |\n| لیمو | ۲ |")
    b.add_page(2, "- آب\n- خاک\n\n### واکنش جانشینی\n\nتوضیح واکنش جانشینی در اینجا می‌آید و طولانی است.")
    doc = b.document("chem.pdf", 2)
    assert [e.kind for e in doc.elements] == [
        "heading", "paragraph", "table", "list", "heading", "paragraph"]
    h1 = doc.elements[0]
    assert h1.text_markdown == "فصل ۳"
    assert h1.metadata["section"] == "" and h1.metadata["parent_id"] == ""
    for el in doc.elements[1:4]:
        assert el.metadata["section"] == "فصل ۳"
        assert el.metadata["parent_id"] == h1.element_id
    tail = doc.elements[4:]
    assert tail[0].metadata["parent_id"] == h1.element_id
    assert tail[1].metadata["section"] == "واکنش جانشینی"
    assert tail[1].metadata["parent_id"] == tail[0].element_id
    assert [e.page_no for e in doc.elements] == [1, 1, 1, 2, 2, 2]
    assert [e.metadata["document_order"] for e in doc.elements] == [1, 2, 3, 4, 5, 6]
    assert doc.elements[2].metadata["rows"] == 2
    assert doc.page_count == 2 and len(doc.pages) == 2


# ---------- parser with fakes ----------

def _texts(monkeypatch, pages):
    import app.knowledge.parsers.gemini_structure as G

    monkeypatch.setattr(G, "_page_texts", lambda blob: list(pages))


def test_parse_text_batches_structured(monkeypatch):
    import app.knowledge.parsers.gemini_structure as G

    _texts(monkeypatch, ["متن صفحه اول درباره اسیدها", "متن صفحه دوم درباره بازها"])
    seen = {}

    def fake_gen(prompt, model, max_tokens, timeout):
        seen["prompt"] = prompt
        seen["model"] = model
        assert "[PAGE 1]" in prompt and "[PAGE 2]" in prompt
        return ("[PAGE 1]\n## اسیدها\n\nمتن صفحه اول درباره اسیدها با توضیح کامل و نقطه پایان.\n\n"
                "[PAGE 2]\n## بازها\n\nمتن صفحه دوم درباره بازها با توضیح کامل و نقطه پایان.",
                {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140})

    doc = G.GeminiStructuredParser().parse(
        "pdf", "chem.pdf", b"%PDF-fake", model="gemini-2.5-flash",
        generate_fn=fake_gen, max_pages_per_call=5)
    assert [e.kind for e in doc.elements] == [
        "heading", "paragraph", "heading", "paragraph"]
    assert [e.page_no for e in doc.elements] == [1, 1, 2, 2]
    # a heading carries its ENCLOSING section (normalize contract); the
    # follower paragraph carries the new section, nested under heading 1
    assert doc.elements[2].metadata["section"] == "اسیدها"
    assert doc.elements[2].metadata["parent_id"] == doc.elements[0].element_id
    assert doc.elements[3].metadata["section"] == "بازها"
    assert doc.elements[3].metadata["parent_id"] == doc.elements[2].element_id
    assert doc.parse_meta["parser"] == "gemini-structured"
    assert doc.parse_meta["model"] == "gemini-2.5-flash"
    assert doc.parse_meta["pages"] == 2
    assert doc.parse_meta["usage"]["calls"] == 1
    assert doc.parse_meta["usage"]["prompt_tokens"] == 100
    assert doc.parse_meta["usage"]["completion_tokens"] == 40
    assert "degraded" not in doc.parse_meta


def test_parse_batches_split_per_call_and_usage_sums(monkeypatch):
    import app.knowledge.parsers.gemini_structure as G

    _texts(monkeypatch, [f"متن صفحه {i}" for i in range(1, 4)])
    calls = []

    def fake_gen(prompt, model, max_tokens, timeout):
        calls.append(prompt)
        import re as _re

        pages = [int(m.group(1)) for m in _re.finditer(r"\[PAGE\s+(\d+)\]", prompt)]
        body = "".join(f"[PAGE {p}]\nمتن ساختاریافته صفحه {p}\n" for p in pages)
        return body, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    doc = G.GeminiStructuredParser().parse(
        "pdf", "b.pdf", b"%PDF", generate_fn=fake_gen, max_pages_per_call=2)
    assert doc.parse_meta["text_batches"] == 2
    assert doc.parse_meta["usage"]["calls"] == 2
    assert doc.parse_meta["usage"]["prompt_tokens"] == 20
    assert sorted({e.page_no for e in doc.elements}) == [1, 2, 3]


def test_parse_empty_pages_vision_real_raster(monkeypatch):
    import app.knowledge.parsers.gemini_structure as G

    blob = _blank_pdf(2)

    def fake_vision(png, prompt, model, max_tokens, timeout):
        assert png.startswith(b"\x89PNG")  # really rasterized
        return "## صفحه\n\nمتن استخراج‌شده از صفحه با نقطه پایان.", {
            "prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}

    doc = G.GeminiStructuredParser().parse(
        "pdf", "scan.pdf", blob, vision_fn=fake_vision)
    assert [e.kind for e in doc.elements] == [
        "heading", "paragraph", "heading", "paragraph"]
    assert [e.page_no for e in doc.elements] == [1, 1, 2, 2]
    assert doc.parse_meta["vision_pages"] == [1, 2]
    assert doc.parse_meta["vision_done"] == 2
    assert doc.parse_meta["usage"]["calls"] == 2


def test_parse_batch_failure_degrades_to_flat(monkeypatch):
    import app.knowledge.parsers.gemini_structure as G

    _texts(monkeypatch, ["متن خام صفحه که باید حفظ شود"])

    def boom(prompt, model, max_tokens, timeout):
        raise RuntimeError("gateway down")

    doc = G.GeminiStructuredParser().parse(
        "pdf", "f.pdf", b"%PDF", generate_fn=boom)
    assert doc.parse_meta["degraded"] is True
    assert doc.parse_meta["failed_batches"] == [[1]]
    assert any("متن خام" in e.text_markdown for e in doc.elements)
    assert doc.elements[0].page_no == 1  # order/page preserved


def test_parse_total_failure_raises(monkeypatch):
    import app.knowledge.parsers.gemini_structure as G

    blob = _blank_pdf(1)

    def boom(png, prompt, model, max_tokens, timeout):
        raise RuntimeError("vision down")

    with pytest.raises(RuntimeError):
        G.GeminiStructuredParser().parse("pdf", "s.pdf", blob, vision_fn=boom)


def test_parse_refuses_oversize(monkeypatch):
    import app.knowledge.parsers.gemini_structure as G
    from app.core.config import get_settings

    _texts(monkeypatch, ["a", "b"])
    monkeypatch.setattr(get_settings(), "GEMINI_STRUCT_MAX_PAGES", 1)
    with pytest.raises(RuntimeError, match="exceeds"):
        G.GeminiStructuredParser().parse("pdf", "big.pdf", b"%PDF")


def test_parse_rejects_non_pdf_and_empty():
    import app.knowledge.parsers.gemini_structure as G

    with pytest.raises(ValueError):
        G.GeminiStructuredParser().parse("docx", "a.docx", b"PK")
    with pytest.raises(RuntimeError):
        G.GeminiStructuredParser().parse("pdf", "e.pdf", b"")


# ---------- dispatcher ----------

def _gemini_off(monkeypatch):
    from app.knowledge import parsers as P

    class S:
        USE_GEMINI_PDF = False

    monkeypatch.setattr(P, "get_settings", lambda: S())


def test_dispatcher_pdf_routes_gemini_first(monkeypatch):
    from app.knowledge import parsers as P
    from app.knowledge.parsers import gemini_structure as G

    sentinel = ParsedDocument(title="structured")
    monkeypatch.setattr(G.GeminiStructuredParser, "parse",
                        lambda self, *a, **k: sentinel)
    assert P.parse_source("pdf", "a.pdf", b"%PDF-1.4") is sentinel


def test_dispatcher_pdf_threads_seams(monkeypatch):
    from app.knowledge import parsers as P
    from app.knowledge.parsers import gemini_structure as G

    seen = {}

    def fake_parse(self, st, fn, blob, model=None, generate_fn=None,
                   vision_fn=None, **k):
        seen["model"] = model
        seen["has_gen"] = generate_fn is not None
        seen["has_vis"] = vision_fn is not None
        return ParsedDocument(title="s")

    monkeypatch.setattr(G.GeminiStructuredParser, "parse", fake_parse)
    gen = lambda *a, **k: "x"  # noqa: E731
    vis = lambda *a, **k: "y"  # noqa: E731
    P.parse_source("pdf", "a.pdf", b"%PDF", pdf_model="m",
                   pdf_generate_fn=gen, pdf_vision_fn=vis)
    assert seen == {"model": "m", "has_gen": True, "has_vis": True}


def test_dispatcher_describe_fn_doubles_as_vision_seam(monkeypatch):
    from app.knowledge import parsers as P

    doc = P.parse_source("pdf", "s.pdf", _blank_pdf(1),
                         describe_fn=lambda blob: "شرح دیده‌شده")
    assert any("شرح دیده‌شده" in e.text_markdown for e in doc.elements)
    assert doc.parse_meta["parser"] == "gemini-structured"


def test_dispatcher_pdf_falls_back_loud_on_gemini_failure(monkeypatch):
    from app.knowledge import parsers as P
    from app.knowledge.parsers import gemini_structure as G

    def boom(self, *a, **k):
        raise RuntimeError("gemini down")

    monkeypatch.setattr(G.GeminiStructuredParser, "parse", boom)
    doc = P.parse_source("pdf", "s.pdf", _blank_pdf(1), ocr_provider=None)
    assert doc.parse_meta["parser"] == "fallback"
    assert doc.parse_meta["ocr_degraded"] is True  # loud, never silent


def test_dispatcher_flag_off_uses_flat(monkeypatch):
    from app.knowledge import parsers as P

    _gemini_off(monkeypatch)
    doc = P.parse_source("pdf", "s.pdf", make_pdf(["hello world"]),
                         ocr_provider="disabled")
    assert doc.pages[0].text == "hello world"
    assert doc.parse_meta["parser"] == "fallback"


def test_dispatcher_docx_never_touches_gemini(monkeypatch):
    from app.knowledge import parsers as P
    from app.knowledge.parsers import gemini_structure as G

    def boom(self, *a, **k):
        raise AssertionError("docx must not route to Gemini")

    monkeypatch.setattr(G.GeminiStructuredParser, "parse", boom)
    out = P.parse_source("txt", "a.txt", "plain text".encode())
    assert out.pages[0].text == "plain text"


def test_dispatcher_plain_text_keeps_structure():
    from app.knowledge import parsers as P
    from app.knowledge.chunking import chunk_parsed

    blob = "# سلام\n\nیک پاراگراف ساده.".encode()
    doc = P.parse_source("txt", "a.txt", blob)
    assert doc.pages[0].text == blob.decode()
    assert [e.kind for e in doc.elements] == ["heading", "paragraph"]
    drafts = chunk_parsed(doc)
    assert drafts[1].metadata["section"] == "سلام"


# ---------- worker integration ----------

def test_worker_gemini_pdf_ready_with_usage(db, monkeypatch):
    import app.knowledge.parsers.gemini_structure as G
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "DOCUMENT_VISION_ENABLED", False)
    monkeypatch.setattr(G, "_page_texts",
                        lambda blob: ["متن صفحه اول درباره اسیدها",
                                      "متن صفحه دوم درباره بازها"])
    _, w, kb = _seed_ws(db)
    st = FakeStorage()
    src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, type="pdf",
                 filename="chem.pdf", storage_key="k", status="pending")
    db.add(src)
    db.commit()
    st.put("k", b"%PDF-1.4 fake")

    def fake_gen(prompt, model, max_tokens, timeout):
        import re as _re

        pages = [int(m.group(1)) for m in _re.finditer(r"\[PAGE\s+(\d+)\]", prompt)]
        body = "".join(
            f"[PAGE {p}]\n## بخش {p}\n\nمتن ساختاریافته اسیدها در صفحه {p} با نقطه پایان.\n"
            for p in pages)
        return body, {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}

    res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                     embed_fn=lambda texts: [[0.01] * 1536 for _ in texts],
                     pdf_generate_fn=fake_gen)
    assert res["ok"] is True and res["parser"] == "gemini-structured"
    db.refresh(src)
    assert src.status == "ready"
    assert src.parse_meta["usage"]["calls"] >= 1
    chunks = db.query(Chunk).all()
    assert chunks and any("اسیدها" in c.content for c in chunks)


def _structured_doc():
    return ParsedDocument(
        title="s",
        elements=[
            ParsedElement(element_id="e1", kind="text", page_no=1, reading_order=1,
                          text_markdown="Acids donate protons in water.",
                          metadata={"kind": "text", "element_id": "e1"}),
            ParsedElement(element_id="e2", kind="table", page_no=1, reading_order=2,
                          text_markdown="| A | B |\n| --- | --- |\n| 1 | 2 |",
                          metadata={"kind": "table", "element_id": "e2",
                                    "rows": 2, "cols": 2}),
            ParsedElement(element_id="e3", kind="image", page_no=2, reading_order=3,
                          text_markdown="[Figure on page 2: Titration curve]",
                          blob=b"\x89PNG\r\n\x1a\n" + b"1" * 50, caption="Titration curve",
                          metadata={"kind": "image", "element_id": "e3",
                                    "needs_vision": True}),
        ],
        parse_meta={"parser": "gemini-structured"},
    )


def test_worker_persists_structured_with_figures(db, monkeypatch):
    import workers.tasks as T
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "DOCUMENT_VISION_ENABLED", False)
    _, w, kb = _seed_ws(db)
    st = FakeStorage()
    src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, type="pdf",
                 filename="s.pdf", storage_key="k", status="pending")
    db.add(src)
    db.commit()
    st.put("k", b"%PDF-1.4 fake")
    monkeypatch.setattr(T, "parse_source", lambda *a, **k: _structured_doc())
    res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                     embed_fn=lambda texts: [[0.01] * 1536 for _ in texts])
    assert res["ok"] and res["parser"] == "gemini-structured"
    chunks = db.query(Chunk).all()
    assert len(chunks) == 3
    kinds = sorted(c.chunk_metadata.get("kind") for c in chunks)
    assert kinds == ["image", "table", "text"]
    img = next(c for c in chunks if c.chunk_metadata.get("kind") == "image")
    key = img.chunk_metadata.get("storage_key", "")
    assert key.startswith(f"workspaces/{w}/kb/{kb.id}/sources/{src.id}/figures/")
    assert st.get(key).startswith(b"\x89PNG")


def test_worker_global_source_sqlite(db, monkeypatch):
    import workers.tasks as T
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "DOCUMENT_VISION_ENABLED", False)
    gkb = KnowledgeBase(id=uuid.uuid4(), workspace_id=None, title="G", scope="global")
    db.add(gkb)
    db.commit()
    st = FakeStorage()
    src = Source(id=uuid.uuid4(), workspace_id=None, kb_id=gkb.id, type="pdf",
                 filename="g.pdf", storage_key="gk", status="pending")
    db.add(src)
    db.commit()
    st.put("gk", b"%PDF-1.4 fake")
    monkeypatch.setattr(T, "parse_source", lambda *a, **k: _structured_doc())
    res = run_ingest(src.id, workspace_id=None, db=db, storage=st,
                     embed_fn=lambda texts: [[0.01] * 1536 for _ in texts])
    assert res["ok"] is True
    assert db.query(Chunk).count() == 3


def test_worker_stage_prefixed_errors(db, monkeypatch):
    import workers.tasks as T

    st = FakeStorage()
    _, w, kb = _seed_ws(db)
    src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, type="pdf",
                 filename="e.pdf", storage_key="ek", status="pending")
    db.add(src)
    db.commit()
    st.put("ek", b"%PDF-1.4 fake")
    monkeypatch.setattr(T, "parse_source",
                        lambda *a, **k: ParsedDocument(
                            pages=[ParsedPage(page_no=1, text="hello world")]))

    def boom_embed(texts):
        raise RuntimeError("provider down")

    res = run_ingest(src.id, workspace_id=w, db=db, storage=st, embed_fn=boom_embed)
    assert res["ok"] is False and "Embed:" in res["error"]
    db.refresh(src)
    assert src.status == "failed" and "Embed:" in src.error


def test_worker_extraction_model_threaded(db, monkeypatch):
    import workers.tasks as T
    from app.ai.settings import upsert_setting

    cfg = T._resolve_extraction_config(db)
    assert cfg == {"model": "gemini-2.5-flash"}
    upsert_setting(db, "extraction.pdf", {"model": "gemini-2.5-flash-lite"})
    assert T._resolve_extraction_config(db) == {"model": "gemini-2.5-flash-lite"}


# ---------- global intake endpoint ----------

def _client_with(db, monkeypatch):
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
    yield from _client_with(db, None)


def _login(c, email):
    return c.post("/auth/login",
                  json={"email": email, "password": "password123"}).json()["access_token"]


def _mk_pdf_bytes() -> bytes:
    return (b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n"
            b"trailer\n<< /Root 1 0 R >>\n%%EOF")


def test_global_source_intake_admin_only(db, apiclient):
    from app.main import app as _app
    from app.storage.s3 import get_storage as _get_storage

    _app.dependency_overrides[_get_storage] = lambda: FakeStorage()
    try:
        pw = hash_password("password123")
        adm = User(id=uuid.uuid4(), email="adm@x.com", password_hash=pw, full_name="A",
                   is_admin=True)
        usr = User(id=uuid.uuid4(), email="m@x.com", password_hash=pw, full_name="M")
        db.add_all([adm, usr])
        w = uuid.uuid4()
        db.add(Workspace(id=w, name="W", type="shared", owner_user_id=usr.id))
        from app.workspaces.models import WorkspaceMember as WM

        db.add(WM(workspace_id=w, user_id=usr.id, role="member"))
        gkb = KnowledgeBase(id=uuid.uuid4(), workspace_id=None, title="G", scope="global")
        db.add(gkb)
        db.commit()
        ta = _login(apiclient, "adm@x.com")
        tm = _login(apiclient, "m@x.com")
        files = {"file": ("doc.pdf", io.BytesIO(_mk_pdf_bytes()), "application/pdf")}
        r = apiclient.post(f"/admin/global-knowledge-bases/{gkb.id}/sources",
                           headers={"Authorization": f"Bearer {ta}"},
                           data={"type": "pdf"}, files=files)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["workspace_id"] is None and body["kb_id"] == str(gkb.id)
        r2 = apiclient.post(f"/admin/global-knowledge-bases/{gkb.id}/sources",
                            headers={"Authorization": f"Bearer {tm}"},
                            data={"type": "pdf"}, files=files)
        assert r2.status_code in (401, 403)
    finally:
        _app.dependency_overrides.pop(_get_storage, None)


# ---------- keepers: plain-text structure + chunking ----------

def test_structure_plain_text_heading_list_section():
    from app.knowledge.parsers.normalize import structure_plain_text

    doc = structure_plain_text(
        "# عنوان\n\nمتن پاراگراف اول که به اندازه کافی طولانی است.\n\n- الف\n- ب\n\n## زیربخش\n\nمتن زیربخش.",
        title="n")
    assert [e.kind for e in doc.elements] == [
        "heading", "paragraph", "list", "heading", "paragraph"]
    h1, sub = doc.elements[0], doc.elements[3]
    assert doc.elements[1].metadata["section"] == "عنوان"
    assert doc.elements[2].metadata["section"] == "عنوان"
    assert sub.metadata["parent_id"] == h1.element_id
    assert doc.elements[4].metadata["section"] == "زیربخش"
    assert doc.elements[4].metadata["parent_id"] == sub.element_id


def test_chunking_structured_atomic_and_metadata():
    doc = _structured_doc()
    drafts = chunk_parsed(doc)
    by_kind = {}
    for d in drafts:
        by_kind.setdefault(d.metadata.get("kind"), []).append(d)
    assert len(by_kind["table"]) == 1  # atomic, never split
    assert by_kind["table"][0].page_no == 1
    assert len(by_kind["image"]) == 1
    for d in drafts:
        assert d.metadata.get("element_id", "").startswith("e")


def test_chunking_oversized_table_splits_with_header_repeat():
    rows = ["| H1 | H2 |", "| --- | --- |"] + [f"| r{i} | v{i} |" for i in range(300)]
    doc = ParsedDocument(elements=[])
    doc.elements = [ParsedElement(element_id="e1", kind="table", page_no=2,
                                  reading_order=1, text_markdown="\n".join(rows),
                                  metadata={"kind": "table", "element_id": "e1"})]
    drafts = chunk_parsed(doc)
    assert len(drafts) > 1
    for d in drafts:
        assert "| H1 | H2 |" in d.text  # header repeated
        assert d.metadata["atomic"] is False
    assert drafts[0].metadata["of"] == len(drafts)


def test_chunking_legacy_path_unchanged():
    doc = ParsedDocument(pages=[ParsedPage(page_no=5, text="word " * 500)])
    drafts = chunk_parsed(doc)
    assert drafts and all(d.page_no == 5 for d in drafts)
    assert all(d.metadata == {} for d in drafts)


def test_chunking_never_emits_blank_drafts():
    from app.knowledge.chunking import chunk_parsed
    from app.knowledge.parsers.base import ParsedDocument, ParsedElement, ParsedPage

    doc = ParsedDocument(
        pages=[ParsedPage(page_no=1, text="   "), ParsedPage(page_no=2, text="")],
        elements=[
            ParsedElement(element_id="e1", kind="table", page_no=1, reading_order=1,
                          text_markdown="", metadata={"kind": "table"}),
            ParsedElement(element_id="e2", kind="text", page_no=1, reading_order=2,
                          text_markdown="  \n  ", metadata={"kind": "text"}),
            ParsedElement(element_id="e3", kind="paragraph", page_no=2, reading_order=3,
                          text_markdown="real content here",
                          metadata={"kind": "paragraph"}),
        ])
    drafts = chunk_parsed(doc)
    assert len(drafts) == 1 and drafts[0].text.strip() == "real content here"
    flat = ParsedDocument(pages=[ParsedPage(page_no=1, text="  \n ")])
    assert chunk_parsed(flat) == []


# ---------- keepers: flat refill + provider payloads ----------

def _mixed_doc():
    return ParsedDocument(
        title="m",
        pages=[ParsedPage(page_no=1, text="keep me"),
               ParsedPage(page_no=2, text="   "),
               ParsedPage(page_no=3, text="")],
        page_count=3,
    )


def test_fallback_refill_partial_marks_still_empty(monkeypatch):
    from app.knowledge import parsers as P
    import app.knowledge.parsers.ocr as O

    doc = _mixed_doc()
    calls = {}

    def fake_gemini(blob, pages, model=None):
        calls["pages"] = set(pages)
        calls["model"] = model
        return {1: "متن تازه"}

    monkeypatch.setattr(O, "gemini_ocr_pages", fake_gemini)
    P._refill_blank_pdf_pages(doc, b"%PDF", "gemini", "gemini-2.5-flash-lite")
    assert calls == {"pages": {1, 2}, "model": "gemini-2.5-flash-lite"}
    assert doc.pages[0].text == "keep me"  # parsed text never overwritten
    assert doc.pages[1].text == "متن تازه"
    assert doc.pages[2].text == ""
    assert doc.parse_meta["parser"] == "fallback+gemini-ocr-refill"
    assert doc.parse_meta["ocr_refilled_pages"] == [1]
    assert doc.parse_meta["ocr_empty_pages"] == [2]
    assert doc.parse_meta["ocr_degraded"] is True
    assert doc.parse_meta["ocr_model"] == "gemini-2.5-flash-lite"


def test_fallback_refill_disabled_retired_unknown_flag_degraded():
    from app.knowledge import parsers as P

    for provider, reason in [
        ("disabled", "ocr-provider-disabled"),
        (None, "ocr-provider-disabled"),
        ("easyocr", "easyocr-retired"),
        ("tesseract", "unknown-ocr-provider:tesseract"),
    ]:
        doc = _mixed_doc()
        P._refill_blank_pdf_pages(doc, b"%PDF", provider, None)
        assert doc.parse_meta["parser"] == "fallback"
        assert doc.parse_meta["ocr_degraded"] is True
        assert doc.parse_meta["ocr_empty_pages"] == [1, 2]
        assert doc.parse_meta["ocr_reason"] == reason
        assert all(p.text == t for p, t in
                   zip(doc.pages, ["keep me", "   ", ""]))


def test_fallback_refill_failure_is_degraded_not_silent(monkeypatch):
    from app.knowledge import parsers as P
    import app.knowledge.parsers.ocr as O

    def boom(blob, pages, model=None):
        raise RuntimeError("provider down")

    monkeypatch.setattr(O, "gemini_ocr_pages", boom)
    doc = _mixed_doc()
    P._refill_blank_pdf_pages(doc, b"%PDF", "gemini", None)
    assert doc.parse_meta["ocr_degraded"] is True
    assert doc.parse_meta["ocr_empty_pages"] == [1, 2]
    assert doc.parse_meta["ocr_refill_failed"].startswith("RuntimeError")


def test_fallback_no_empty_pages_touches_nothing(monkeypatch):
    from app.knowledge import parsers as P
    import app.knowledge.parsers.ocr as O

    def boom(*a, **k):
        raise AssertionError("no OCR call expected without empty pages")

    monkeypatch.setattr(O, "gemini_ocr_pages", boom)
    doc = ParsedDocument(title="t", pages=[ParsedPage(page_no=1, text="hi")])
    P._refill_blank_pdf_pages(doc, b"%PDF", "gemini", "m")
    assert doc.parse_meta == {"parser": "fallback"}
    assert doc.pages[0].text == "hi"


def test_parse_source_flat_pdf_wires_refill(monkeypatch):
    from app.knowledge import parsers as P
    import app.knowledge.parsers.ocr as O

    _gemini_off(monkeypatch)

    def fake_gemini(blob, pages, model=None):
        assert model == "gemini-2.5-flash-lite"
        return {i: f"صفحه {i + 1}" for i in sorted(pages)}

    monkeypatch.setattr(O, "gemini_ocr_pages", fake_gemini)
    doc = P.parse_source("pdf", "s.pdf", _blank_pdf(2),
                         ocr_provider="gemini", ocr_model="gemini-2.5-flash-lite")
    assert [p.text for p in doc.pages] == ["صفحه 1", "صفحه 2"]
    assert doc.parse_meta["ocr_refilled_pages"] == [0, 1]
    assert "ocr_degraded" not in doc.parse_meta


def test_worker_flat_scanned_pdf_ready_with_chunks(db, monkeypatch):
    import workers.tasks as T
    import app.knowledge.parsers.ocr as O

    _gemini_off(monkeypatch)
    monkeypatch.setattr(O, "gemini_ocr_pages",
                        lambda blob, pages, model=None: {i: "متن اسکن‌شده" for i in pages})
    _, w, kb = _seed_ws(db)
    st = FakeStorage()
    src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, type="pdf",
                 filename="scan.pdf", storage_key="sk", status="pending")
    db.add(src)
    db.commit()
    st.put("sk", _blank_pdf(2))
    res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                     embed_fn=lambda texts: [[0.01] * 1536 for _ in texts])
    assert res["ok"] is True and res["chunks"] == 2
    db.refresh(src)
    assert src.status == "ready"
    assert src.parse_meta["parser"] == "fallback+gemini-ocr-refill"
    assert src.parse_meta["ocr_refilled_pages"] == [0, 1]


def test_worker_ocr_degraded_empty_is_failed(db, monkeypatch):
    import workers.tasks as T

    _, w, kb = _seed_ws(db)
    st = FakeStorage()
    src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, type="pdf",
                 filename="s.pdf", storage_key="sk", status="pending")
    db.add(src)
    db.commit()
    st.put("sk", b"%PDF-1.4 fake")
    degraded = ParsedDocument(title="s")
    degraded.parse_meta = {"parser": "fallback", "ocr_provider": "disabled",
                           "ocr_degraded": True, "ocr_empty_pages": [1],
                           "ocr_reason": "ocr-provider-disabled"}
    monkeypatch.setattr(T, "parse_source", lambda *a, **k: degraded)
    res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                     embed_fn=lambda texts: [[0.01] * 1536 for _ in texts])
    assert res["ok"] is False and "OCR:" in res["error"]
    db.refresh(src)
    assert src.status == "failed" and "OCR:" in src.error


def test_ai_settings_extraction_ocr_keys(db):
    from app.ai.settings import get_setting, upsert_setting

    assert get_setting(db, "extraction.pdf")["model"] == "gemini-2.5-flash"
    assert get_setting(db, "extraction.pdf")["provider"] == "openai_compat"
    upsert_setting(db, "extraction.pdf", {"model": "gemini-2.5-flash-lite"})
    assert get_setting(db, "extraction.pdf")["model"] == "gemini-2.5-flash-lite"
    assert get_setting(db, "ocr.provider")["provider"] == "gemini"
    assert get_setting(db, "ocr.provider")["model"] == "gemini-2.5-flash-lite"
    assert get_setting(db, "vision.provider")["model"] == "gemini-2.5-flash-lite"
    with pytest.raises(KeyError):
        upsert_setting(db, "nope.unknown", {"a": 1})


def test_worker_ocr_config_threads_gapgpt_gemini_model(db):
    import workers.tasks as T
    from app.ai.settings import upsert_setting

    cfg = T._resolve_ocr_config(db)
    assert cfg == {"provider": "gemini", "model": "gemini-2.5-flash-lite"}
    upsert_setting(db, "ocr.provider",
                   {"provider": "gemini", "model": "gemini-2.5-flash"})
    assert T._resolve_ocr_config(db)["model"] == "gemini-2.5-flash"
    upsert_setting(db, "ocr.provider", {"provider": "disabled"})
    cfg = T._resolve_ocr_config(db)
    assert cfg["provider"] == "disabled" and cfg["model"] is not None


def test_ocr_pages_rejects_unknown_provider():
    from app.knowledge.parsers.ocr import ocr_pages

    with pytest.raises(ValueError):
        ocr_pages("p", b"x", {0}, "tesseract", ["fa"])
    with pytest.raises(ValueError):
        ocr_pages("p", b"x", {0}, "easyocr", ["fa"])  # retired with Docling


def test_gemini_paths_never_touch_removed_deps(monkeypatch):
    # The gemini paths must never import the removed stack
    # (easyocr/docling/raganything were deleted with the Docling route).
    import sys as _sys

    import app.knowledge.parsers.ocr as O

    monkeypatch.setitem(_sys.modules, "easyocr", None)
    monkeypatch.setitem(_sys.modules, "docling", None)
    monkeypatch.setitem(_sys.modules, "docling.document_converter", None)
    with pytest.raises(RuntimeError):
        # garbage bytes cannot rasterize -> surfaces loudly, never silent {}
        O.gemini_ocr_pages(b"not-a-pdf", {0})


def test_embed_refuses_blank_batch_without_http(monkeypatch):
    import app.ai.openai_compat as OC

    called = []

    def _boom(*a, **k):
        called.append(True)
        raise AssertionError("no HTTP on invalid batch")

    monkeypatch.setattr(OC.OpenAICompatProvider, "_post",
                        lambda self, *a, **k: _boom())
    prov = OC.OpenAICompatProvider.__new__(OC.OpenAICompatProvider)
    with pytest.raises(RuntimeError, match="blank inputs"):
        prov.embed(["hello", "   ", "world"])
    assert not called


def test_generate_with_usage_returns_text_and_tokens(monkeypatch):
    import httpx as _httpx

    from app.ai.openai_compat import OpenAICompatProvider

    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "hi"}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3,
                              "total_tokens": 10}}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["model"] = (json or {}).get("model")
        return Resp()

    monkeypatch.setattr(_httpx, "post", fake_post)
    p = OpenAICompatProvider.__new__(OpenAICompatProvider)
    p.base_url = "https://api.gapgpt.app/v1"
    p.api_key = "k"
    p.chat_model = "gemini-2.5-flash"
    text, usage = p.generate_with_usage("hello", temperature=0.1,
                                        max_tokens=50, timeout=5.0)
    assert text == "hi"
    assert usage == {"prompt_tokens": 7, "completion_tokens": 3,
                     "total_tokens": 10}
    assert seen["model"] == "gemini-2.5-flash"
    # legacy generate() keeps its plain-string contract
    assert p.generate("hello") == "hi"


def test_describe_image_posts_vision_payload(monkeypatch):
    import httpx as _httpx

    from app.ai.openai_compat import OpenAICompatProvider

    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "شرح"}}]}  # no usage

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["json"] = json
        return Resp()

    monkeypatch.setattr(_httpx, "post", fake_post)
    p = OpenAICompatProvider.__new__(OpenAICompatProvider)
    p.base_url = "https://api.gapgpt.app/v1"
    p.api_key = "k"
    p.chat_model = "gpt-4o-mini"
    out = p.describe_image(b"\x89PNG\r\n\x1a\n" + b"0" * 10, "reading prompt")
    assert out == "شرح"
    assert seen["url"].endswith("/chat/completions")
    parts = seen["json"]["messages"][0]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    # usage-aware twin tolerates missing usage with zeros (never crash)
    text, usage = p.describe_image_with_usage(b"\x89PNG" + b"0" * 10, "q")
    assert text == "شرح" and usage["total_tokens"] == 0


def test_transcribe_audio_posts_input_audio_payload(monkeypatch):
    import httpx as _httpx

    from app.ai.openai_compat import OpenAICompatProvider

    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "متن گفته‌شده"}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["json"] = json
        return Resp()

    monkeypatch.setattr(_httpx, "post", fake_post)
    p = OpenAICompatProvider.__new__(OpenAICompatProvider)
    p.base_url = "https://api.gapgpt.app/v1"
    p.api_key = "k"
    p.chat_model = "gpt-4o-mini"
    out = p.transcribe_audio(b"RIFF....", "wav", model="gemini-2.5-flash-lite")
    assert out == "متن گفته‌شده"
    assert seen["url"].endswith("/chat/completions")
    assert seen["json"]["model"] == "gemini-2.5-flash-lite"
    parts = seen["json"]["messages"][0]["content"]
    audio = next(x["input_audio"] for x in parts if x.get("type") == "input_audio")
    assert audio["format"] == "wav" and audio["data"]


# ---------- boundary ----------

def test_null_multimodal_processor_calls_nothing():
    assert NullMultimodalProcessor().describe_figure("k", "c") == ""
    assert NullMultimodalProcessor.provider_name == "null"
