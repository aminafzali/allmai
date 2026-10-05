"""Phase 3 (D) Vision tests: triage, eager order, failure behaviour.

All vision calls are stubbed (no network). The single live opt-in test
lives in test_live_vision.py (RUN_LIVE_VISION=1, skipped otherwise).
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
from app.knowledge.parsers.base import ParsedDocument, ParsedElement, ParsedPage
from app.knowledge.parsers.multimodal import (
    GeminiVisionProcessor,
    caption_is_data_carrying,
    triage_figures,
)
from app.users.models import User
from app.workspaces.models import Workspace, WorkspaceMember
from workers.tasks import run_ingest

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
    pw = hash_password("password123")
    u = User(id=uuid.uuid4(), email="u@x.com", password_hash=pw)
    db.add(u)
    w = uuid.uuid4()
    db.add(Workspace(id=w, name="W", type="shared", owner_user_id=u.id))
    db.add(WorkspaceMember(workspace_id=w, user_id=u.id, role="owner"))
    kb = KnowledgeBase(id=uuid.uuid4(), workspace_id=w, title="KB", scope="workspace")
    db.add(kb)
    db.commit()
    return w, kb


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes, content_type: str = "") -> str:
        self.objects[key] = data
        return key

    def get(self, key: str) -> bytes:
        return self.objects[key]


def _img_draft(eid, caption, page=1, blob=b"\x89PNG" + b"0" * 20):
    from app.knowledge.chunking import ChunkDraft

    return ChunkDraft(text=f"[Figure on page {page}: {caption or 'figure without caption'}]",
                      tokens=8, page_no=page,
                      metadata={"kind": "image", "element_id": eid,
                                "caption": caption, "needs_vision": True},
                      blob=blob)


def _txt_draft(text, page=1):
    from app.knowledge.chunking import ChunkDraft

    return ChunkDraft(text=text, tokens=5, page_no=page,
                      metadata={"kind": "text", "element_id": "et"})


# ---------- triage ----------

def test_caption_data_rule():
    assert caption_is_data_carrying("نمودار فروش ماهانه جمعاً ۲۴۰ میلیون", 20) is True
    assert caption_is_data_carrying("نمودار فروش میوه‌ها", 20) is False  # no digit
    assert caption_is_data_carrying("نمودار ۱", 20) is False  # too short
    assert caption_is_data_carrying("", 20) is False


def test_triage_splits_correctly():
    drafts = [
        _txt_draft("hello"),
        _img_draft("a", "نمودار فروش ماهانه جمعاً ۲۴۰ میلیون"),  # caption-data
        _img_draft("b", "نمودار ۱"),  # describe
        _img_draft("c", "", page=2),  # describe
    ]
    pages = {1: "x" * 10, 2: "y" * 10}
    go, skipped = triage_figures(drafts, pages, max_figures=5,
                                 caption_min_chars=20, page_text_min=200)
    assert go == [2, 3]
    assert skipped == {1: "caption-data"}


def test_triage_page_rich_and_over_cap():
    drafts = [_img_draft(f"e{i}", "", page=1) for i in range(4)]
    pages = {1: "z" * 500}  # long but digit-less prose -> NOT data-carrying
    go, skipped = triage_figures(drafts, pages, max_figures=5,
                                 caption_min_chars=20, page_text_min=200)
    assert go == [0, 1, 2, 3] and skipped == {}
    pages = {1: "z" * 190 + " فروش ۲۴۰ میلیون"}  # rich AND has digits
    go, skipped = triage_figures(drafts, pages, max_figures=5,
                                 caption_min_chars=20, page_text_min=200)
    assert go == [] and set(skipped.values()) == {"page-rich"}
    go2, skipped2 = triage_figures(drafts, {}, max_figures=2,
                                   caption_min_chars=20, page_text_min=200)
    assert go2 == [0, 1]
    assert skipped2 == {2: "over-cap", 3: "over-cap"}


# ---------- processor contract ----------

def test_processor_marks_caption_unverified():
    seen = {}

    def fake_describe(blob, prompt, **kw):
        seen["prompt"] = prompt
        assert blob.startswith(b"\x89PNG")
        return "desc"

    p = GeminiVisionProcessor(describe_fn=fake_describe)
    assert p.describe_figure(b"\x89PNGxy", "نمودار ۱") == "desc"
    assert "تأییدنشده" in seen["prompt"] and "نمودار ۱" in seen["prompt"]


def test_processor_empty_or_error_is_loud():
    p = GeminiVisionProcessor(describe_fn=lambda *a, **k: "  ")
    with pytest.raises(RuntimeError):
        p.describe_figure(b"x", "")

    def boom(*a, **k):
        raise RuntimeError("net down")

    with pytest.raises(RuntimeError):
        GeminiVisionProcessor(describe_fn=boom).describe_figure(b"x", "")


# ---------- worker eager stage ----------

def _enable_vision(monkeypatch, **kw):
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "DOCUMENT_VISION_ENABLED", True)
    for k, v in kw.items():
        monkeypatch.setattr(s, k, v)


def _doc_with_figure():
    return ParsedDocument(
        title="s",
        pages=[ParsedPage(page_no=1, text="short intro")],
        elements=[
            ParsedElement(element_id="e1", kind="text", page_no=1, reading_order=1,
                          text_markdown="short intro",
                          metadata={"kind": "text", "element_id": "e1"}),
            ParsedElement(element_id="e2", kind="image", page_no=1, reading_order=2,
                          text_markdown="[Figure on page 1: نمودار ۱]",
                          blob=b"\x89PNG\r\n\x1a\n" + b"1" * 50, caption="نمودار ۱",
                          metadata={"kind": "image", "element_id": "e2",
                                    "caption": "نمودار ۱", "needs_vision": True}),
        ])


def test_eager_vision_updates_chunk_not_segment(db, monkeypatch):
    import workers.tasks as T
    from app.knowledge.parsers import multimodal as MM

    w, kb = _seed(db)
    st = FakeStorage()
    src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, type="pdf",
                 filename="v.pdf", storage_key="vk", status="pending")
    db.add(src)
    db.commit()
    st.put("vk", b"%PDF-1.4 fake")
    monkeypatch.setattr(T, "parse_source", lambda *a, **k: _doc_with_figure())
    _enable_vision(monkeypatch)
    monkeypatch.setattr(MM.GeminiVisionProcessor, "describe_figure",
                        lambda self, blob, caption="": "سیب: ۴۰، پرتقال: ۶۵")
    seen_texts = []
    res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                     embed_fn=lambda texts: (seen_texts.extend(texts),
                                             [[0.01] * 1536 for _ in texts])[1])
    assert res["ok"] is True
    assert res["vision"]["processed"] == 1
    chunks = db.query(Chunk).all()
    img = next(c for c in chunks if c.chunk_metadata.get("kind") == "image")
    # chunk carries caption + description AND was embedded with it
    assert "نمودار ۱" in img.content and "سیب: ۴۰" in img.content
    assert any("سیب: ۴۰" in t for t in seen_texts)
    assert img.chunk_metadata["vision_model"]
    assert img.chunk_metadata["vision_at"]
    # segment stays original (locked D3)
    seg = db.query(PageSegment).filter(PageSegment.id == img.segment_id).one()
    assert "سیب: ۴۰" not in seg.text
    assert seg.text == "[Figure on page 1: نمودار ۱]"


def test_vision_disabled_by_default_calls_nothing(db, monkeypatch):
    import workers.tasks as T
    from app.core.config import get_settings
    from app.knowledge.parsers import multimodal as MM

    # Hermetic: force the flag off (a local .env may enable vision).
    monkeypatch.setattr(get_settings(), "DOCUMENT_VISION_ENABLED", False)
    w, kb = _seed(db)
    st = FakeStorage()
    src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, type="pdf",
                 filename="v.pdf", storage_key="vk", status="pending")
    db.add(src)
    db.commit()
    st.put("vk", b"%PDF-1.4 fake")
    monkeypatch.setattr(T, "parse_source", lambda *a, **k: _doc_with_figure())

    def boom(self, blob, caption=""):
        raise AssertionError("vision must not run when disabled")

    monkeypatch.setattr(MM.GeminiVisionProcessor, "describe_figure", boom)
    res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                     embed_fn=lambda texts: [[0.01] * 1536 for _ in texts])
    assert res["ok"] is True
    assert res["vision"] == {"processed": 0, "skipped": 0, "failed": 0,
                             "skipped_reasons": {}}
    img = next(c for c in db.query(Chunk).all()
               if c.chunk_metadata.get("kind") == "image")
    assert img.content == "[Figure on page 1: نمودار ۱]"
    assert "vision_description" not in img.chunk_metadata


def test_vision_failure_keeps_placeholder(db, monkeypatch):
    import workers.tasks as T
    from app.knowledge.parsers import multimodal as MM

    w, kb = _seed(db)
    st = FakeStorage()
    src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, type="pdf",
                 filename="v.pdf", storage_key="vk", status="pending")
    db.add(src)
    db.commit()
    st.put("vk", b"%PDF-1.4 fake")
    monkeypatch.setattr(T, "parse_source", lambda *a, **k: _doc_with_figure())
    _enable_vision(monkeypatch)

    def boom(self, blob, caption=""):
        raise RuntimeError("provider down")

    monkeypatch.setattr(MM.GeminiVisionProcessor, "describe_figure", boom)
    res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                     embed_fn=lambda texts: [[0.01] * 1536 for _ in texts])
    assert res["ok"] is True and res["vision"]["failed"] == 1
    img = next(c for c in db.query(Chunk).all()
               if c.chunk_metadata.get("kind") == "image")
    assert img.content == "[Figure on page 1: نمودار ۱]"  # OUR placeholder, no confabulation
    assert "vision_failed" in img.chunk_metadata


def test_worker_passes_admin_ocr_provider(db, monkeypatch):
    import workers.tasks as T
    from app.ai.settings import upsert_setting

    w, kb = _seed(db)
    upsert_setting(db, "ocr.provider", {"provider": "gemini"})
    st = FakeStorage()
    src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, type="pdf",
                 filename="o.pdf", storage_key="ok", status="pending")
    db.add(src)
    db.commit()
    st.put("ok", b"%PDF-1.4 fake")
    seen = {}

    def fake_parse(*a, **k):
        seen.update(k)
        return ParsedDocument(pages=[])

    monkeypatch.setattr(T, "parse_source", fake_parse)
    run_ingest(src.id, workspace_id=w, db=db, storage=st,
               embed_fn=lambda texts: [[0.01] * 1536 for _ in texts])
    assert seen.get("ocr_provider") == "gemini"


def test_worker_persists_parse_meta(db, monkeypatch):
    import workers.tasks as T
    from app.knowledge.parsers import multimodal as MM

    w, kb = _seed(db)
    st = FakeStorage()
    src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, type="pdf",
                 filename="m.pdf", storage_key="mk", status="pending")
    db.add(src)
    db.commit()
    st.put("mk", b"%PDF-1.4 fake")
    monkeypatch.setattr(T, "parse_source", lambda *a, **k: _doc_with_figure())
    _enable_vision(monkeypatch)
    monkeypatch.setattr(MM.GeminiVisionProcessor, "describe_figure",
                        lambda self, blob, caption="": "ok desc ۱۲۳")
    res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                     embed_fn=lambda texts: [[0.01] * 1536 for _ in texts])
    assert res["ok"] is True
    db.refresh(src)
    assert src.parse_meta["vision"]["processed"] == 1
    assert src.parse_meta["chunks"] == 2


def test_vision_uncertain_flagged_not_silent(db, monkeypatch):
    import workers.tasks as T
    from app.knowledge.parsers import multimodal as MM

    w, kb = _seed(db)
    st = FakeStorage()
    src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, type="pdf",
                 filename="u.pdf", storage_key="uk", status="pending")
    db.add(src)
    db.commit()
    st.put("uk", b"%PDF-1.4 fake")
    monkeypatch.setattr(T, "parse_source", lambda *a, **k: _doc_with_figure())
    _enable_vision(monkeypatch)
    monkeypatch.setattr(MM.GeminiVisionProcessor, "describe_figure",
                        lambda self, blob, caption="": "مقادیر نامشخص است")
    res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                     embed_fn=lambda texts: [[0.01] * 1536 for _ in texts])
    assert res["ok"] is True and res["vision"]["processed"] == 1
    img = next(c for c in db.query(Chunk).all()
               if c.chunk_metadata.get("kind") == "image")
    # honest output kept, flagged uncertain — never presented as verified
    assert img.chunk_metadata.get("vision_uncertain") is True
    assert "نامشخص" in img.content


def test_vision_cap_enforced(db, monkeypatch):
    import workers.tasks as T
    from app.knowledge.parsers import multimodal as MM
    from app.knowledge.chunking import ChunkDraft

    w, kb = _seed(db)
    st = FakeStorage()
    src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, type="pdf",
                 filename="v.pdf", storage_key="vk", status="pending")
    db.add(src)
    db.commit()
    st.put("vk", b"%PDF-1.4 fake")
    doc = ParsedDocument(title="s", pages=[],
                         elements=[ParsedElement(element_id=f"e{i}", kind="image",
                                                 page_no=1, reading_order=i,
                                                 text_markdown=f"[Figure {i}]",
                                                 blob=b"\x89PNG" + bytes([i]) * 10,
                                                 caption="",
                                                 metadata={"kind": "image",
                                                           "element_id": f"e{i}"})
                                   for i in range(4)])
    monkeypatch.setattr(T, "parse_source", lambda *a, **k: doc)
    _enable_vision(monkeypatch, DOC_VISION_MAX_FIGURES=2)
    n = {"c": 0}

    def fake_describe(self, blob, caption=""):
        n["c"] += 1
        return f"desc {n['c']}"

    monkeypatch.setattr(MM.GeminiVisionProcessor, "describe_figure", fake_describe)
    res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                     embed_fn=lambda texts: [[0.01] * 1536 for _ in texts])
    assert n["c"] == 2
    assert res["vision"]["processed"] == 2
    assert res["vision"]["skipped_reasons"].get("over-cap") == 2


# ---------- fallback figure extraction (no docling) ----------

def _fake_reader_factory(pages):
    class FakeReader:
        def __init__(self, blob):
            self.pages = pages

    return FakeReader


def test_pdf_figure_drafts_keeps_big_drops_icons(monkeypatch):
    from PIL import Image

    from app.knowledge.chunking import pdf_figure_drafts
    from types import SimpleNamespace

    big = Image.new("RGB", (700, 420), "white")
    icon = Image.new("RGB", (20, 20), "red")

    class Img:
        def __init__(self, image):
            self.image = image

    pages = [SimpleNamespace(images=[Img(big), Img(icon)]),
             SimpleNamespace(images=[Img(icon)])]
    monkeypatch.setattr("pypdf.PdfReader", _fake_reader_factory(pages))
    drafts = pdf_figure_drafts(b"%PDF-fake", max_figures=5, min_px=120)
    assert len(drafts) == 1
    d = drafts[0]
    assert d.page_no == 1 and d.blob.startswith(b"\x89PNG")
    assert d.metadata["kind"] == "figure" and d.metadata["caption"] == ""
    assert d.metadata["element_id"] == "fallback-p1-img0"
    assert d.text == "[Figure on page 1]"


def test_pdf_figure_drafts_largest_first_and_capped(monkeypatch):
    from PIL import Image

    from app.knowledge.chunking import pdf_figure_drafts
    from types import SimpleNamespace

    class Img:
        def __init__(self, image):
            self.image = image

    pages = [SimpleNamespace(images=[
        Img(Image.new("RGB", (200, 200), "white")),
        Img(Image.new("RGB", (500, 500), "white")),
        Img(Image.new("RGB", (300, 300), "white"))])]
    monkeypatch.setattr("pypdf.PdfReader", _fake_reader_factory(pages))
    drafts = pdf_figure_drafts(b"%PDF-fake", max_figures=2, min_px=120)
    assert len(drafts) == 2  # 500px then 300px, 200px cut by cap
    assert drafts[0].metadata["element_id"] == "fallback-p1-img1"
    assert drafts[1].metadata["element_id"] == "fallback-p1-img2"


def test_pdf_figure_drafts_never_raises(monkeypatch):
    from app.knowledge.chunking import pdf_figure_drafts
    from types import SimpleNamespace

    class Bad:
        @property
        def images(self):
            raise RuntimeError("weird xref")

    monkeypatch.setattr("pypdf.PdfReader",
                        _fake_reader_factory([Bad()]))

    def boom_reader(blob):
        raise RuntimeError("not a pdf")

    assert pdf_figure_drafts(b"junk") == []
    monkeypatch.setattr("pypdf.PdfReader", boom_reader)
    assert pdf_figure_drafts(b"junk") == []


def test_worker_fallback_figures_described(db, monkeypatch):
    import workers.tasks as T
    from app.core.config import get_settings
    from app.knowledge.chunking import ChunkDraft
    from app.knowledge.parsers import multimodal as MM

    monkeypatch.setattr(get_settings(), "DOCUMENT_VISION_ENABLED", True)
    w, kb = _seed(db)
    st = FakeStorage()
    src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, type="pdf",
                 filename="v.pdf", storage_key="vk", status="pending")
    db.add(src)
    db.commit()
    st.put("vk", b"%PDF-1.4 fake")
    monkeypatch.setattr(T, "parse_source", lambda *a, **k: ParsedDocument(
        title="s", pages=[ParsedPage(page_no=1, text="short intro")]))
    made = [ChunkDraft(text="[Figure on page 1]", tokens=8, page_no=1,
                       metadata={"kind": "figure", "element_id": "fallback-p1-img0",
                                 "caption": ""}, blob=b"\x89PNG" + b"9" * 20)]
    monkeypatch.setattr(T, "_fallback_figure_drafts", lambda blob: made)
    monkeypatch.setattr(MM.GeminiVisionProcessor, "describe_figure",
                        lambda self, blob, caption="": "میله‌ها: الف ۱۰")
    res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                     embed_fn=lambda texts: [[0.01] * 1536 for _ in texts])
    assert res["vision"]["processed"] == 1
    img = next(c for c in db.query(Chunk).all()
               if c.chunk_metadata.get("kind") == "figure")
    # chunk content is Persian-normalized at ingest (ZWNJ -> space)
    assert "میله ها" in img.content
    key = img.chunk_metadata.get("storage_key", "")
    assert key.endswith(".png") and st.get(key).startswith(b"\x89PNG")


def test_worker_fallback_figures_skipped_when_disabled(db, monkeypatch):
    import workers.tasks as T
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "DOCUMENT_VISION_ENABLED", False)

    def boom(blob):
        raise AssertionError("extraction must not run when disabled")

    monkeypatch.setattr(T, "_fallback_figure_drafts", boom)
    w, kb = _seed(db)
    st = FakeStorage()
    src = Source(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, type="pdf",
                 filename="v.pdf", storage_key="vk", status="pending")
    db.add(src)
    db.commit()
    st.put("vk", b"%PDF-1.4 fake")
    monkeypatch.setattr(T, "parse_source", lambda *a, **k: ParsedDocument(
        title="s", pages=[ParsedPage(page_no=1, text="short intro")]))
    res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                     embed_fn=lambda texts: [[0.01] * 1536 for _ in texts])
    assert res["ok"] is True
    assert res["vision"] == {"processed": 0, "skipped": 0, "failed": 0,
                             "skipped_reasons": {}}
    assert all(c.chunk_metadata.get("kind") != "figure"
               for c in db.query(Chunk).all())
