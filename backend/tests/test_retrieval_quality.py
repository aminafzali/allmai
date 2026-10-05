"""Retrieval quality: Persian normalization, title-aware ranking,
passage expansion, and dominant-source full text."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
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


def _ws_kb(db, title="KB"):
    wid, kid = uuid.uuid4(), uuid.uuid4()
    db.add(Workspace(id=wid, name="W", type="shared",
                     owner_user_id=uuid.uuid4()))
    db.add(KnowledgeBase(id=kid, workspace_id=wid, title=title,
                         scope="workspace"))
    db.commit()
    return wid, kid


def _doc(db, ws, kb, source_name, doc_title, pages):
    """One source+document with one segment+chunk per page text."""
    src = Source(id=uuid.uuid4(), workspace_id=ws, kb_id=kb, type="note",
                 filename=source_name, status="ready")
    db.add(src)
    db.flush()
    doc = Document(id=uuid.uuid4(), workspace_id=ws, source_id=src.id,
                   title=doc_title)
    db.add(doc)
    db.flush()
    chunks = []
    for i, text in enumerate(pages):
        seg = PageSegment(id=uuid.uuid4(), workspace_id=ws,
                          document_id=doc.id, page_no=i + 1, text=text)
        db.add(seg)
        db.flush()
        ch = Chunk(id=uuid.uuid4(), workspace_id=ws, kb_id=kb,
                   segment_id=seg.id, content=text, tokens=5,
                   embedding=[1.0, 0.0], chunk_metadata={})
        db.add(ch)
        chunks.append(ch)
    db.commit()
    return src, doc, chunks


def test_normalize_mirror_and_idempotent():
    from app.knowledge.normalize import fa_tokens, normalize_fa

    assert normalize_fa("ي ك ة ـ‌ًٌٍَُِّْٰ") == "ی ک ه  "
    assert normalize_fa(normalize_fa("كتابِ خوب")) == normalize_fa("كتابِ خوب")
    assert normalize_fa("") == "" and normalize_fa(None) == ""
    assert "و" not in fa_tokens("اسید و باز")
    assert fa_tokens("كتاب‌ها") == ["کتاب", "ها"] or "کتاب" in fa_tokens("كتاب‌ها")


def test_migration_literals_match_python():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "m17", "alembic/versions/0017_normalize_fa.py")
    m17 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m17)
    assert [ord(c) for c in m17.NORM_FROM] == [
        0x64A, 0x643, 0x629, 0x64B, 0x64C, 0x64D, 0x64E, 0x64F,
        0x650, 0x651, 0x652, 0x670]
    assert [ord(c) for c in m17.NORM_TO] == [0x6CC, 0x6A9, 0x647]
    assert ord(m17.TATWEEL) == 0x640 and ord(m17.ZWNJ) == 0x200C


def test_title_boost_beats_vector(db):
    from app.knowledge.retrieval.hybrid import hybrid_search

    wid, kb = _ws_kb(db)
    # NOTE: contents must differ — P1 content-dedup intentionally collapses
    # byte-identical chunks (re-ingest copies); both stay query-irrelevant.
    _doc(db, wid, kb, "other.pdf", "گزارش سالانه",
         ["متن نامرتبط درباره هوا"])
    _doc(db, wid, kb, "acids.pdf", "اسیدها و بازها در شیمی",
         ["متن نامرتبط درباره باران"])
    # vector says doc 1 (embedding match), query names doc 2's title
    hits = hybrid_search(db, wid, "اسیدها شیمی", kb_id=kb, top_k=2,
                         query_vector=[1.0, 0.0],
                         embed_fn=lambda texts: [[1.0, 0.0] for _ in texts],
                         rerank_fn=None)
    assert len(hits) == 2
    assert hits[0].source.filename == "acids.pdf"


def test_arabic_variants_match(db):
    from app.knowledge.retrieval.hybrid import hybrid_search

    wid, kb = _ws_kb(db)
    _doc(db, wid, kb, "k.pdf", "كتاب", ["درباره كتاب خوب"])
    hits = hybrid_search(db, wid, "کتاب", kb_id=kb, top_k=2,
                         query_vector=[0.0, 0.0],
                         embed_fn=lambda texts: [[0.0, 0.0] for _ in texts],
                         rerank_fn=None)
    assert hits and hits[0].source.filename == "k.pdf"


def test_expansion_merges_neighbors(db):
    from app.knowledge.retrieval.hybrid import hybrid_search
    from app.knowledge.retrieval.passages import expand_hit_texts

    wid, kb = _ws_kb(db)
    pages = [f"پاراگراف شماره {i} درباره اسیدها" for i in range(1, 4)]
    _doc(db, wid, kb, "a.pdf", "مقاله اسید", pages)
    hits = hybrid_search(db, wid, "اسیدها", kb_id=kb, top_k=3,
                         query_vector=[1.0, 0.0],
                         embed_fn=lambda texts: [[1.0, 0.0] for _ in texts],
                         rerank_fn=None)
    expanded = expand_hit_texts(db, hits)
    assert expanded
    for text in expanded.values():
        # middle hit pulls both neighbors into one coherent passage
        assert "پاراگراف شماره" in text
        assert len(text) <= 2000 + 1
    # overlap dedupe: no paragraph text repeated across kept windows
    joined = "\n".join(expanded.values())
    assert joined.count("پاراگراف شماره 2") <= 2


def test_fulltext_dominant_and_named(db):
    from app.knowledge.retrieval.passages import maybe_source_fulltext

    wid, kb = _ws_kb(db)
    src, _, _ = _doc(db, wid, kb, "solo.pdf", "تک‌مقاله",
                     ["متن طولانی " * 50])
    merged = [{"chunk_id": "x", "source_id": str(src.id),
               "source": "solo.pdf", "score": 0.9} for _ in range(3)]
    ft = maybe_source_fulltext(db, merged, "سؤال کلی")
    assert ft is not None and ft["filename"] == "solo.pdf"
    assert "متن طولانی" in ft["text"] and len(ft["text"]) <= 12001

    _doc(db, wid, kb, "other.pdf", "مقاله دیگر", ["چیز دیگر"])
    other_id = str(db.query(Source).filter(Source.filename == "other.pdf").one().id)
    merged2 = (merged[:1] + [{"chunk_id": "y", "source_id": other_id,
                              "source": "other.pdf", "score": 0.8}])
    assert maybe_source_fulltext(db, merged2, "سؤال کلی") is None
    # named-article rule fires even without dominance
    named = maybe_source_fulltext(
        db, [{"chunk_id": "z", "source_id": str(src.id),
              "source": "solo.pdf", "score": 0.8}], "تک‌مقاله")
    assert named is not None and named["filename"] == "solo.pdf"


def test_source_titles_skips_garbage_ids(db):
    from app.knowledge.retrieval.passages import _source_titles

    wid, kb = _ws_kb(db)
    src, _, _ = _doc(db, wid, kb, "s.pdf", "T", ["x"])
    out = _source_titles(db, [str(src.id), "garbage", "", None])
    assert out == {str(src.id): "T"}
    assert _source_titles(db, []) == {}
    assert _source_titles(db, ["garbage"]) == {}


def test_fulltext_filename_mention(db):
    from app.knowledge.retrieval.passages import maybe_source_fulltext

    wid, kb = _ws_kb(db)
    src, _, _ = _doc(db, wid, kb, "Executive Summary.docx", "خلاصه مدیریتی",
                     ["متن انگلیسی گزارش " * 20])
    _doc(db, wid, kb, "other.pdf", "چیز دیگر", ["مطلب نامرتبط"])
    other_id = str(db.query(Source).filter(
        Source.filename == "other.pdf").one().id)
    merged = [
        {"chunk_id": "a", "source_id": other_id,
         "source": "other.pdf", "score": 0.9},
        {"chunk_id": "b", "source_id": str(src.id),
         "source": "Executive Summary.docx", "score": 0.5},
    ]
    ft = maybe_source_fulltext(db, merged, "Executive Summary.docx متنش رو ترجمه کن")
    assert ft is not None and ft["filename"] == "Executive Summary.docx"
    assert "متن انگلیسی" in ft["text"]


def test_replace_source_text_reindexes(db):
    from app.knowledge.retrieval.hybrid import hybrid_search
    from app.knowledge.service import replace_source_text

    wid, kb = _ws_kb(db)
    src, _, _ = _doc(db, wid, kb, "n.pdf", "یادداشت",
                     ["متن قدیمی درباره هوا"])
    ws = type("W", (), {"id": wid})()
    user = type("U", (), {"id": uuid.uuid4()})()

    class FakeStorage:
        def __init__(self):
            self.objects = {"blob": b"x"}

        def delete(self, key):
            del self.objects[key]

    storage = FakeStorage()
    out = replace_source_text(
        db, ws, type("K", (), {"id": kb})(), user, src.id,
        "متن جدید درباره اسیدها و بازها در شیمی", storage,
        embed_fn=lambda texts: [[0.2, 0.3] for _ in texts])
    assert out["status"] == "ready" and out["chunks"] >= 1
    assert storage.objects == {"blob": b"x"}  # original blob untouched
    db.refresh(src)
    assert (src.parse_meta or {}).get("manual_text", {}).get("chars", 0) > 0
    # old content gone from RAG, new content searchable
    hits = hybrid_search(db, wid, "هوای قدیمی", kb_id=kb, top_k=5,
                         query_vector=[0.0, 0.0],
                         embed_fn=lambda texts: [[0.0, 0.0] for _ in texts],
                         rerank_fn=None)
    assert all("هوای قدیمی" not in (h.chunk.content or "") for h in hits)
    hits2 = hybrid_search(db, wid, "اسیدها", kb_id=kb, top_k=5,
                          query_vector=[0.0, 0.0],
                          embed_fn=lambda texts: [[0.0, 0.0] for _ in texts],
                          rerank_fn=None)
    assert any("اسیدها" in (h.chunk.content or "") for h in hits2)
    # transcript serves the new text
    from app.knowledge.service import source_transcript

    db.refresh(src)
    assert "اسیدها" in source_transcript(db, src)["text"]


def test_replace_rejects_empty_and_oversize(db):
    import pytest as _pytest

    from app.knowledge.service import replace_source_text

    wid, kb = _ws_kb(db)
    src, _, _ = _doc(db, wid, kb, "n.pdf", "یادداشت", ["x"])
    ws = type("W", (), {"id": wid})()
    user = type("U", (), {"id": uuid.uuid4()})()

    class FakeStorage:
        def delete(self, key):
            pass

    from fastapi import HTTPException

    with _pytest.raises(HTTPException):
        replace_source_text(db, ws, type("K", (), {"id": kb})(), user,
                            src.id, "   ", FakeStorage(), embed_fn=lambda t: [])
    assert db.query(Chunk).count() == 1  # untouched


def test_revise_previews_without_saving(db, monkeypatch):
    from app.knowledge.service import revise_source_text

    wid, kb = _ws_kb(db)
    src, _, _ = _doc(db, wid, kb, "n.pdf", "یادداشت", ["متن اولیه"])
    ws = type("W", (), {"id": wid})()
    user = type("U", (), {"id": uuid.uuid4()})()

    class FakeProvider:
        def generate(self, prompt, **kw):
            assert "متن اولیه" in prompt and "خلاصه" in prompt
            return "متن بازنویسی‌شده"

    monkeypatch.setattr("app.ai.factory.get_chat_provider",
                        lambda db=None: FakeProvider())
    out = revise_source_text(db, ws, type("K", (), {"id": kb})(), user,
                             src.id, "خلاصه کن")
    assert out["revised"] == "متن بازنویسی‌شده"
    # nothing persisted
    from app.knowledge.service import source_transcript

    assert "بازنویسی" not in source_transcript(db, src)["text"]


def test_rerank_title_bonus():
    from types import SimpleNamespace

    from app.knowledge.retrieval.rerank import heuristic_rerank

    def hit(content, filename, score=0.05):
        return SimpleNamespace(
            chunk=SimpleNamespace(content=content),
            source=SimpleNamespace(filename=filename), score=score)

    hits = [hit("متن طولانی نامرتبط " * 10, "zzz.pdf"),
            hit("متن کوتاه", "اسیدها و بازها.pdf")]
    out = heuristic_rerank("اسیدها", hits)
    assert out[0].source.filename == "اسیدها و بازها.pdf"
