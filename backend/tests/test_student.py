"""Student coach tests: profile, study plan, stateful chat, progress."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.agents.student.schemas import StudyPlanBody, StudyWeekOut, StudyTaskOut
from app.agents.teacher.service import get_generate_structured_fn
from app.common.base import Base
from app.core.database import get_db
from app.core.security import hash_password
from app.knowledge.retrieval.hybrid import get_embed_fn, get_generate_fn
from app.main import app
from app.users.models import User
from app.workspaces.models import Workspace, WorkspaceMember

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)
PROMPTS: list[str] = []


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
    ua = User(id=uuid.uuid4(), email="a@x.com", password_hash=hash_password("password123"), full_name="A")
    ub = User(id=uuid.uuid4(), email="b@x.com", password_hash=hash_password("password123"), full_name="B")
    db.add_all([ua, ub])
    wa, wb = uuid.uuid4(), uuid.uuid4()
    db.add_all([
        Workspace(id=wa, name="A", type="shared", owner_user_id=ua.id),
        Workspace(id=wb, name="B", type="shared", owner_user_id=ub.id),
    ])
    db.add_all([
        WorkspaceMember(workspace_id=wa, user_id=ua.id, role="owner"),
        WorkspaceMember(workspace_id=wb, user_id=ub.id, role="owner"),
    ])
    db.commit()
    return {"wa": wa, "wb": wb}


def _fake_structured(prompt, schema, **kw):
    PROMPTS.append(prompt)
    assert schema is StudyPlanBody
    return StudyPlanBody(
        weeks=[StudyWeekOut(week=1, focus="Algebra basics",
                            tasks=[StudyTaskOut(id="x", title="Solve 20 equations",
                                                subject="math", minutes=90)],
                            milestones=["Finish chapter 1"])],
        advice=["Sleep well"],
    )


@pytest.fixture()
def client(db):
    seed = _seed(db)
    PROMPTS.clear()

    def override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_embed_fn] = lambda: (lambda texts: [[0.0] for _ in texts])
    app.dependency_overrides[get_generate_fn] = lambda: (lambda prompt, **kw: "coach reply")
    app.dependency_overrides[get_generate_structured_fn] = lambda: _fake_structured
    yield TestClient(app), seed
    app.dependency_overrides.clear()


def _token(c, email):
    return c.post("/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]


def _h(t):
    return {"Authorization": f"Bearer {t}"}


def test_profile_plan_chat_progress_flow(client):
    c, seed = client
    ta = _token(c, "a@x.com")
    wa = seed["wa"]
    aid = c.post(f"/workspaces/{wa}/agents",
                 json={"key": "student_academic_coach"}, headers=_h(ta)).json()["id"]

    prof = c.post(f"/workspaces/{wa}/agents/{aid}/profile", headers=_h(ta),
                  json={"facts": {"grade": "9th", "weak_subject": "math"}}).json()
    assert set(prof["saved"]) == {"profile.grade", "profile.weak_subject"}
    got = c.get(f"/workspaces/{wa}/agents/{aid}/profile", headers=_h(ta)).json()
    assert got["profile"] == {"grade": "9th", "weak_subject": "math"}

    plan_r = c.post(f"/workspaces/{wa}/agents/{aid}/study-plans", headers=_h(ta), json={
        "goals": ["Pass algebra exam"], "weekly_hours": 6})
    assert plan_r.status_code == 201, plan_r.text
    plan = plan_r.json()["plan"]
    assert plan["weeks"][0]["tasks"][0]["id"] == "w1t1"  # ids stamped by service
    conv_id = plan_r.json()["conversation_id"]
    assert "9th" in PROMPTS[-1] and "algebra exam" in PROMPTS[-1]

    # coach chat continues the SAME plan conversation: stateful
    chat = c.post(f"/workspaces/{wa}/agents/{aid}/coach/chat", headers=_h(ta), json={
        "message": "I finished chapter 1, what next?", "conversation_id": conv_id})
    assert chat.status_code == 200
    assert chat.json()["has_plan"] is True
    assert chat.json()["conversation_id"] == conv_id

    # progress tracking with id validation
    bad = c.post(f"/workspaces/{wa}/agents/{aid}/progress", headers=_h(ta), json={
        "conversation_id": conv_id, "completed_task_ids": ["nope"]})
    assert bad.status_code == 422
    ok = c.post(f"/workspaces/{wa}/agents/{aid}/progress", headers=_h(ta), json={
        "conversation_id": conv_id, "completed_task_ids": ["w1t1"], "note": "easy!"})
    assert ok.status_code == 200
    assert ok.json()["completed_task_ids"] == ["w1t1"]
    assert ok.json()["total_tasks"] == 1

    # follow-up chat sees the progress
    chat2 = c.post(f"/workspaces/{wa}/agents/{aid}/coach/chat", headers=_h(ta), json={
        "message": "Adjust my plan", "conversation_id": conv_id})
    assert chat2.json()["has_plan"] is True


def test_student_wrong_agent_and_isolation(client):
    c, seed = client
    ta, tb = _token(c, "a@x.com"), _token(c, "b@x.com")
    wa = seed["wa"]
    teacher_id = c.post(f"/workspaces/{wa}/agents",
                        json={"key": "teacher_lesson_planner"}, headers=_h(ta)).json()["id"]
    coach_id = c.post(f"/workspaces/{wa}/agents",
                      json={"key": "student_academic_coach"}, headers=_h(ta)).json()["id"]
    payload = {"goals": ["X"]}
    assert c.post(f"/workspaces/{wa}/agents/{teacher_id}/study-plans",
                  headers=_h(ta), json=payload).status_code == 422
    assert c.post(f"/workspaces/{wa}/agents/{coach_id}/study-plans",
                  headers=_h(tb), json=payload).status_code == 404
