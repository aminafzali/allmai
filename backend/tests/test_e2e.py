"""End-to-end: workspace -> KB -> sources -> ingest -> search ->
agent -> chat, plus teacher plan + student plan, with isolation.

AI calls are mocked; storage is faked. Proves the platform flow works
with zero cross-workspace leakage.
"""

import io
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.agents.student.schemas import StudyPlanBody, StudyWeekOut, StudyTaskOut
from app.agents.teacher.schemas import LessonPlanBody
from app.agents.teacher.service import get_generate_structured_fn
from app.common.base import Base
from app.core.database import get_db
from app.core.security import hash_password
from app.knowledge.models import Chunk, Source
from app.knowledge.retrieval.hybrid import get_embed_fn, get_generate_fn
from app.main import app
from app.storage.s3 import get_storage
from app.users.models import User
from workers.tasks import run_ingest

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put(self, key, data, content_type=""):
        self.objects[key] = data
        return key

    def get(self, key):
        return self.objects[key]

    def presigned_get(self, key, expires_seconds=900):
        return f"https://fake-s3/{key}"


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
def client(db):
    fake = FakeStorage()

    def override_db():
        try:
            yield db
        finally:
            pass

    from app.knowledge.retrieval.hybrid import get_generate_tools_fn

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_storage] = lambda: fake
    app.dependency_overrides[get_embed_fn] = lambda: (lambda texts: [[1.0, 0.0] for _ in texts])
    app.dependency_overrides[get_generate_fn] = lambda: (lambda prompt, **kw: "پاسخ [1]")
    app.dependency_overrides[get_generate_structured_fn] = lambda: _fake_structured
    # hermetic: coach tool rounds stay off in the offline e2e
    app.dependency_overrides[get_generate_tools_fn] = lambda: None
    yield TestClient(app), fake, db
    app.dependency_overrides.clear()


def _fake_structured(prompt, schema, **kw):
    if schema is LessonPlanBody:
        return LessonPlanBody(
            title="Plan", objectives=["O"], prerequisites=[], topics=[],
            activities=[], examples=[], questions=[], assessment=[],
        )
    return StudyPlanBody(
        weeks=[StudyWeekOut(week=1, focus="F",
                            tasks=[StudyTaskOut(id="x", title="T", subject="s", minutes=30)],
                            milestones=[])],
        advice=[],
    )


def _user(db, email):
    u = User(id=uuid.uuid4(), email=email, password_hash=hash_password("password123"), full_name=email)
    db.add(u)
    db.commit()
    return u


def _token(c, email):
    return c.post("/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]


def test_full_platform_flow(client):
    c, fake, db = client
    _user(db, "teacher@x.com")
    _user(db, "other@x.com")
    tt = _token(c, "teacher@x.com")
    to = _token(c, "other@x.com")
    h = {"Authorization": f"Bearer {tt}"}

    # 1. workspace + KB
    wid = c.post("/workspaces", json={"name": "Class"}, headers=h).json()["id"]
    kb = c.post(f"/workspaces/{wid}/knowledge-bases", json={"title": "Chem"}, headers=h).json()["id"]

    # 2. note source -> ingest for real (mocked embed)
    src = c.post(f"/workspaces/{wid}/knowledge-bases/{kb}/sources/link", headers=h,
                 json={"type": "note", "title": "N", "content": "acids donate protons"}).json()
    res = run_ingest(src["id"], db=db, storage=fake,
                     embed_fn=lambda texts: [[1.0, 0.0] for _ in texts])
    assert res["ok"] is True and res["chunks"] == 1

    # 3. search finds it
    hits = c.post(f"/workspaces/{wid}/knowledge/search", headers=h,
                  json={"query": "acids", "kb_id": kb}).json()["chunks"]
    assert len(hits) == 1 and "protons" in hits[0]["content"]

    # 4. teacher agent + lesson plan + chat
    ta = c.post(f"/workspaces/{wid}/agents", json={"key": "teacher_lesson_planner"}, headers=h).json()
    plan = c.post(f"/workspaces/{wid}/agents/{ta['id']}/lesson-plans", headers=h, json={
        "kb_id": kb, "chapter": "Acids", "grade": "9th"}).json()
    assert plan["plan"]["title"] == "Plan"
    assert plan["plan"]["references"][0]["chunk_id"] == hits[0]["chunk_id"]
    chat = c.post(f"/workspaces/{wid}/agents/{ta['id']}/chat", headers=h,
                  json={"message": "Explain", "kb_id": kb}).json()
    assert chat["answer"] == "پاسخ [1]" and len(chat["citations"]) == 1

    # 5. student agent: profile -> plan -> chat -> progress
    sa = c.post(f"/workspaces/{wid}/agents", json={"key": "student_academic_coach"}, headers=h).json()
    c.post(f"/workspaces/{wid}/agents/{sa['id']}/profile", headers=h,
           json={"facts": {"grade": "9th"}})
    sp = c.post(f"/workspaces/{wid}/agents/{sa['id']}/study-plans", headers=h,
                json={"goals": ["Pass chem"], "weekly_hours": 5}).json()
    cid = sp["conversation_id"]
    assert sp["plan"]["weeks"][0]["tasks"][0]["id"] == "w1t1"
    ch = c.post(f"/workspaces/{wid}/agents/{sa['id']}/coach/chat", headers=h,
                json={"message": "Next?", "conversation_id": cid}).json()
    assert ch["has_plan"] is True
    pr = c.post(f"/workspaces/{wid}/agents/{sa['id']}/progress", headers=h,
                json={"conversation_id": cid, "completed_task_ids": ["w1t1"]}).json()
    assert pr["completed_task_ids"] == ["w1t1"]

    # 6. isolation: outsider sees nothing anywhere
    ho = {"Authorization": f"Bearer {to}"}
    assert c.get(f"/workspaces/{wid}/knowledge-bases", headers=ho).status_code == 404
    assert c.post(f"/workspaces/{wid}/knowledge/search", headers=ho,
                  json={"query": "acids"}).status_code == 404
    assert c.post(f"/workspaces/{wid}/agents/{ta['id']}/chat", headers=ho,
                  json={"message": "hi"}).status_code == 404
    assert c.get(f"/workspaces/{wid}/memory/facts", headers=ho).status_code == 404
