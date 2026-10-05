"""Source delete: full RAG footprint + storage blobs are removed.

Workspace + global paths, 404 scoping, and delete-while-processing.
"""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.common.base import Base
from app.knowledge.models import Chunk, Document, KnowledgeBase, PageSegment, Source
from app.users.models import User
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


class FakeStorage:
    def __init__(self, keys=()):
        self.objects = {k: b"x" for k in keys}

    def delete(self, key: str) -> None:
        del self.objects[key]  # KeyError when missing (best-effort path)


def _seed_tree(db, ws_id, kb_id, status="ready", stype="note",
               storage_key="k1", global_=False):
    src = Source(id=uuid.uuid4(),
                 workspace_id=None if global_ else ws_id, kb_id=kb_id,
                 type=stype, filename="f", storage_key=storage_key,
                 status=status,
                 parse_meta={"excel": {"duckdb_key": "k1.duckdb",
                                       "tables": []}} if stype == "csv" else {})
    db.add(src)
    db.flush()
    doc = Document(id=uuid.uuid4(), workspace_id=None if global_ else ws_id,
                   source_id=src.id, title="d")
    db.add(doc)
    db.flush()
    seg = PageSegment(id=uuid.uuid4(), workspace_id=None if global_ else ws_id,
                      document_id=doc.id, page_no=1, text="hello world")
    db.add(seg)
    db.flush()
    for i in range(2):
        db.add(Chunk(id=uuid.uuid4(), workspace_id=None if global_ else ws_id,
                     kb_id=kb_id, segment_id=seg.id, content=f"c{i}", tokens=1,
                     embedding=[0.1, 0.2],
                     chunk_metadata={"storage_key": f"fig{i}.png"} if i == 0 else {}))
    db.commit()
    return src


def _mk_ws_kb(db, scope="workspace", ws_id=None):
    ws_id = ws_id or uuid.uuid4()
    if scope == "workspace":
        db.add(Workspace(id=ws_id, name="W", type="shared",
                         owner_user_id=uuid.uuid4()))
        db.flush()
    kb = KnowledgeBase(id=uuid.uuid4(),
                       workspace_id=None if scope == "global" else ws_id,
                       title="KB", scope=scope)
    db.add(kb)
    db.commit()
    return ws_id, kb


def _ws_obj(ws_id):
    return type("W", (), {"id": ws_id})()


def _user():
    return type("U", (), {"id": uuid.uuid4()})()


def test_delete_workspace_source_purges_all(db):
    from app.knowledge.service import delete_source

    ws_id, kb = _mk_ws_kb(db)
    src = _seed_tree(db, ws_id, kb.id, status="ready", stype="csv")
    storage = FakeStorage(["k1", "k1.duckdb", "fig0.png", "unrelated"])
    out = delete_source(db, _ws_obj(ws_id), kb, _user(), src.id, storage)
    assert out["documents"] == 1 and out["segments"] == 1
    assert out["chunks"] == 2 and out["deleted"] == str(src.id)
    assert set(storage.objects) == {"unrelated"}  # source+duckdb+figure gone
    assert db.query(Source).count() == 0
    assert db.query(Document).count() == 0
    assert db.query(PageSegment).count() == 0
    assert db.query(Chunk).count() == 0


def test_delete_missing_blobs_still_succeeds(db):
    from app.knowledge.service import delete_source

    ws_id, kb = _mk_ws_kb(db)
    src = _seed_tree(db, ws_id, kb.id, storage_key="gone")
    storage = FakeStorage([])
    out = delete_source(db, _ws_obj(ws_id), kb, _user(), src.id, storage)
    assert out["deleted"] == str(src.id)
    assert db.query(Source).count() == 0


def test_delete_wrong_kb_404(db):
    from fastapi import HTTPException

    from app.knowledge.service import delete_source

    ws_id, kb = _mk_ws_kb(db)
    other = KnowledgeBase(id=uuid.uuid4(), workspace_id=ws_id,
                          title="OTHER", scope="workspace")
    db.add(other)
    db.commit()
    src = _seed_tree(db, ws_id, kb.id)
    with pytest.raises(HTTPException):
        delete_source(db, _ws_obj(ws_id), other, _user(), src.id, FakeStorage([]))
    assert db.query(Source).count() == 1  # untouched


def test_delete_while_processing(db):
    from app.knowledge.service import delete_source

    ws_id, kb = _mk_ws_kb(db)
    src = _seed_tree(db, ws_id, kb.id, status="processing")
    delete_source(db, _ws_obj(ws_id), kb, _user(), src.id, FakeStorage(["k1"]))
    assert db.query(Source).count() == 0
    assert db.query(Chunk).count() == 0


def test_delete_global_source(db):
    from fastapi import HTTPException

    from app.knowledge.service import delete_global_source

    _, kb = _mk_ws_kb(db, scope="global")
    src = _seed_tree(db, None, kb.id, stype="csv", global_=True)
    storage = FakeStorage(["k1", "k1.duckdb", "fig0.png"])
    out = delete_global_source(db, kb, _user(), src.id, storage)
    assert out["deleted"] == str(src.id) and out["chunks"] == 2
    assert storage.objects == {}
    assert db.query(Source).count() == 0
    with pytest.raises(HTTPException):
        delete_global_source(db, kb, _user(), src.id, storage)


def test_delete_global_rejects_workspace_kb(db):
    from fastapi import HTTPException

    from app.knowledge.service import delete_global_source

    ws_id, kb = _mk_ws_kb(db)
    src = _seed_tree(db, ws_id, kb.id)
    with pytest.raises(HTTPException):
        delete_global_source(db, kb, _user(), src.id, FakeStorage([]))
    assert db.query(Source).count() == 1
