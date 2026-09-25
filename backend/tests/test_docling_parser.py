"""Phase 2 Document Core tests (no heavy models, no network).

- normalize: content_list (realistic raganything 1.4.1 schema) -> elements
- chunking: atomic tables, oversized split w/ header repeat, equations,
  image placeholders + blob passthrough, legacy path unchanged
- adapter: fake parser subsystem (no docling/raganything needed),
  temp cleanup, Persian-OCR refill trigger, missing-package path
- dispatcher: USE_DOCLING on/off
- worker: figure persist to storage, metadata, global source, stage errors
- multimodal boundary: null processor calls nothing
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
from app.knowledge.parsers.base import ParsedDocument, ParsedPage
from app.knowledge.parsers.multimodal import NullMultimodalProcessor
from app.knowledge.parsers.normalize import normalize_content_list
from app.users.models import User
from app.workspaces.models import Workspace, WorkspaceMember
from workers.tasks import run_ingest

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)
FAKE_VEC = [0.01] * 1536


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


def _content_list():
    return [
        {"type": "text", "text": "Chapter 2: Acids and Bases", "page_idx": 0},
        {"type": "text", "text": "Acids donate protons in water.", "page_idx": 0},
        {"type": "table", "table_body": {
            "table_cells": [
                {"start_row_offset_idx": 0, "start_col_offset_idx": 0, "text": "Substance"},
                {"start_row_offset_idx": 0, "start_col_offset_idx": 1, "text": "pH"},
                {"start_row_offset_idx": 1, "start_col_offset_idx": 0, "text": "Lemon"},
                {"start_row_offset_idx": 1, "start_col_offset_idx": 1, "text": "2"},
            ]}, "table_caption": "Acidity", "table_footnote": "",
         "img_path": "", "page_idx": 0},
        {"type": "equation", "text": "pH = -\\log[H+]", "text_format": "latex",
         "img_path": "", "page_idx": 0},
        {"type": "image", "img_path": "/tmp/fig1.png", "image_caption": "Titration curve",
         "image_footnote": "", "page_idx": 1},
        {"type": "text", "text": "[Table processing failed: broken]", "page_idx": 1},
        {"type": "text", "text": "   ", "page_idx": 1},
    ]


# ---------- normalize ----------

def test_normalize_content_list(tmp_path, monkeypatch):
    from app.knowledge.parsers import normalize as N

    fig = tmp_path / "fig1.png"
    fig.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    items = _content_list()
    items[4]["img_path"] = str(fig)
    doc = N.normalize_content_list(items, title="chem.pdf")
    kinds = [e.kind for e in doc.elements]
    assert kinds == ["text", "text", "table", "equation", "image", "text"]
    # page_idx 0-based -> page_no 1-based (citation contract)
    assert [e.page_no for e in doc.elements] == [1, 1, 1, 1, 2, 2]
    table = doc.elements[2]
    assert "| Substance | pH |" in table.text_markdown
    assert "| Lemon | 2 |" in table.text_markdown
    assert table.metadata["rows"] == 2 and table.metadata["cols"] == 2
    eq = doc.elements[3]
    assert eq.text_markdown == "$pH = -\\log[H+]$"
    img = doc.elements[4]
    assert img.blob and img.blob.startswith(b"\x89PNG")
    assert img.metadata["needs_vision"] is True
    assert "Titration curve" in img.text_markdown
    # degraded failure marker kept WITH warning (never silent)
    failed = doc.elements[5]
    assert failed.kind == "text" and "parse_warning" in failed.metadata
    # empty text item skipped
    assert len(doc.elements) == 6
    assert doc.page_count == 2
    assert len(doc.pages) == 2  # legacy pages view intact


def test_normalize_dual_caption_keys_and_missing_bytes():
    doc = normalize_content_list([
        {"type": "image", "img_path": "/nonexistent/x.png",
         "img_caption": ["Alt text"], "img_footnote": [], "page_idx": 3},
    ])
    img = doc.elements[0]
    assert img.caption == "Alt text" and img.blob is None
    assert img.metadata["parse_warning"] == "image-bytes-missing"
    assert img.page_no == 4


# ---------- chunking ----------

def test_chunking_structured_atomic_and_metadata():
    doc = normalize_content_list(_content_list())
    drafts = chunk_parsed(doc)
    by_kind = {}
    for d in drafts:
        by_kind.setdefault(d.metadata.get("kind"), []).append(d)
    assert len(by_kind["table"]) == 1  # atomic, never split
    assert by_kind["table"][0].page_no == 1
    assert by_kind["equation"][0].text.startswith("$")
    assert len(by_kind["image"]) == 1
    assert by_kind["image"][0].blob is None or isinstance(by_kind["image"][0].blob, bytes)
    for d in drafts:
        assert d.metadata.get("element_id", "").startswith("e")


def test_chunking_oversized_table_splits_with_header_repeat():
    rows = ["| H1 | H2 |", "| --- | --- |"] + [f"| r{i} | v{i} |" for i in range(300)]
    doc = ParsedDocument(elements=[])
    from app.knowledge.parsers.base import ParsedElement

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


# ---------- adapter (fake parser subsystem) ----------

def _run_adapter(monkeypatch, items, refill=None, ocr_provider="easyocr",
                 write_json=None, text_layer=(1000, 2), ocr_model=None):
    import app.knowledge.parsers.raganything_adapter as A

    class FakeParser:
        def parse_document(self, path, method="auto", output_dir=None, **kw):
            assert output_dir and output_dir.startswith(
                __import__("tempfile").gettempdir())
            if write_json is not None:
                import json as _json
                import os as _os

                d = _os.path.join(output_dir, "doc", "docling")
                _os.makedirs(d, exist_ok=True)
                with open(_os.path.join(d, "doc.json"), "w",
                          encoding="utf-8") as f:
                    _json.dump(write_json, f)
            return [dict(i) for i in items]

    # NOTE: the adapter does `from raganything.parser import get_parser`
    # inside parse(), so patch the source attribute (module attr patch
    # on A would NOT intercept the from-import).
    monkeypatch.setattr("raganything.parser.get_parser", lambda name: FakeParser())
    # Default: pretend a healthy text layer so tests exercise the RA path
    # (scanned-detect tests override with text_layer=(0, N)).
    monkeypatch.setattr(A, "_text_chars", lambda blob: text_layer)
    if refill is not None:
        # ocr_pages(pdf_path, pdf_bytes, empty_pages, provider, langs)
        monkeypatch.setattr("app.knowledge.parsers.ocr.ocr_pages",
                            lambda *a, **k: refill(*a, **k))
    return A.RAGAnythingParser().parse("pdf", "f.pdf", b"%PDF-fake",
                                       ocr_provider=ocr_provider,
                                       ocr_model=ocr_model)


def test_adapter_parse_only_and_cleanup(monkeypatch, tmp_path):
    import tempfile

    import app.knowledge.parsers.raganything_adapter as A

    created: list[str] = []
    real_mkstemp = tempfile.mkstemp
    real_mkdtemp = tempfile.mkdtemp

    def track_mkstemp(*a, **k):
        fd, path = real_mkstemp(*a, **k)
        created.append(path)
        return fd, path

    def track_mkdtemp(*a, **k):
        path = real_mkdtemp(*a, **k)
        created.append(path)
        return path

    monkeypatch.setattr(tempfile, "mkstemp", track_mkstemp)
    monkeypatch.setattr(tempfile, "mkdtemp", track_mkdtemp)
    doc = _run_adapter(monkeypatch, _content_list())
    assert [e.kind for e in doc.elements][:4] == ["text", "text", "table", "equation"]
    assert doc.parse_meta["parser"] == "raganything-docling"
    import os as _os

    assert created, "adapter must stage temp input + output_dir"
    assert all(not _os.path.exists(p) for p in created)  # all removed


def test_adapter_ocr_refill_only_for_empty_pages(monkeypatch):
    items = [
        {"type": "text", "text": "visible intro", "page_idx": 0},
        {"type": "text", "text": "   ", "page_idx": 1},
    ]
    seen = {}

    def fake_refill(pdf_path, pdf_bytes, empty_pages, provider, langs,
                    model=None):
        seen["pages"] = set(empty_pages)
        seen["provider"] = provider
        seen["langs"] = list(langs)
        seen["model"] = model
        assert "fa" in langs  # Persian mandatory, never dropped
        return {1: "متن بازیابی‌شده فارسی"}

    doc = _run_adapter(monkeypatch, items, refill=fake_refill,
                       ocr_provider="easyocr", ocr_model="gemini-2.5-flash-lite")
    assert seen["pages"] == {1}
    assert seen["provider"] == "easyocr"
    assert seen["model"] == "gemini-2.5-flash-lite"  # threaded to ocr_pages
    texts = [e.text_markdown for e in doc.elements]
    assert any("فارسی" in t for t in texts)
    assert doc.parse_meta["ocr_refilled_pages"] == [1]
    assert doc.parse_meta["ocr_model"] == "gemini-2.5-flash-lite"


def test_adapter_refill_failure_is_degraded_not_silent(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ocr engine down")

    monkeypatch.setattr("app.knowledge.parsers.ocr.ocr_pages", boom)
    doc = _run_adapter(monkeypatch, [{"type": "text", "text": "  ", "page_idx": 0}])
    assert doc.parse_meta["ocr_degraded"] is True
    assert doc.parse_meta["ocr_empty_pages"] == [0]


def test_adapter_missing_package(monkeypatch):
    import builtins

    import app.knowledge.parsers.raganything_adapter as A

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("raganything"):
            raise ImportError("nope")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError):
        A.RAGAnythingParser().parse("pdf", "f.pdf", b"x")


def test_dispatcher_flag(monkeypatch):
    from app.knowledge import parsers as P

    class S:
        USE_DOCLING = False
        USE_RAGANYTHING = False

    monkeypatch.setattr(P, "get_settings", lambda: S())
    out = P.parse_source("note", "n", b'{"title": "t", "content": "hello"}')
    assert out.pages[0].text == "hello"

    S.USE_DOCLING = True
    from app.knowledge.parsers import raganything_adapter as RA

    sentinel = ParsedDocument(title="structured")
    monkeypatch.setattr(RA.RAGAnythingParser, "parse",
                        lambda self, *a, **k: sentinel)
    routed = P.parse_source("pdf", "a.pdf", b"%PDF-1.4")
    assert routed is sentinel  # dispatcher really routes pdf to the adapter
    # non-pdf types never route to the adapter (Phase 2 scope is PDF-only)
    out2 = P.parse_source("txt", "a.txt", "plain text".encode())
    assert out2.pages[0].text == "plain text"


# ---------- worker integration ----------

def _structured_doc():
    from app.knowledge.parsers.base import ParsedElement

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
        parse_meta={"parser": "raganything-docling"},
    )


def test_worker_persists_structured_with_figures(db, monkeypatch):
    import workers.tasks as T
    from app.core.config import get_settings

    # Hermetic: persistence only — vision must not hit the network here.
    monkeypatch.setattr(get_settings(), "DOCUMENT_VISION_ENABLED", False)
    u, w, kb = _seed_ws(db)
    st = FakeStorage()
    src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, type="pdf",
                 filename="s.pdf", storage_key="k", status="pending")
    db.add(src)
    db.commit()
    st.put("k", b"%PDF-1.4 fake")
    monkeypatch.setattr(T, "parse_source", lambda *a, **k: _structured_doc())
    res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                     embed_fn=lambda texts: [[0.01] * 1536 for _ in texts])
    assert res["ok"] and res["parser"] == "raganything-docling"
    chunks = db.query(Chunk).all()
    assert len(chunks) == 3
    kinds = sorted(c.chunk_metadata.get("kind") for c in chunks)
    assert kinds == ["image", "table", "text"]
    img = next(c for c in chunks if c.chunk_metadata.get("kind") == "image")
    key = img.chunk_metadata.get("storage_key", "")
    assert key.startswith(f"workspaces/{w}/kb/{kb.id}/sources/{src.id}/figures/")
    assert st.get(key).startswith(b"\x89PNG")
    seg_ids = {c.segment_id for c in chunks}
    from app.knowledge.models import PageSegment

    pages = sorted(s.page_no for s in db.query(PageSegment).filter(
        PageSegment.id.in_(seg_ids)).all())
    assert pages == [1, 1, 2]  # page-level citations intact


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
    u, w, kb = _seed_ws(db)
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


# ---------- boundary ----------

def test_null_multimodal_processor_calls_nothing():
    assert NullMultimodalProcessor().describe_figure("k", "c") == ""
    assert NullMultimodalProcessor.provider_name == "null"


# ---------- A: real page mapping ----------

def _docling_dict_fixture():
    def blk(label, text, page, key="orig"):
        return {"label": label, key: text, "prov": [{"page_no": page}]}
    return {"body": {"children": [
        {"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}, {"$ref": "#/tables/0"},
        {"$ref": "#/texts/2"}, {"$ref": "#/texts/3"},
    ]}, "texts": [
        blk("text", "Intro words", 1),
        blk("text", "Acids donate", 1),
        blk("text", "Second page material", 2),
        blk("text", "Second page material", 3),  # duplicate text, other page
    ], "tables": [
        {"label": "table", "caption": "Acidity",
         "data": {"table_cells": []}, "prov": [{"page_no": 2}]},
    ]}


def test_page_map_from_docling_dict():
    from app.knowledge.parsers.normalize import page_map_from_docling_dict

    items = [
        {"type": "text", "text": "Intro words", "page_idx": 0},
        {"type": "text", "text": "Acids donate", "page_idx": 0},
        {"type": "table", "table_body": {"table_cells": []},
         "table_caption": "Acidity", "page_idx": 0},
        {"type": "text", "text": "Second page material", "page_idx": 0},
        {"type": "text", "text": "UNMATCHABLE XYZ", "page_idx": 0},
    ]
    page_map, unverified = page_map_from_docling_dict(_docling_dict_fixture(), items)
    assert page_map == {0: 1, 1: 1, 2: 2, 3: 2}
    assert unverified == {4}  # duplicate-text/fallback exhausted -> flagged


def test_adapter_applies_json_page_map(monkeypatch):
    items = [
        {"type": "text", "text": "Intro words", "page_idx": 0},
        {"type": "text", "text": "Second page material", "page_idx": 0},
    ]
    doc = _run_adapter(monkeypatch, items, write_json=_docling_dict_fixture())
    assert [e.page_no for e in doc.elements] == [1, 2]  # NOT all page 1
    assert doc.page_count == 2
    assert "page_map" not in doc.parse_meta  # mapping succeeded


def test_adapter_missing_json_keeps_heuristic(monkeypatch):
    items = [{"type": "text", "text": "hi", "page_idx": 4}]
    doc = _run_adapter(monkeypatch, items)
    assert doc.elements[0].page_no == 5
    assert doc.parse_meta.get("page_map") == "unavailable-fallback-page-idx"


# ---------- B: scanned early-detect ----------

def test_scanned_pdf_skips_full_parse(monkeypatch):
    import app.knowledge.parsers.raganything_adapter as A

    calls = []

    def fake_get_parser(name):
        calls.append(name)
        raise AssertionError("full parse must be skipped for scanned PDFs")

    monkeypatch.setattr("raganything.parser.get_parser", fake_get_parser)
    monkeypatch.setattr(A, "_text_chars", lambda blob: (0, 2))

    def fake_ocr(pdf_path, pdf_bytes, empty_pages, provider, langs,
                 model=None):
        assert provider == "easyocr"
        assert set(empty_pages) == {0, 1}  # both text-empty pages refilled
        return {i: f"متن صفحه {i + 1}" for i in sorted(empty_pages)}

    monkeypatch.setattr("app.knowledge.parsers.ocr.ocr_pages",
                        lambda *a, **k: fake_ocr(*a, **k))
    doc = A.RAGAnythingParser().parse("pdf", "scan.pdf", b"%PDF-fake",
                                      ocr_provider="easyocr")
    assert calls == []
    assert [e.text_markdown for e in doc.elements] == ["متن صفحه 1", "متن صفحه 2"]
    assert [e.page_no for e in doc.elements] == [1, 2]
    assert doc.parse_meta.get("scanned_early_detect") is True


def test_scanned_pdf_with_disabled_ocr_is_degraded(monkeypatch):
    import app.knowledge.parsers.raganything_adapter as A

    monkeypatch.setattr(A, "_text_chars", lambda blob: (0, 1))
    doc = A.RAGAnythingParser().parse("pdf", "scan.pdf", b"%PDF-fake",
                                      ocr_provider="disabled")
    assert doc.elements == []
    assert doc.parse_meta["ocr_degraded"] is True
    assert doc.parse_meta["ocr_reason"] == "ocr-provider-disabled"


def test_scan_threshold_configurable(monkeypatch):
    # 35 chars over 2 pages: below default 20/page -> scanned; with a
    # threshold of 10/page -> full RA parse. No hard-coded numbers in prod.
    import app.knowledge.parsers.raganything_adapter as A

    monkeypatch.setattr(A, "_text_chars", lambda blob: (35, 2))

    def fake_get_parser(name):
        class P:
            def parse_document(self, *a, **k):
                return [{"type": "text", "text": "x", "page_idx": 0}]
        return P()

    monkeypatch.setattr("raganything.parser.get_parser", fake_get_parser)
    doc = A.RAGAnythingParser().parse("pdf", "f.pdf", b"x",
                                      ocr_provider="disabled")
    assert doc.parse_meta.get("scanned_early_detect") is True  # default 20
    monkeypatch.setattr(A, "_scan_threshold", lambda: 10)
    doc2 = A.RAGAnythingParser().parse("pdf", "f.pdf", b"x",
                                       ocr_provider="disabled")
    assert "scanned_early_detect" not in doc2.parse_meta  # RA path taken


# ---------- C: OCR provider selection ----------

def test_ocr_default_is_disabled_safe(monkeypatch):
    # No provider passed (unresolved) -> behaves as disabled, never easyocr.
    doc = _run_adapter(monkeypatch, [{"type": "text", "text": "  ", "page_idx": 0}],
                       ocr_provider=None)
    assert doc.parse_meta["ocr_degraded"] is True
    assert doc.parse_meta["ocr_reason"] == "ocr-provider-disabled"


def test_gemini_ocr_path_never_touches_easyocr_or_docling(monkeypatch):
    # Mutual exclusion: importing easyocr/docling must explode if the
    # gemini path ever touches them.
    import sys as _sys

    import app.knowledge.parsers.ocr as O

    monkeypatch.setitem(_sys.modules, "easyocr", None)
    monkeypatch.setitem(_sys.modules, "docling", None)
    monkeypatch.setitem(_sys.modules, "docling.document_converter", None)
    with pytest.raises(RuntimeError):
        # garbage bytes cannot rasterize -> surfaces loudly, never silent {}
        O.gemini_ocr_pages(b"not-a-pdf", {0})


def test_ocr_pages_rejects_unknown_provider():
    from app.knowledge.parsers.ocr import ocr_pages

    with pytest.raises(ValueError):
        ocr_pages("p", b"x", {0}, "tesseract", ["fa"])


def test_ai_settings_ocr_vision_keys(db):
    from app.ai.settings import get_setting, upsert_setting

    assert get_setting(db, "ocr.provider")["provider"] == "gemini"
    # GapGPT-routed Gemini default (verified live against the /models list).
    assert get_setting(db, "ocr.provider")["model"] == "gemini-2.5-flash-lite"
    assert get_setting(db, "vision.provider")["model"] == "gemini-2.5-flash-lite"
    upsert_setting(db, "ocr.provider", {"provider": "easyocr"})
    assert get_setting(db, "ocr.provider")["provider"] == "easyocr"
    # unknown keys still rejected; model survives a provider-only upsert
    assert get_setting(db, "ocr.provider")["model"] == "gemini-2.5-flash-lite"
    upsert_setting(db, "ocr.provider",
                   {"provider": "gemini", "model": "gemini-2.5-flash"})
    assert get_setting(db, "ocr.provider")["model"] == "gemini-2.5-flash"
    assert get_setting(db, "vision.provider")["enabled"] is False
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


# ---------- fallback direct-gemini refill (no docling) ----------

def _blank_pdf(n=2):
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(n):
        w.add_blank_page(200, 200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _flags_off(monkeypatch):
    from app.knowledge import parsers as P

    class S:
        USE_DOCLING = False
        USE_RAGANYTHING = False

    monkeypatch.setattr(P, "get_settings", lambda: S())


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


def test_fallback_refill_disabled_easyocr_unknown_flag_degraded():
    from app.knowledge import parsers as P

    for provider, reason in [
        ("disabled", "ocr-provider-disabled"),
        (None, "ocr-provider-disabled"),
        ("easyocr", "easyocr-requires-docling"),
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


def test_parse_source_fallback_pdf_wires_refill(monkeypatch):
    from app.knowledge import parsers as P
    import app.knowledge.parsers.ocr as O

    _flags_off(monkeypatch)

    def fake_gemini(blob, pages, model=None):
        assert model == "gemini-2.5-flash-lite"
        return {i: f"صفحه {i + 1}" for i in sorted(pages)}

    monkeypatch.setattr(O, "gemini_ocr_pages", fake_gemini)
    doc = P.parse_source("pdf", "s.pdf", _blank_pdf(2),
                         ocr_provider="gemini", ocr_model="gemini-2.5-flash-lite")
    assert [p.text for p in doc.pages] == ["صفحه 1", "صفحه 2"]
    assert doc.parse_meta["ocr_refilled_pages"] == [0, 1]
    assert "ocr_degraded" not in doc.parse_meta


def test_worker_fallback_scanned_pdf_ready_with_chunks(db, monkeypatch):
    import workers.tasks as T
    import app.knowledge.parsers.ocr as O
    from app.knowledge import parsers as P

    _flags_off(monkeypatch)
    monkeypatch.setattr(O, "gemini_ocr_pages",
                        lambda blob, pages, model=None: {i: "متن اسکن‌شده" for i in pages})
    u, w, kb = _seed_ws(db)
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


def test_describe_image_posts_vision_payload(monkeypatch):
    import httpx as _httpx

    from app.ai.openai_compat import OpenAICompatProvider

    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "شرح"}}]}

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


def test_worker_ocr_degraded_empty_is_failed(db, monkeypatch):
    import workers.tasks as T

    u, w, kb = _seed_ws(db)
    st = FakeStorage()
    src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, type="pdf",
                 filename="s.pdf", storage_key="sk", status="pending")
    db.add(src)
    db.commit()
    st.put("sk", b"%PDF-1.4 fake")
    degraded = ParsedDocument(title="s")
    degraded.parse_meta = {"parser": "easyocr-fa-direct", "ocr_provider": "disabled",
                           "ocr_degraded": True, "ocr_empty_pages": [1],
                           "ocr_reason": "ocr-provider-disabled"}
    monkeypatch.setattr(T, "parse_source", lambda *a, **k: degraded)
    res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                     embed_fn=lambda texts: [[0.01] * 1536 for _ in texts])
    assert res["ok"] is False and "OCR:" in res["error"]
    db.refresh(src)
    assert src.status == "failed" and "OCR:" in src.error


# ---------- boundary (unchanged) ----------

def test_null_multimodal_processor_calls_nothing():
    assert NullMultimodalProcessor().describe_figure("k", "c") == ""
    assert NullMultimodalProcessor.provider_name == "null"
