"""Video source tests: ingest (audio-track transcription), guards,
retrieval, citation timestamps, cross-workspace denial, upload validation.

Offline: real imageio-ffmpeg binary for probe/extract (hard dependency),
faked transcription (no network), SQLite + FakeStorage + fake embeddings.
"""

import io
import subprocess
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.common.base import Base
from app.core.config import get_settings
from app.knowledge.models import Chunk, Document, KnowledgeBase, PageSegment, Source
from app.knowledge.parsers.video import probe_duration_s, transcribe_video
from app.knowledge.retrieval.hybrid import hybrid_search
from app.users.models import User
from app.workspaces.models import Workspace, WorkspaceMember
from workers.tasks import run_ingest

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)
FAKE_VEC = [0.01] * 1536


def _ffmpeg():
    from imageio_ffmpeg import get_ffmpeg_exe

    return get_ffmpeg_exe()


def make_mp4(seconds: float = 2.0) -> bytes:
    """Tiny mp4 with a real audio track (testsrc + sine)."""
    import os
    import tempfile

    with tempfile.TemporaryDirectory(prefix="allmai-testvid-") as tmp:
        out = os.path.join(tmp, "t.mp4")
        subprocess.run(
            [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i",
             f"testsrc=duration={seconds}:size=128x128:rate=10",
             "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
             "-c:v", "mpeg4", "-c:a", "aac", "-shortest", out],
            capture_output=True, timeout=120, check=True)
        with open(out, "rb") as f:
            return f.read()


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

    storage = FakeStorage()
    yield db, storage, wid, kb_id
    db.close()


def _source(db, storage, wid, kb_id, filename, blob) -> Source:
    src = Source(id=uuid.uuid4(), workspace_id=wid, kb_id=kb_id, type="video",
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


def fake_transcribe(blob, filename):
    assert blob and filename.endswith(".mp3")
    return [(0, 30_000, "video intro to chemistry"),
            (30_000, 60_000, "video acids part one")]


def test_probe_duration(env):
    blob = make_mp4()
    assert 1.5 <= probe_duration_s(blob) <= 2.5


def test_video_ingest_transcript_timestamps(env):
    db, storage, wid, kb_id = env
    src = _source(db, storage, wid, kb_id, "clip.mp4", make_mp4())
    res = run_ingest(src.id, db=db, storage=storage, embed_fn=fake_embed,
                     transcribe_fn=fake_transcribe)
    assert res["ok"] is True and res["chunks"] >= 1
    db.refresh(src)
    assert src.status == "ready"
    assert (src.parse_meta or {}).get("parser") == "video-whisper"
    doc = db.query(Document).filter(Document.source_id == src.id).one()
    assert doc.duration_ms == 60_000
    chunks = db.query(Chunk).filter(Chunk.kb_id == kb_id).all()
    assert any("acids" in c.content for c in chunks)
    # citation timestamps survive on segments
    segs = db.query(PageSegment).filter(PageSegment.document_id == doc.id).all()
    assert any((s.start_ms or 0) > 0 or (s.end_ms or 0) > 0 for s in segs)


def test_video_retrieval_and_citation(env):
    from app.agents.tools import ToolContext, knowledge_search

    db, storage, wid, kb_id = env
    src = _source(db, storage, wid, kb_id, "clip.mp4", make_mp4())
    run_ingest(src.id, db=db, storage=storage, embed_fn=fake_embed,
               transcribe_fn=fake_transcribe)
    hits = hybrid_search(db, wid, "video acids", kb_id, top_k=5,
                         embed_fn=fake_embed, rerank_fn=None)
    assert hits and any("acids" in h.chunk.content for h in hits)
    ctx = ToolContext(db=db, workspace_id=wid, user_id=uuid.uuid4(),
                      embed_fn=fake_embed)
    found = knowledge_search(ctx, "acids", kb_id=kb_id, top_k=5)
    assert found and found[0]["source"] == "clip.mp4"
    assert found[0]["start_ms"] is not None  # timestamp citation


def test_video_cross_workspace_denied(env):
    db, storage, wid, kb_id = env
    src = _source(db, storage, wid, kb_id, "clip.mp4", make_mp4())
    run_ingest(src.id, db=db, storage=storage, embed_fn=fake_embed,
               transcribe_fn=fake_transcribe)
    other = uuid.uuid4()
    assert hybrid_search(db, other, "acids", None, top_k=5,
                         embed_fn=fake_embed, rerank_fn=None) == []
    # NOTE: the worker's tenant bind is enforced by Postgres RLS (live DB);
    # on SQLite there is no RLS layer, so only the app-layer filter above
    # is assertable here. Live RLS denial is covered by test_rls_live.py.


def test_video_too_long_rejected(env, monkeypatch):
    monkeypatch.setattr(get_settings(), "VIDEO_MAX_DURATION_S", 0.5)
    db, storage, wid, kb_id = env
    src = _source(db, storage, wid, kb_id, "clip.mp4", make_mp4())
    res = run_ingest(src.id, db=db, storage=storage, embed_fn=fake_embed,
                     transcribe_fn=fake_transcribe)
    assert res["ok"] is False
    db.refresh(src)
    assert src.status == "failed" and "too long" in (src.error or "")


def test_video_too_large_rejected(env, monkeypatch):
    monkeypatch.setattr(get_settings(), "VIDEO_MAX_BYTES", 10)
    with pytest.raises(Exception):
        transcribe_video(b"x" * 11, "clip.mp4", transcribe_fn=fake_transcribe)


def test_video_upload_validation(monkeypatch):
    from fastapi import HTTPException

    from app.storage.validation import validate_upload

    mp4 = b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 100
    assert validate_upload("video", "clip.mp4", mp4) == "video/mp4"
    with pytest.raises(HTTPException):
        validate_upload("video", "clip.avi", mp4)
    with pytest.raises(HTTPException):
        validate_upload("video", "clip.mp4", b"....")
    monkeypatch.setattr(get_settings(), "VIDEO_MAX_BYTES", 10)
    with pytest.raises(HTTPException):
        validate_upload("video", "clip.mp4", mp4 * 10)
