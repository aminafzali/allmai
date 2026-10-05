"""Ingestion tests: real task core, mocked AI (no network, no services).

Covers: 2-page PDF (page numbers survive), note, audio timestamps,
corrupt-file failure path, and broker-down-safe statuses.
"""

import io
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.common.base import Base
from app.knowledge.models import Chunk, Document, KnowledgeBase, PageSegment, Source
from app.users.models import User
from app.workspaces.models import Workspace, WorkspaceMember
from workers.tasks import run_ingest

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)
FAKE_VEC = [0.01] * 1536


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

    def presigned_get(self, key: str, expires_seconds: int = 900) -> str:
        return f"https://fake-s3/{key}"


@pytest.fixture()
def env():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = TestingSession()
    uid, wid = uuid.uuid4(), uuid.uuid4()
    db.add(User(id=uid, email="u@x.com", password_hash="x", full_name="U"))
    db.add(Workspace(id=wid, name="W", type="shared", owner_user_id=uid))
    db.add(WorkspaceMember(workspace_id=wid, user_id=uid, role="owner"))
    kb_id = uuid.uuid4()
    db.add(KnowledgeBase(id=kb_id, workspace_id=wid, title="KB"))
    db.commit()
    storage = FakeStorage()
    yield db, storage, wid, kb_id
    db.close()


def _source(db, storage, wid, kb_id, type, filename, blob) -> Source:
    src = Source(id=uuid.uuid4(), workspace_id=wid, kb_id=kb_id, type=type,
                 filename=filename, status="pending")
    db.add(src)
    db.flush()
    key = f"k/{src.id}/{filename}"
    storage.put(key, blob)
    src.storage_key = key
    db.commit()
    return src


def fake_embed(texts):
    assert texts, "should not embed zero chunks"
    return [[float(len(t) % 7)] * 1536 for t in texts]


def test_pdf_ingest_preserves_pages(env, monkeypatch):
    from app.core.config import get_settings

    # Hermetic: the live Gemini route must never fire here — the
    # structured path is covered with fakes in test_gemini_structure.py.
    monkeypatch.setattr(get_settings(), "USE_GEMINI_PDF", False)
    db, storage, wid, kb_id = env
    blob = make_pdf(["Chemistry page one content", "Second page acids and bases"])
    src = _source(db, storage, wid, kb_id, "pdf", "book.pdf", blob)

    res = run_ingest(src.id, db=db, storage=storage, embed_fn=fake_embed)
    assert res["ok"] is True and res["chunks"] >= 1
    assert res.get("parser", "fallback") == "fallback"  # USE_GEMINI_PDF off here

    db.refresh(src)
    assert src.status == "ready"
    doc = db.query(Document).filter(Document.source_id == src.id).one()
    assert doc.page_count == 2
    pages = sorted(
        {s.page_no for s in db.query(PageSegment).filter(PageSegment.document_id == doc.id).all()}
    )
    assert pages == [1, 2]
    chunks = db.query(Chunk).filter(Chunk.kb_id == kb_id).all()
    assert chunks and all(len(c.embedding) == 1536 for c in chunks)
    assert any("acids" in c.content for c in chunks)


def test_note_ingest(env):
    import json

    db, storage, wid, kb_id = env
    blob = json.dumps({"title": "N", "content": "Acids donate protons in water."}).encode()
    src = _source(db, storage, wid, kb_id, "note", "n", blob)
    res = run_ingest(src.id, db=db, storage=storage, embed_fn=fake_embed)
    assert res["ok"] is True
    chunks = db.query(Chunk).filter(Chunk.kb_id == kb_id).all()
    assert len(chunks) == 1 and "protons" in chunks[0].content


def test_audio_ingest_preserves_timestamps(env):
    db, storage, wid, kb_id = env
    src = _source(db, storage, wid, kb_id, "audio", "class1.mp3", b"ID3 blob")

    def fake_transcribe(blob, filename):
        return [(0, 30_000, "intro to chemistry"), (30_000, 60_000, "acids part one"),
                (60_000, 100_000, "bases part two")]

    res = run_ingest(src.id, db=db, storage=storage, embed_fn=fake_embed,
                     transcribe_fn=fake_transcribe)
    assert res["ok"] is True and res["chunks"] == 2
    chunks = db.query(Chunk).filter(Chunk.kb_id == kb_id).order_by(Chunk.created_at).all()
    segs = [db.query(PageSegment).filter(PageSegment.id == c.segment_id).one() for c in chunks]
    assert segs[0].start_ms == 0 and segs[0].end_ms == 60_000
    assert segs[1].start_ms == 60_000 and segs[1].end_ms == 100_000


def test_corrupt_file_marks_failed(env, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "USE_GEMINI_PDF", False)
    db, storage, wid, kb_id = env
    src = _source(db, storage, wid, kb_id, "pdf", "broken.pdf", b"definitely not a pdf")
    res = run_ingest(src.id, db=db, storage=storage, embed_fn=fake_embed)
    assert res["ok"] is False
    db.refresh(src)
    assert src.status == "failed" and src.error
    assert db.query(Document).filter(Document.source_id == src.id).count() == 0


def test_reingest_replaces_previous_tree(env, monkeypatch):
    from app.core.config import get_settings
    from app.knowledge.models import PageSegment

    monkeypatch.setattr(get_settings(), "USE_GEMINI_PDF", False)
    db, storage, wid, kb_id = env
    blob = make_pdf(["First ingest text here"])
    src = _source(db, storage, wid, kb_id, "pdf", "book.pdf", blob)

    res1 = run_ingest(src.id, db=db, storage=storage, embed_fn=fake_embed)
    assert res1["ok"] is True
    n1 = db.query(Chunk).filter(Chunk.kb_id == kb_id).count()
    assert n1 >= 1

    res2 = run_ingest(src.id, db=db, storage=storage, embed_fn=fake_embed)
    assert res2["ok"] is True
    assert db.query(Document).filter(Document.source_id == src.id).count() == 1
    assert db.query(Chunk).filter(Chunk.kb_id == kb_id).count() == res2["chunks"]
    db.refresh(src)
    assert src.status == "ready"
    assert src.parse_meta["replaced"]["documents"] == 1
    assert src.parse_meta["replaced"]["chunks"] == n1


def test_reingest_deletes_old_figure_blobs(env, monkeypatch):
    import workers.tasks as T
    from app.core.config import get_settings
    from app.knowledge.parsers.base import ParsedDocument, ParsedElement

    # Persistence focus: vision must not hit the network here.
    monkeypatch.setattr(get_settings(), "DOCUMENT_VISION_ENABLED", False)
    db, storage, wid, kb_id = env
    src = _source(db, storage, wid, kb_id, "pdf", "s.pdf", b"%PDF-1.4 fake")

    def fake_parse(*a, **k):
        return ParsedDocument(
            title="s",
            elements=[ParsedElement(element_id="e1", kind="image", page_no=1,
                                    reading_order=1, text_markdown="[Figure]",
                                    blob=b"\x89PNG\r\n\x1a\n" + b"7" * 20,
                                    metadata={"kind": "image", "element_id": "e1"})])

    orig = T.parse_source
    T.parse_source = lambda *a, **k: fake_parse()
    try:
        run_ingest(src.id, db=db, storage=storage, embed_fn=fake_embed)
    finally:
        T.parse_source = orig
    keys1 = [k for k in storage.objects if "/figures/" in k]
    assert len(keys1) == 1

    T.parse_source = lambda *a, **k: fake_parse()
    try:
        res = run_ingest(src.id, db=db, storage=storage, embed_fn=fake_embed)
    finally:
        T.parse_source = orig
    assert res["ok"] is True
    assert db.query(Document).filter(Document.source_id == src.id).count() == 1
    assert db.query(Chunk).filter(Chunk.kb_id == kb_id).count() == 1
    db.refresh(src)
    assert src.parse_meta["replaced"]["blobs"] == 1
    assert len([k for k in storage.objects if "/figures/" in k]) == 1


def test_transcribe_routes_gemini_models_to_chat(monkeypatch):
    import app.ai.openai_compat as OC
    from app.knowledge.parsers import audio as A

    seen = {}

    class FakeProvider:
        def transcribe_audio(self, blob, fmt, model=None, **kw):
            seen["fmt"] = fmt
            seen["model"] = model
            assert blob == b"audio-bytes"
            return "  سلام دنیا  "

    monkeypatch.setattr(OC, "OpenAICompatProvider", FakeProvider)
    assert A.transcribe(b"audio-bytes", "v.wav", model="gemini-2.5-flash-lite") == [(0, 0, "سلام دنیا")]
    assert seen == {"fmt": "wav", "model": "gemini-2.5-flash-lite"}
    assert A.transcribe(b"audio-bytes", "v.m4a", model="GEMINI-2.0-FLASH") == [(0, 0, "سلام دنیا")]
    assert seen["fmt"] == "mp3"  # m4a rides the mp3 envelope

    class EmptyProvider:
        def transcribe_audio(self, *a, **k):
            return "   "

    monkeypatch.setattr(OC, "OpenAICompatProvider", EmptyProvider)
    assert A.transcribe(b"x", "v.mp3", model="gemini-2.5-flash") == []


def test_whisper_upload_name_ascii_and_error_body(monkeypatch):
    import httpx as _httpx
    from app.knowledge.parsers import audio as A

    seen = {}
    req = _httpx.Request("POST", "http://x")

    def fake_post(*a, **k):
        seen["fname"] = k["files"]["file"][0]
        resp = _httpx.Response(400, text='{"error": "Unrecognized file format"}',
                               request=req)
        raise _httpx.HTTPStatusError("400 Bad Request", request=req, response=resp)

    monkeypatch.setattr(_httpx, "post", fake_post)
    with pytest.raises(A.TranscriptionUnavailable) as ei:
        A.transcribe_whisper_api(b"\xff\xfb...", "اپیزود اول.mp3")
    # GapGPT detects format from the filename: must be ASCII
    assert seen["fname"] == "audio.mp3"
    # the provider body must surface (was swallowed before)
    assert "Unrecognized file format" in str(ei.value)


def test_transcribe_whisper_uses_mime_and_model(monkeypatch):
    import httpx as _httpx
    from app.knowledge.parsers import audio as A

    seen = {}

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"segments": [{"start": 0.0, "end": 2.5, "text": "hi"}]}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        seen["url"] = url
        seen["ctype"] = files["file"][2]
        seen["model"] = data["model"]
        seen["format"] = data["response_format"]
        return Resp()

    monkeypatch.setattr(_httpx, "post", fake_post)
    out = A.transcribe_whisper_api(b"RIFF....", "v.wav", model="whisper-1")
    assert out == [(0, 2500, "hi")]
    assert seen["url"].endswith("/audio/transcriptions")
    assert seen["ctype"] == "audio/wav" and seen["model"] == "whisper-1"
    assert seen["format"] == "verbose_json"
    A.transcribe_whisper_api(b"ID3...", "v.mp3")
    assert seen["ctype"] == "audio/mpeg"
