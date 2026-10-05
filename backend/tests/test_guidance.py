"""Guidance (moshaver): RIASEC quiz scoring + deterministic O_NET match."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.agents import guidance as g
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


def test_quiz_bank_integrity():
    assert len(g.QUIZ) == 18
    by_type: dict[str, int] = {}
    for q in g.QUIZ:
        assert q["type"] in g.RIASEC_TYPES
        assert q["text"].strip()
        by_type[q["type"]] = by_type.get(q["type"], 0) + 1
    assert by_type == {t: 3 for t in g.RIASEC_TYPES}
    assert [s["v"] for s in g.SCALE] == [1, 2, 3, 4, 5]


def test_score_quiz_full_and_partial():
    answers = {q["id"]: 5 if q["type"] == "S" else 2 for q in g.QUIZ}
    res = g.score_quiz(answers)
    assert res["code"].startswith("S") and len(res["code"]) == 3
    assert res["scores"]["S"] == 15
    assert [t["code"] for t in res["types"]][0] == "S"
    with pytest.raises(ValueError):
        g.score_quiz({"q01": 5})
    # invalid values ignored, valid ones still count
    mixed = {q["id"]: 4 for q in g.QUIZ}
    mixed["q01"] = "junk"
    res2 = g.score_quiz(mixed)
    assert len(res2["code"]) == 3


def _seed_onet(db, ws_id, kb_id, table="o_net"):
    cols = [
        {"col": "col_13", "name": "عنوان شغل", "dtype": "text"},
        {"col": "col_4", "name": "گروه شغلی", "dtype": "text"},
        {"col": "col_8", "name": "تحصیلات معمول", "dtype": "text"},
        {"col": "riasec", "name": "تیپ علاقه (RIASEC)", "dtype": "text"},
        {"col": "col_14", "name": "شرح شغل", "dtype": "text"},
    ]
    src = Source(id=uuid.uuid4(), workspace_id=ws_id, kb_id=kb_id,
                 type="csv", filename="onet.csv", status="ready",
                 parse_meta={"excel": {
                     "tables": [{"table": table, "sheet": "s",
                                 "rows": 3, "columns": cols}],
                     "duckdb_key": "k"}})
    db.add(src)
    db.commit()
    return src


def test_match_ranking_and_isolation(db, monkeypatch):
    wa, wb = uuid.uuid4(), uuid.uuid4()
    db.add_all([
        Workspace(id=wa, name="A", type="shared", owner_user_id=uuid.uuid4()),
        Workspace(id=wb, name="B", type="shared", owner_user_id=uuid.uuid4()),
    ])
    kba, kbb = uuid.uuid4(), uuid.uuid4()
    db.add_all([
        KnowledgeBase(id=kba, workspace_id=wa, title="KA", scope="workspace"),
        KnowledgeBase(id=kbb, workspace_id=wb, title="KB", scope="workspace"),
    ])
    db.commit()
    _seed_onet(db, wa, kba)
    _seed_onet(db, wb, kbb)

    rows = [
        ("معلم", "آموزش", "لیسانس", "شرح معلمی", "SIA — اجتماعی"),
        ("مهندس", "فنی", "لیسانس", "شرح مهندسی", "RIE — واقع‌گرا"),
        ("حسابدار", "مالی", "لیسانس", "شرح حسابداری", "SEA — اجتماعی"),
    ]

    def fake_run(storage, duck_key, sql, timeout_s=15):
        assert "ILIKE" in sql and "riasec" in sql
        cols = ["col_13", "col_4", "col_8", "col_14", "riasec"]
        return cols, rows

    monkeypatch.setattr("app.knowledge.excel.store.run_query", fake_run)

    class _U:
        id = uuid.uuid4()

    res = g.match_jobs(db, type("W", (), {"id": wa})(), _U(), "SIA", limit=10)
    assert res["code"] == "SIA"
    assert [j["title"] for j in res["jobs"]] == ["معلم", "حسابدار", "مهندس"]
    assert [j["matched"] for j in res["jobs"]] == [3, 2, 1]
    assert res["jobs"][0]["riasec"] == "SIA"
    # other workspace's table never leaks (only its own 3 rows -> total 3)
    assert res["total"] == 3
    with pytest.raises(Exception):
        g.match_jobs(db, type("W", (), {"id": wa})(), _U(), "???", limit=5)


def test_profile_save_read(db):
    from app.workspaces.models import Workspace

    ws_id = uuid.uuid4()
    db.add(Workspace(id=ws_id, name="W", type="shared",
                     owner_user_id=uuid.uuid4()))
    db.commit()

    class _U:
        id = uuid.uuid4()

    ws = type("W", (), {"id": ws_id})()
    assert g.read_guidance_profile(db, ws, _U())["code"] is None
    res = g.score_quiz({q["id"]: (5 if q["type"] in ("S", "I") else 3)
                        for q in g.QUIZ})
    saved = g.save_guidance_profile(db, ws, _U(), res)
    assert saved["code"] == res["code"]
    back = g.read_guidance_profile(db, ws, _U())
    assert back["code"] == res["code"]
    assert back["scores"]["S"] == 15
