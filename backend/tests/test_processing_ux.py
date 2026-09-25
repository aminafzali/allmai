"""Processing UX endpoints: stage derivation + gating (workspace + admin)."""

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
from app.knowledge.models import Chunk, Document, KnowledgeBase, PageSegment, Source
from app.knowledge.service import processing_stages
from app.main import app
from app.users.models import User
from app.workspaces.models import Workspace, WorkspaceMember

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


@pytest.fixture()
def setup(db):
    pw = hash_password("password123")
    ua = User(id=uuid.uuid4(), email="a@x.com", password_hash=pw, full_name="A")
    ub = User(id=uuid.uuid4(), email="b@x.com", password_hash=pw, full_name="B")
    adm = User(id=uuid.uuid4(), email="adm@x.com", password_hash=pw,
               full_name="ADM", is_admin=True)
    db.add_all([ua, ub, adm])
    w = uuid.uuid4()
    db.add(Workspace(id=w, name="W", type="shared", owner_user_id=ua.id))
    db.add(WorkspaceMember(workspace_id=w, user_id=ua.id, role="owner"))
    kb = KnowledgeBase(id=uuid.uuid4(), workspace_id=w, title="KB", scope="workspace")
    gkb = KnowledgeBase(id=uuid.uuid4(), workspace_id=None, title="G", scope="global")
    db.add_all([kb, gkb])
    db.commit()
    return {"ua": ua, "ub": ub, "adm": adm, "w": w, "kb": kb, "gkb": gkb}


@pytest.fixture()
def client(db, setup):
    def override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app), setup
    app.dependency_overrides.clear()


def _token(c, email):
    return c.post("/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]


def _h(t):
    return {"Authorization": f"Bearer {t}"}


def _mk_source(db, setup, **kw):
    defaults = {"workspace_id": setup["w"], "kb_id": setup["kb"].id,
                "status": "pending", "parse_meta": {}}
    defaults.update(kw)
    src = Source(id=uuid.uuid4(), type="pdf", filename="d.pdf", storage_key="k",
                 error="", **defaults)
    db.add(src)
    db.commit()
    return src


def test_stages_shapes():
    pending = Source(status="pending", parse_meta={})
    assert [s["key"] for s in processing_stages(pending)] == ["upload", "parsing"]
    ready = Source(status="ready",
                   parse_meta={"parser": "fallback", "chunks": 3})
    keys = [(s["key"], s["state"]) for s in processing_stages(ready)]
    assert ("result", "done") in [(k, v) for k, v in keys]
    assert ("ocr", "skipped") in keys
    degraded = Source(status="ready", parse_meta={
        "parser": "raganything-docling", "ocr_degraded": True,
        "ocr_empty_pages": [2], "vision": {"processed": 1, "failed": 1},
        "chunks": 4})
    states = {s["key"]: s["state"] for s in processing_stages(degraded)}
    assert states["ocr"] == "degraded" and states["vision"] == "degraded"
    assert states["result"] == "degraded"
    failed = Source(status="failed", error="Parse: boom", parse_meta={})
    assert processing_stages(failed)[-1] == {
        "key": "result", "label": "Failed", "state": "failed",
        "detail": "Parse: boom"}


def test_workspace_processing_detail_gating(client, db):
    c, setup = client
    ta, tb = _token(c, "a@x.com"), _token(c, "b@x.com")
    w, kb = setup["w"], setup["kb"]
    src = _mk_source(db, setup, status="ready",
                     parse_meta={"parser": "fallback", "chunks": 1})
    seg = PageSegment(id=uuid.uuid4(), workspace_id=w,
                      document_id=uuid.uuid4(), page_no=2, text="cap")
    # real document link for the figure path:
    from app.knowledge.models import Document

    doc = Document(id=uuid.uuid4(), workspace_id=w, source_id=src.id, title="d")
    db.add(doc)
    db.flush()
    seg.document_id = doc.id
    db.add(seg)
    db.flush()
    db.add(Chunk(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, segment_id=seg.id,
                 content="[Figure on page 2: cap]",
                 tokens=5, embedding=[0.01] * 1536,
                 chunk_metadata={"kind": "image", "element_id": "e1",
                                 "caption": "cap", "vision_skipped": "caption-data"}))
    db.add(Chunk(id=uuid.uuid4(), workspace_id=w, kb_id=kb.id, segment_id=seg.id,
                 content="plain text", tokens=2, embedding=[0.01] * 1536,
                 chunk_metadata={"kind": "text"}))
    db.commit()
    url = f"/workspaces/{w}/knowledge-bases/{kb.id}/sources/{src.id}/processing"
    r = c.get(url, headers=_h(ta))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready" and body["chunks"] == 2
    assert [s["key"] for s in body["stages"]] == [
        "upload", "parsing", "ocr", "vision", "chunking", "result"]
    assert len(body["figures"]) == 1
    fig = body["figures"][0]
    assert fig["page_no"] == 2 and fig["vision_status"] == "skipped:caption-data"
    # outsider gets 404 (no leak)
    assert c.get(url, headers=_h(tb)).status_code == 404
    # wrong kb -> 404
    other_kb = KnowledgeBase(id=uuid.uuid4(), workspace_id=w, title="K2",
                             scope="workspace")
    db.add(other_kb)
    db.commit()
    bad = f"/workspaces/{w}/knowledge-bases/{other_kb.id}/sources/{src.id}/processing"
    assert c.get(bad, headers=_h(ta)).status_code == 404


def test_admin_global_processing_endpoints(client, db):
    c, setup = client
    ta, tadm = _token(c, "a@x.com"), _token(c, "adm@x.com")
    gkb = setup["gkb"]
    src = Source(id=uuid.uuid4(), workspace_id=None, kb_id=gkb.id, type="pdf",
                 filename="g.pdf", storage_key="gk", status="ready",
                 parse_meta={"parser": "raganything-docling", "chunks": 0})
    db.add(src)
    db.commit()
    lst = c.get(f"/admin/global-knowledge-bases/{gkb.id}/sources",
                headers=_h(tadm))
    assert lst.status_code == 200 and len(lst.json()) == 1
    assert lst.json()[0]["workspace_id"] is None
    det = c.get(f"/admin/global-knowledge-bases/{gkb.id}/sources/{src.id}/processing",
                headers=_h(tadm))
    assert det.status_code == 200
    assert det.json()["figures"] == []
    # plain member gated out of admin surface
    assert c.get(f"/admin/global-knowledge-bases/{gkb.id}/sources",
                 headers=_h(ta)).status_code in (401, 403)
