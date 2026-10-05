"""URL provider tests: detection, subtitle parsing, transcript/text ingest,
retrieval + citation metadata, cross-workspace denial. Fully offline
(provider seam, fake transcription/embeddings, SQLite + FakeStorage)."""

import json
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.common.base import Base
from app.knowledge.models import Chunk, Document, KnowledgeBase, PageSegment, Source
from app.knowledge.parsers.providers import ExtractedURL, detect, extract
from app.knowledge.parsers.providers.youtube import parse_subtitles
from app.knowledge.retrieval.hybrid import hybrid_search
from app.users.models import User
from app.workspaces.models import Workspace, WorkspaceMember
from workers.tasks import run_ingest

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)


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

    class FakeStorage:
        def __init__(self):
            self.objects: dict[str, bytes] = {}

        def put(self, key, data, content_type=""):
            self.objects[key] = data
            return key

        def get(self, key):
            return self.objects[key]

        def delete(self, key):
            self.objects.pop(key, None)

    yield db, FakeStorage(), wid, kb_id
    db.close()


def _url_source(db, storage, wid, kb_id, url, title="T") -> Source:
    blob = json.dumps({"title": title, "url": url}, ensure_ascii=False).encode()
    src = Source(id=uuid.uuid4(), workspace_id=wid, kb_id=kb_id, type="url",
                 filename=title, status="pending")
    db.add(src)
    db.flush()
    key = f"k/{src.id}/descriptor.json"
    storage.put(key, blob)
    src.storage_key = key
    db.commit()
    return src


def fake_embed(texts):
    assert texts, "should not embed zero chunks"
    return [[float(len(t) % 7)] * 1536 for t in texts]


def test_detect():
    assert detect("https://www.youtube.com/watch?v=x") == "youtube"
    assert detect("https://youtu.be/x") == "youtube"
    assert detect("https://www.instagram.com/p/x/") == "instagram"
    assert detect("https://www.aparat.com/v/x") == "aparat"
    assert detect("https://example.com/a") == "generic"
    assert detect("not a url") == "generic"


def test_parse_subtitles_vtt():
    vtt = ("WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n"
           "Hello <b>world</b>\n\n00:01:02.500 --> 00:01:05.000\nSecond line\n")
    segs = parse_subtitles(vtt)
    assert segs == [(1000, 4000, "Hello world"), (62500, 65000, "Second line")]


def test_aparat_videohash_and_smallest():
    from app.knowledge.parsers.providers.aparat import _smallest_mp4, videohash

    assert videohash("https://www.aparat.com/v/k37oi08?x=1") == "k37oi08"
    assert videohash("https://example.com/") == ""
    info = {"file_link_all": [
        {"text": "1080p", "urls": ["https://c/x-1080p.apt"]},
        {"text": "12.72 مگابایت", "urls": ["https://c/x-144p.mp4"]},
        {"text": "17.35 مگابایت", "urls": ["https://c/x-240p.mp4"]}],
        "file_link": "https://c/x-main.apt"}
    assert _smallest_mp4(info) == "https://c/x-144p.mp4"
    assert _smallest_mp4({}) == ""


def test_url_fallback_records_provider_error(monkeypatch):
    import app.knowledge.parsers.providers as P
    from app.knowledge.parsers.fallback import parse_url_descriptor

    def boom(url, fetch_fn=None, transcribe_fn=None, provider_overrides=None):
        raise RuntimeError("net down")

    monkeypatch.setattr(P, "extract", boom)
    blob = json.dumps({"title": "T",
                       "url": "https://www.youtube.com/watch?v=x"}).encode()
    doc = parse_url_descriptor(blob)
    assert doc.parse_meta.get("parser") == "url-descriptor-fallback"
    assert "youtube" in doc.parse_meta.get("provider_error", "")


def test_extract_override_seam(monkeypatch):
    def fake(url):
        return ExtractedURL(provider="youtube", url=url, title="Vid",
                            kind="transcript",
                            segments=[(0, 5000, "caption one")])

    out = extract("https://www.youtube.com/watch?v=x",
                  provider_overrides={"youtube": fake})
    assert out.kind == "transcript" and out.segments[0][2] == "caption one"


def test_url_video_ingest_transcript(env, monkeypatch):
    # fallback imports `extract` lazily from the providers package, so
    # patching the package attribute reroutes the whole pipeline offline.
    import app.knowledge.parsers.providers as P

    def fake_extract(url, fetch_fn=None, transcribe_fn=None,
                     provider_overrides=None):
        return ExtractedURL(provider="youtube", url=url, title="Chem Vid",
                            kind="transcript",
                            segments=[(0, 30_000, "video intro to chemistry"),
                                      (30_000, 60_000, "video acids part one"),
                                      (60_000, 100_000, "video bases part two")])

    monkeypatch.setattr(P, "extract", fake_extract)
    db, storage, wid, kb_id = env
    src = _url_source(db, storage, wid, kb_id,
                      "https://www.youtube.com/watch?v=x", "Chem Vid")
    res = run_ingest(src.id, db=db, storage=storage, embed_fn=fake_embed)
    assert res["ok"] is True and res["chunks"] == 2  # ~45s windows
    db.refresh(src)
    assert src.status == "ready"
    assert src.parse_meta.get("provider") == "youtube"
    assert src.parse_meta.get("url") == "https://www.youtube.com/watch?v=x"
    doc = db.query(Document).filter(Document.source_id == src.id).one()
    assert doc.duration_ms == 100_000
    segs = db.query(PageSegment).filter(PageSegment.document_id == doc.id).all()
    assert (segs[0].start_ms, segs[0].end_ms) == (0, 60_000)
    assert (segs[1].start_ms, segs[1].end_ms) == (60_000, 100_000)


def test_url_text_ingest(env, monkeypatch):
    import app.knowledge.parsers.providers as P

    def fake_extract(url, fetch_fn=None, transcribe_fn=None,
                     provider_overrides=None):
        return ExtractedURL(provider="aparat", url=url, title="Aparat Vid",
                            kind="text", text="Aparat video description here",
                            meta={"oembed": True})

    monkeypatch.setattr(P, "extract", fake_extract)
    db, storage, wid, kb_id = env
    src = _url_source(db, storage, wid, kb_id, "https://www.aparat.com/v/x")
    res = run_ingest(src.id, db=db, storage=storage, embed_fn=fake_embed)
    assert res["ok"] is True
    chunks = db.query(Chunk).filter(Chunk.kb_id == kb_id).all()
    assert any("Aparat video description" in c.content for c in chunks)


def test_url_retrieval_citation_and_isolation(env, monkeypatch):
    import app.knowledge.parsers.providers as P
    from app.agents.tools import ToolContext, knowledge_search

    def fake_extract(url, fetch_fn=None, transcribe_fn=None,
                     provider_overrides=None):
        return ExtractedURL(provider="youtube", url=url, title="Chem Vid",
                            kind="transcript",
                            segments=[(0, 30_000, "acids in this video lecture")])

    monkeypatch.setattr(P, "extract", fake_extract)
    db, storage, wid, kb_id = env
    src = _url_source(db, storage, wid, kb_id,
                      "https://www.youtube.com/watch?v=x", "Chem Vid")
    run_ingest(src.id, db=db, storage=storage, embed_fn=fake_embed)
    ctx = ToolContext(db=db, workspace_id=wid, user_id=uuid.uuid4(),
                      embed_fn=fake_embed)
    found = knowledge_search(ctx, "acids", kb_id=kb_id, top_k=5)
    assert found and found[0]["source"] == "Chem Vid"
    assert found[0]["start_ms"] == 0 and found[0]["end_ms"] == 30_000
    other = uuid.uuid4()
    assert hybrid_search(db, other, "acids", None, top_k=5,
                         embed_fn=fake_embed, rerank_fn=None) == []
