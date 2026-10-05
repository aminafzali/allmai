"""Knowledge API tests: KB CRUD, upload validation, link intake, isolation.

Storage is faked (in-memory); the Celery broker is absent so sources stay
`pending` — the enqueue path is broker-down safe by design.
"""

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.common.base import Base
from app.core.database import get_db
from app.core.security import hash_password
from app.main import app
from app.storage.s3 import get_storage
from app.users.models import User

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)


class FakeStorage:
    provider_name = "fake"

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes, content_type: str = "") -> str:
        self.objects[key] = data
        return key

    def get(self, key: str) -> bytes:
        try:
            return self.objects[key]
        except KeyError:
            raise FileNotFoundError(key)

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self.objects

    def presigned_get(self, key: str, expires_seconds: int = 900) -> str:
        return f"https://fake-s3/{key}?exp={expires_seconds}"


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db):
    fake = FakeStorage()

    def override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_storage] = lambda: fake
    yield TestClient(app), fake
    app.dependency_overrides.clear()


def _user(db, email) -> User:
    u = User(id=uuid.uuid4(), email=email, password_hash=hash_password("password123"), full_name=email)
    db.add(u)
    db.commit()
    return u


def _token(client, email) -> str:
    return client.post("/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]


def _h(t) -> dict:
    return {"Authorization": f"Bearer {t}"}


PDF = b"%PDF-1.7 fake body for tests"
MP3 = b"ID3\x04\x00\x00\x00 fake audio"


def test_kb_and_source_flows(client):
    c, fake = client
    with TestingSession() as s:
        _user(s, "a@x.com")
        _user(s, "b@x.com")
    ta, tb = _token(c, "a@x.com"), _token(c, "b@x.com")

    ws = c.post("/workspaces", json={"name": "Chem", "type": "shared"}, headers=_h(ta)).json()
    wid = ws["id"]

    kb = c.post(f"/workspaces/{wid}/knowledge-bases", json={"title": "Chemistry KB"}, headers=_h(ta))
    assert kb.status_code == 201, kb.text
    kb_id = kb.json()["id"]
    assert len(c.get(f"/workspaces/{wid}/knowledge-bases", headers=_h(ta)).json()) == 1

    # outsider sees nothing
    assert c.get(f"/workspaces/{wid}/knowledge-bases", headers=_h(tb)).status_code == 404
    assert c.post(f"/workspaces/{wid}/knowledge-bases", json={"title": "Evil"}, headers=_h(tb)).status_code == 404

    # valid PDF upload
    up = c.post(
        f"/workspaces/{wid}/knowledge-bases/{kb_id}/sources",
        data={"type": "pdf"},
        files={"file": ("textbook.pdf", io.BytesIO(PDF), "application/pdf")},
        headers=_h(ta),
    )
    assert up.status_code == 201, up.text
    body = up.json()
    assert body["status"] in ("pending", "processing")
    key = next(k for k in fake.objects if k.endswith("textbook.pdf"))
    assert key.startswith(f"workspaces/{wid}/kb/{kb_id}/sources/")
    assert fake.objects[key] == PDF

    # audio upload
    au = c.post(
        f"/workspaces/{wid}/knowledge-bases/{kb_id}/sources",
        data={"type": "audio"},
        files={"file": ("class1.mp3", io.BytesIO(MP3), "audio/mpeg")},
        headers=_h(ta),
    )
    assert au.status_code == 201, au.text

    # rejections: exe, mismatched content, video, wrong-type link
    bad_ext = c.post(
        f"/workspaces/{wid}/knowledge-bases/{kb_id}/sources",
        data={"type": "pdf"},
        files={"file": ("evil.exe", io.BytesIO(b"MZ..."), "application/octet-stream")},
        headers=_h(ta),
    )
    assert bad_ext.status_code == 422
    mismatch = c.post(
        f"/workspaces/{wid}/knowledge-bases/{kb_id}/sources",
        data={"type": "image"},
        files={"file": ("pic.png", io.BytesIO(PDF), "image/png")},
        headers=_h(ta),
    )
    assert mismatch.status_code == 422
    video = c.post(
        f"/workspaces/{wid}/knowledge-bases/{kb_id}/sources",
        data={"type": "video"},
        files={"file": ("v.mp4", io.BytesIO(b"...."), "video/mp4")},
        headers=_h(ta),
    )
    assert video.status_code == 422
    wrong_link = c.post(
        f"/workspaces/{wid}/knowledge-bases/{kb_id}/sources/link",
        json={"type": "pdf", "title": "x"},
        headers=_h(ta),
    )
    assert wrong_link.status_code == 422

    # note + url intake
    note = c.post(
        f"/workspaces/{wid}/knowledge-bases/{kb_id}/sources/link",
        json={"type": "note", "title": "Teacher note", "content": "Acids donate protons."},
        headers=_h(ta),
    )
    assert note.status_code == 201, note.text
    url = c.post(
        f"/workspaces/{wid}/knowledge-bases/{kb_id}/sources/link",
        json={"type": "url", "title": "Ref", "url": "https://example.com/chem"},
        headers=_h(ta),
    )
    assert url.status_code == 201, url.text

    sources = c.get(f"/workspaces/{wid}/knowledge-bases/{kb_id}/sources", headers=_h(ta)).json()
    assert len(sources) == 4

    # authenticated download: member ok with exact bytes, outsider 404
    dl = c.get(f"/workspaces/{wid}/sources/{body['id']}/download", headers=_h(ta))
    assert dl.status_code == 200 and dl.content == PDF
    assert c.get(f"/workspaces/{wid}/sources/{body['id']}/download", headers=_h(tb)).status_code == 404
    # presigned urls are an s3-only feature
    assert c.get(f"/workspaces/{wid}/sources/{body['id']}/download-url", headers=_h(ta)).status_code == 409


def test_source_transcript_view(client):
    from app.knowledge.models import Document, PageSegment, Source
    from app.workspaces.models import Workspace, WorkspaceMember
    from app.users.models import User as _User

    c, fake = client
    with TestingSession() as s:
        u = _User(id=uuid.uuid4(), email="t@x.com",
                  password_hash=hash_password("password123"), full_name="t")
        s.add(u)
        wid = uuid.uuid4()
        s.add(Workspace(id=wid, name="W", type="shared", owner_user_id=u.id))
        s.add(WorkspaceMember(workspace_id=wid, user_id=u.id, role="owner"))
        from app.knowledge.models import KnowledgeBase
        kb_id = uuid.uuid4()
        s.add(KnowledgeBase(id=kb_id, workspace_id=wid, title="KB"))
        sid = uuid.uuid4()
        s.add(Source(id=sid, workspace_id=wid, kb_id=kb_id, type="audio",
                     filename="v.mp3", status="ready"))
        did = uuid.uuid4()
        s.add(Document(id=did, workspace_id=wid, source_id=sid, title="v"))
        s.add(PageSegment(document_id=did, start_ms=0, end_ms=2000, text="سلام"))
        s.add(PageSegment(document_id=did, start_ms=2000, end_ms=4000, text="دنیا"))
        s.add(PageSegment(document_id=did, start_ms=4000, end_ms=5000, text="   "))
        s.commit()
    t = _token(c, "t@x.com")
    r = c.get(f"/workspaces/{wid}/knowledge-bases/{kb_id}/sources/{sid}/transcript",
              headers=_h(t))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"] == "v.mp3" and body["status"] == "ready"
    assert [sg["text"] for sg in body["segments"]] == ["سلام", "دنیا"]
    assert body["segments"][0]["start_ms"] == 0
    assert body["text"] == "سلام\n\nدنیا"
    # outsider gets 404 (no existence leak)
    with TestingSession() as s:
        _user(s, "o@x.com")
    to = _token(c, "o@x.com")
    assert c.get(f"/workspaces/{wid}/knowledge-bases/{kb_id}/sources/{sid}/transcript",
                 headers=_h(to)).status_code == 404


def test_meeting_save_is_explicit_one_source_with_original_audio(client):
    """Meeting drafts are not sources until explicit save; on save the
    recorded audio blob and the approved transcript share ONE indexed
    source (no second STT pass, no duplicate docs)."""
    from app.knowledge.models import Chunk, Document, PageSegment, Source
    from app.knowledge.retrieval.hybrid import get_embed_fn

    c, fake = client
    with TestingSession() as s:
        u = _user(s, "meeting@x.com")
    token = _token(c, "meeting@x.com")
    h = _h(token)
    wid = c.post("/workspaces", headers=h, json={"name": "Meetings", "type": "shared"}).json()["id"]
    kb = c.post(f"/workspaces/{wid}/knowledge-bases", headers=h, json={"title": "KB"}).json()["id"]

    # Merely starting/recording a meeting performs no POST, thus the KB is empty.
    assert c.get(f"/workspaces/{wid}/knowledge-bases/{kb}/sources", headers=h).json() == []

    app.dependency_overrides[get_embed_fn] = lambda: (lambda texts: [[0.1] * 1536 for _ in texts])
    audio = b"\x1a\x45\xdf\xa3" + b"test-webm-audio"
    saved = c.post(
        f"/workspaces/{wid}/knowledge-bases/{kb}/sources/meeting",
        headers=h,
        data={"title": "جلسه برنامه‌ریزی", "text": "[علی ۱۰:۰۰]\nبحث درباره زمان تحویل\n\nیادداشت‌های جلسه:\nـ تماس با تیم"},
        files={"file": ("meeting.webm", io.BytesIO(audio), "audio/webm")},
    )
    assert saved.status_code == 201, saved.text
    row = saved.json()
    assert row["type"] == "video" and row["status"] == "ready"
    assert row["parse_meta"]["meeting_session"] is True
    assert audio in fake.objects.values()

    transcript = c.get(
        f"/workspaces/{wid}/knowledge-bases/{kb}/sources/{row['id']}/transcript", headers=h
    )
    assert transcript.status_code == 200
    assert "بحث درباره زمان تحویل" in transcript.json()["text"]
    assert "تماس با تیم" in transcript.json()["text"]
    with TestingSession() as s:
        assert s.query(Source).filter(Source.kb_id == uuid.UUID(kb)).count() == 1
        assert s.query(Document).filter(Document.source_id == uuid.UUID(row["id"])).count() == 1
        assert s.query(Chunk).filter(Chunk.kb_id == uuid.UUID(kb)).count() > 0
