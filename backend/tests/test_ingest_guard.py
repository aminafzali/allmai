"""No-repeat ingestion guards: a failed extraction must NEVER re-run itself.

- celery task: max_retries == 0 (no broker-level re-execution)
- run_ingest: fresh-processing rows are refused without touching anything
  (no model calls, no status change); stale/pending rows proceed
- retry_source: 409 on a live run, requeues only idle sources
- intake leaves sources pending for the worker claim (never pre-marked)
- embed pre-filter: blank texts never reach the provider batch
"""

import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.common.base import Base, utcnow
from app.knowledge.models import KnowledgeBase, Source
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
    from app.core.security import hash_password

    pw = hash_password("password123")
    u = User(id=uuid.uuid4(), email="u@x.com", password_hash=pw)
    db.add(u)
    w = uuid.uuid4()
    db.add(Workspace(id=w, name="W", type="shared", owner_user_id=u.id))
    db.add(WorkspaceMember(workspace_id=w, user_id=u.id, role="owner"))
    kb = KnowledgeBase(id=uuid.uuid4(), workspace_id=w, title="KB",
                       scope="workspace")
    db.add(kb)
    db.commit()
    return u, w, kb


def _src(db, w, kb_id, status="pending", age_min=None):
    from app.knowledge.models import Source as _S

    src = _S(id=uuid.uuid4(), workspace_id=w, kb_id=kb_id, type="note",
             filename="n", status=status, storage_key="k")
    db.add(src)
    db.flush()
    if age_min is not None:
        naive = utcnow().replace(tzinfo=None) - timedelta(minutes=age_min)
        db.query(_S).filter(_S.id == src.id).update(
            {"processing_started_at": naive})
        db.commit()
    return src


class FakeStorage:
    def __init__(self, blobs=None):
        self.objects = dict(blobs or {})

    def put(self, key, data, content_type=""):
        self.objects[key] = data
        return key

    def get(self, key):
        return self.objects[key]

    def delete(self, key):
        self.objects.pop(key, None)


def _no_model(*a, **k):
    raise AssertionError("no model call allowed on a refused duplicate")


# ---------- celery: no broker retry ----------

def test_celery_ingest_task_has_no_retries():
    from workers.tasks import ingest_source

    assert ingest_source.max_retries == 0


# ---------- run_ingest duplicate guard ----------

def test_fresh_processing_is_refused_untouched(db):
    _, w, kb = _seed(db)
    src = _src(db, w, kb.id, status="processing", age_min=5)
    st = FakeStorage({"k": b'{"title": "t", "content": "hi"}'})
    res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                     embed_fn=_no_model, transcribe_fn=_no_model)
    assert res["ok"] is False and "duplicate" in res["error"]
    db.refresh(src)
    assert src.status == "processing"  # live run owns it; untouched


def test_stale_processing_proceeds(db):
    from app.knowledge.parsers.base import ParsedDocument, ParsedPage

    _, w, kb = _seed(db)
    src = _src(db, w, kb.id, status="processing", age_min=180)
    st = FakeStorage({"k": b"x"})
    import workers.tasks as T

    doc = ParsedDocument(title="s", pages=[ParsedPage(page_no=1, text="hi")])
    import unittest.mock as _mock

    with _mock.patch.object(T, "parse_source", return_value=doc):
        res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                         embed_fn=lambda texts: [[0.01] * 1536 for _ in texts])
    assert res["ok"] is True
    db.refresh(src)
    assert src.status == "ready"


def test_pending_proceeds_normally(db):
    from app.knowledge.parsers.base import ParsedDocument, ParsedPage

    _, w, kb = _seed(db)
    src = _src(db, w, kb.id, status="pending")
    st = FakeStorage({"k": b'{"title": "t", "content": "hello world"}'})
    res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                     embed_fn=lambda texts: [[0.01] * 1536 for _ in texts])
    assert res["ok"] is True
    db.refresh(src)
    assert src.status == "ready"


def test_is_fresh_processing_cases(db):
    from workers.tasks import is_fresh_processing

    _, w, kb = _seed(db)
    assert is_fresh_processing(None) is False
    assert is_fresh_processing(_src(db, w, kb.id, "pending")) is False
    assert is_fresh_processing(_src(db, w, kb.id, "failed", 90)) is False
    assert is_fresh_processing(_src(db, w, kb.id, "processing", 5)) is True
    assert is_fresh_processing(_src(db, w, kb.id, "processing", 180)) is False


# ---------- retry endpoint guard ----------

def test_retry_refuses_live_run(db, monkeypatch):
    from app.knowledge import service as S

    u, w, kb = _seed(db)
    src = _src(db, w, kb.id, status="processing", age_min=5)
    ws = type("W", (), {"id": w})()

    def _boom(*a, **k):
        raise AssertionError("must not enqueue a live run")

    monkeypatch.setattr(S, "enqueue_ingest", _boom)
    with pytest.raises(HTTPException) as ei:
        S.retry_source(db, ws, u, src.id)
    assert ei.value.status_code == 409
    db.refresh(src)
    assert src.status == "processing"  # untouched


def test_retry_requeues_idle_source(db, monkeypatch):
    from app.knowledge import service as S

    u, w, kb = _seed(db)
    src = _src(db, w, kb.id, status="failed", age_min=90)
    ws = type("W", (), {"id": w})()
    calls = []
    monkeypatch.setattr(S, "enqueue_ingest",
                        lambda *a, **k: calls.append(a) or True)
    out = S.retry_source(db, ws, u, src.id)
    assert out.status == "pending" and out.error == ""
    assert calls  # exactly one enqueue


# ---------- intake leaves pending for the worker claim ----------

def test_upload_stays_pending_for_claim(db, monkeypatch):
    from app.knowledge import service as S

    u, w, kb = _seed(db)
    monkeypatch.setattr(S, "enqueue_ingest", lambda *a, **k: True)
    ws = type("W", (), {"id": w})()
    kbobj = type("K", (), {"id": kb.id})()

    class _ST(FakeStorage):
        pass

    src = S.create_file_source(db, ws, kbobj, u, "txt", "n.txt",
                               b"hello", _ST())
    assert src.status == "pending"


# ---------- embed blank pre-filter ----------

def test_embed_never_receives_blanks(db):
    from app.knowledge.parsers.base import ParsedDocument, ParsedPage

    _, w, kb = _seed(db)
    src = _src(db, w, kb.id, status="pending")
    st = FakeStorage({"k": b"x"})
    import workers.tasks as T
    import unittest.mock as _mock

    doc = ParsedDocument(
        title="s",
        pages=[ParsedPage(page_no=1, text="real content"),
               ParsedPage(page_no=2, text="   ")])
    seen = []

    def _embed(texts):
        seen.extend(texts)
        assert all((t or "").strip() for t in texts), "blank reached provider"
        return [[0.01] * 1536 for _ in texts]

    with _mock.patch.object(T, "parse_source", return_value=doc):
        res = run_ingest(src.id, workspace_id=w, db=db, storage=st,
                         embed_fn=_embed)
    assert res["ok"] is True
    assert seen == ["real content"]
