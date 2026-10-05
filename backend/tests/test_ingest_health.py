"""Ingest health: heartbeat, lazy watchdog, celery safety flags."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.common.base import utcnow
from app.common.base import Base
from app.knowledge.models import KnowledgeBase, Source
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


def _ws_kb(db, scope="workspace"):
    wid = uuid.uuid4()
    if scope == "workspace":
        db.add(Workspace(id=wid, name="W", type="shared",
                         owner_user_id=uuid.uuid4()))
        db.flush()
    kb = KnowledgeBase(id=uuid.uuid4(),
                       workspace_id=None if scope == "global" else wid,
                       title="KB", scope=scope)
    db.add(kb)
    db.commit()
    return wid, kb


def _src(db, ws_id, kb_id, status="processing", age_min=None, global_=False):
    src = Source(id=uuid.uuid4(), workspace_id=None if global_ else ws_id,
                 kb_id=kb_id, type="note", filename="f", status=status)
    db.add(src)
    db.flush()
    if age_min is not None:
        # naive UTC (as SQLite returns) to mimic aged rows portably
        naive = utcnow().replace(tzinfo=None) - timedelta(minutes=age_min)
        db.query(Source).filter(Source.id == src.id).update(
            {"processing_started_at": naive})
        db.commit()
    return src


def test_watchdog_marks_stale_failed(db):
    from app.knowledge.service import mark_stale_ingests

    wid, kb = _ws_kb(db)
    old = _src(db, wid, kb.id, "processing", age_min=60)
    fresh = _src(db, wid, kb.id, "processing", age_min=5)
    ready = _src(db, wid, kb.id, "ready")
    db.refresh(ready)
    ready.processing_started_at = None
    db.commit()
    n = mark_stale_ingests(db, wid, timeout_min=30)
    assert n == 1
    db.refresh(old)
    assert old.status == "failed" and "استخراج مجدد" in old.error
    assert old.processing_started_at is None
    db.refresh(fresh)
    assert fresh.status == "processing"
    db.refresh(ready)
    assert ready.status == "ready"


def test_watchdog_global_scope(db):
    from app.knowledge.service import mark_stale_ingests

    _, gkb = _ws_kb(db, scope="global")
    old = _src(db, None, gkb.id, "processing", age_min=90, global_=True)
    assert mark_stale_ingests(db, None, timeout_min=30) == 1
    db.refresh(old)
    assert old.status == "failed"
    # workspace call never touches global rows and vice versa
    wid, kb = _ws_kb(db)
    wold = _src(db, wid, kb.id, "pending", age_min=90)
    assert mark_stale_ingests(db, None, timeout_min=30) == 0
    assert mark_stale_ingests(db, wid, timeout_min=30) == 1
    db.refresh(wold)
    assert wold.status == "failed"


def test_intake_sets_heartbeat_without_broker(db, monkeypatch):
    from app.knowledge.service import create_link_source

    wid, kb = _ws_kb(db)
    monkeypatch.setattr("workers.celery_app.broker_available", lambda: False)
    user = type("U", (), {"id": uuid.uuid4()})()
    ws = type("W", (), {"id": wid})()

    class FakeStorage:
        def put(self, key, blob, content_type="x"):
            pass

    src = create_link_source(db, ws, kb, user, "note", "t",
                             {"content": "hello"}, FakeStorage())
    # broker down: stays pending, but heartbeat is set for fair judging
    assert src.status == "pending"
    assert src.processing_started_at is not None


def test_retry_resets_heartbeat(db, monkeypatch):
    from app.knowledge.service import retry_source

    wid, kb = _ws_kb(db)
    src = _src(db, wid, kb.id, "failed", age_min=90)
    monkeypatch.setattr("workers.celery_app.broker_available", lambda: False)
    user = type("U", (), {"id": uuid.uuid4()})()
    ws = type("W", (), {"id": wid})()
    out = retry_source(db, ws, user, src.id)
    assert out.status == "pending" and out.error == ""
    assert out.processing_started_at is not None


def test_celery_safety_flags():
    from workers.celery_app import celery

    assert celery.conf.task_acks_late is True
    assert celery.conf.task_reject_on_worker_lost is True
    assert celery.conf.worker_prefetch_multiplier == 1


def test_worker_status_shape():
    from fastapi.testclient import TestClient

    from app.core.database import get_db
    from app.core.security import hash_password
    from app.main import app
    from app.users.models import User

    engine2 = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    S2 = sessionmaker(bind=engine2, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine2)
    s = S2()
    admin = User(id=uuid.uuid4(), email="adm@x.com",
                 password_hash=hash_password("password123"), is_admin=True)
    s.add(admin)
    s.commit()

    def override_db():
        try:
            yield s
        finally:
            pass

    app.dependency_overrides[get_db] = override_db
    try:
        c = TestClient(app)
        t = c.post("/auth/login",
                   json={"email": "adm@x.com",
                         "password": "password123"}).json()["access_token"]
        r = c.get("/admin/worker-status",
                  headers={"Authorization": f"Bearer {t}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body["worker_alive"], bool)
        assert body["queue_len"] is None or isinstance(body["queue_len"], int)
    finally:
        app.dependency_overrides.clear()
        s.close()


def test_heartbeat_ping_refreshes_only_processing(db, monkeypatch):
    import app.core.database as DB
    from workers.tasks import _heartbeat_ping

    # ping sessions are independent (production: fresh SessionLocal each
    # beat); assertions keep using the test session.
    monkeypatch.setattr(DB, "SessionLocal", TestingSession)
    wid, kb = _ws_kb(db)
    live = _src(db, wid, kb.id, status="processing", age_min=10)
    live_id = live.id
    before = live.processing_started_at
    assert _heartbeat_ping(live_id, wid) is True
    db.expire_all()
    now = db.query(Source).filter(Source.id == live_id).one()
    assert now.processing_started_at >= before
    assert now.status == "processing"
    # terminal rows stop the pings
    now.status = "failed"
    db.commit()
    assert _heartbeat_ping(live_id, wid) is False
    assert _heartbeat_ping(uuid.uuid4(), wid) is False  # gone row


def test_orphan_reset_requeues_only_truly_stuck(db):
    from workers.tasks import reset_orphaned_ingests

    wid, kb = _ws_kb(db)
    stale = _src(db, wid, kb.id, status="processing", age_min=180)
    fresh = _src(db, wid, kb.id, status="processing", age_min=5)
    done = _src(db, wid, kb.id, status="failed", age_min=180)
    enqueued = []
    out = reset_orphaned_ingests(db, enqueue_fn=lambda sid, ws: enqueued.append((sid, ws)) or True,
                                 stale_s=2400)
    assert out == {"reset": 1, "requeued": 1}
    assert enqueued == [(stale.id, wid)]
    db.refresh(stale)
    assert stale.status == "pending" and not stale.error
    db.refresh(fresh)
    assert fresh.status == "processing"  # live run untouched
    db.refresh(done)
    assert done.status == "failed"  # terminal untouched


def test_supervisor_files_present_and_valid():
    import xml.etree.ElementTree as ET
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    xml_path = root / "scripts" / "allmai-worker.xml"
    probe = root / "scripts" / "worker-probe.ps1"
    assert xml_path.is_file() and probe.is_file()
    tree = ET.parse(str(xml_path))  # well-formed XML
    xml_text = xml_path.read_text(encoding="utf-8")
    assert "RestartOnFailure" in xml_text and "celery.exe" in xml_text
    assert "AllMaiWorker" in probe.read_text(encoding="utf-8", errors="replace")
