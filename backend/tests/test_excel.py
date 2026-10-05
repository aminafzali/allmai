"""Excel tests: multi-sheet ingest (DuckDB + RAG summaries), simple query,
SUM/COUNT/GROUP BY, citation fields, cross-workspace denial, validation.

Offline: openpyxl builds workbooks in-test, duckdb runs locally, fake
embeddings, SQLite + FakeStorage.
"""

import io
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.agents.tools import ToolContext, excel_query
from app.common.base import Base
from app.core.config import get_settings
from app.knowledge.excel.store import validate_sql
from app.knowledge.models import Chunk, Document, KnowledgeBase, PageSegment, Source
from app.knowledge.retrieval.hybrid import hybrid_search
from app.users.models import User
from app.workspaces.models import Workspace, WorkspaceMember
from workers.tasks import run_ingest

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)


def make_xlsx() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["Region", "Amount", "Units"])
    ws.append(["Tehran", 100, 2])
    ws.append(["Tehran", 200, 3])
    ws.append(["Shiraz", 150, 1])
    ws2 = wb.create_sheet("Costs")
    ws2.append(["Item", "Cost"])
    ws2.append(["Rent", 500])
    ws2.append(["Food", 300])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


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


def _source(db, storage, wid, kb_id, filename="data.xlsx") -> Source:
    src = Source(id=uuid.uuid4(), workspace_id=wid, kb_id=kb_id, type="excel",
                 filename=filename, status="pending")
    db.add(src)
    db.flush()
    key = f"k/{src.id}/{filename}"
    storage.put(key, make_xlsx())
    src.storage_key = key
    db.commit()
    return src


def fake_embed(texts):
    assert texts, "should not embed zero chunks"
    return [[float(len(t) % 7)] * 1536 for t in texts]


def _ctx(db, storage, wid, kb_id=None):
    return ToolContext(db=db, workspace_id=wid, user_id=uuid.uuid4(),
                       storage=storage,
                       kb_ids=[kb_id] if kb_id else None)


def test_excel_ingest_two_sheets(env):
    db, storage, wid, kb_id = env
    src = _source(db, storage, wid, kb_id)
    res = run_ingest(src.id, db=db, storage=storage, embed_fn=fake_embed)
    assert res["ok"] is True and res["chunks"] == 2
    db.refresh(src)
    assert src.status == "ready"
    meta = (src.parse_meta or {}).get("excel") or {}
    assert {t["sheet"] for t in meta.get("tables", [])} == {"Sales", "Costs"}
    assert meta.get("duckdb_key", "").endswith(".duckdb")
    assert meta["duckdb_key"] in storage.objects  # artifact persisted
    chunks = db.query(Chunk).filter(Chunk.kb_id == kb_id).all()
    assert any("Amount" in c.content and "Rows: 3" in c.content for c in chunks)


def test_excel_rag_retrieval(env):
    db, storage, wid, kb_id = env
    _source(db, storage, wid, kb_id)
    run_ingest(db.query(Source).first().id, db=db, storage=storage,
               embed_fn=fake_embed)
    hits = hybrid_search(db, wid, "Sales Amount Units", kb_id, top_k=5,
                         embed_fn=fake_embed, rerank_fn=None)
    assert hits and any("Sales" in h.chunk.content for h in hits)


def test_excel_sum_query(env):
    db, storage, wid, kb_id = env
    _source(db, storage, wid, kb_id)
    run_ingest(db.query(Source).first().id, db=db, storage=storage,
               embed_fn=fake_embed)
    out = excel_query(_ctx(db, storage, wid, kb_id), "مجموع Amount چقدر است؟")
    assert len(out) == 1
    r = out[0]
    assert r["sheet"] == "Sales" and r["row_count"] == 1
    assert r["rows"][0][0] == "450"  # 100 + 200 + 150
    # citation fields for the agent
    assert r["source"] == "data.xlsx" and r["source_id"]
    assert "SUM" in r["sql"] and "sales" in r["sql"].lower()


def test_excel_count_group_by(env):
    db, storage, wid, kb_id = env
    _source(db, storage, wid, kb_id)
    run_ingest(db.query(Source).first().id, db=db, storage=storage,
               embed_fn=fake_embed)
    out = excel_query(_ctx(db, storage, wid, kb_id),
                      "تعداد Units به تفکیک Region")
    assert len(out) == 1
    got = {r[0]: r[1] for r in out[0]["rows"]}
    assert got == {"Tehran": "2", "Shiraz": "1"}
    assert "GROUP BY" in out[0]["sql"]


def test_excel_informational_yields_to_rag(env):
    db, storage, wid, kb_id = env
    _source(db, storage, wid, kb_id)
    run_ingest(db.query(Source).first().id, db=db, storage=storage,
               embed_fn=fake_embed)
    assert excel_query(_ctx(db, storage, wid, kb_id),
                       "این فایل درباره چیست؟") == []


def test_excel_cross_workspace_denied(env):
    db, storage, wid, kb_id = env
    _source(db, storage, wid, kb_id)
    run_ingest(db.query(Source).first().id, db=db, storage=storage,
               embed_fn=fake_embed)
    assert excel_query(_ctx(db, storage, uuid.uuid4()),
                       "مجموع Amount") == []


def test_excel_sql_validator_rejects():
    allow = {"sales": {"region", "amount"}}
    with pytest.raises(ValueError):
        validate_sql("DROP TABLE sales", allow)
    with pytest.raises(ValueError):
        validate_sql("SELECT amount FROM costs", allow)
    with pytest.raises(ValueError):
        validate_sql("SELECT amount; DELETE FROM sales", allow)
    with pytest.raises(ValueError):
        validate_sql("SELECT secret FROM sales", allow)
    ok = validate_sql("SELECT region, SUM(amount) FROM sales GROUP BY region LIMIT 5",
                      allow)
    assert ok["table"] == "sales" and ok["limit"] == 5


def make_csv() -> bytes:
    return ("Region;Amount\nTehran;100\nShiraz;150\n").encode("utf-8-sig")


def _csv_source(db, storage, wid, kb_id, filename="data.csv") -> Source:
    src = Source(id=uuid.uuid4(), workspace_id=wid, kb_id=kb_id, type="csv",
                 filename=filename, status="pending")
    db.add(src)
    db.flush()
    key = f"k/{src.id}/{filename}"
    storage.put(key, make_csv())
    src.storage_key = key
    db.commit()
    return src


def test_csv_ingest_and_duckdb(env):
    db, storage, wid, kb_id = env
    src = _csv_source(db, storage, wid, kb_id)
    res = run_ingest(src.id, db=db, storage=storage, embed_fn=fake_embed)
    assert res["ok"] is True and res["chunks"] == 1
    db.refresh(src)
    assert src.status == "ready"
    meta = (src.parse_meta or {}).get("excel") or {}
    assert meta["tables"][0]["sheet"] == "data"
    assert meta["tables"][0]["rows"] == 2
    assert meta.get("duckdb_key", "").endswith(".duckdb")
    assert meta["duckdb_key"] in storage.objects


def test_csv_sum_query(env):
    db, storage, wid, kb_id = env
    _csv_source(db, storage, wid, kb_id)
    run_ingest(db.query(Source).first().id, db=db, storage=storage,
               embed_fn=fake_embed)
    out = excel_query(_ctx(db, storage, wid, kb_id), "مجموع Amount")
    assert len(out) == 1
    assert out[0]["rows"][0][0] == "250"  # 100 + 150
    assert out[0]["source"] == "data.csv"


def test_csv_validation():
    from fastapi import HTTPException

    from app.storage.validation import validate_upload

    assert validate_upload("csv", "data.csv", b"a,b\n1,2\n") == "text/csv"
    with pytest.raises(HTTPException):
        validate_upload("csv", "data.txt", b"a,b\n1,2\n")


def test_excel_validation_and_limits(env, monkeypatch):
    from fastapi import HTTPException

    from app.storage.validation import validate_upload

    assert validate_upload("excel", "data.xlsx",
                           b"PK\x03\x04" + b"\x00" * 100).endswith("sheet")
    with pytest.raises(HTTPException):
        validate_upload("excel", "data.xls", b"PK\x03\x04" + b"\x00" * 10)
    monkeypatch.setattr(get_settings(), "EXCEL_MAX_ROWS_PER_SHEET", 2)
    db, storage, wid, kb_id = env
    src = _source(db, storage, wid, kb_id)
    res = run_ingest(src.id, db=db, storage=storage, embed_fn=fake_embed)
    assert res["ok"] is False
    db.refresh(src)
    assert src.status == "failed" and "too many rows" in (src.error or "")
