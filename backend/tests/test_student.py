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

    from app.knowledge.retrieval.hybrid import get_generate_tools_fn

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_embed_fn] = lambda: (lambda texts: [[0.0] for _ in texts])
    app.dependency_overrides[get_generate_fn] = lambda: (lambda prompt, **kw: "coach reply")
    app.dependency_overrides[get_generate_structured_fn] = lambda: _fake_structured
    # hermetic: no live tool rounds in the legacy-path tests
    app.dependency_overrides[get_generate_tools_fn] = lambda: None
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


def test_coach_prompt_shows_task_detail(client):
    from app.agents.student import service as CS

    c, seed = client
    ta = _token(c, "a@x.com")
    wa = seed["wa"]
    aid = c.post(f"/workspaces/{wa}/agents",
                 json={"key": "student_academic_coach"}, headers=_h(ta)).json()["id"]
    plan_r = c.post(f"/workspaces/{wa}/agents/{aid}/study-plans", headers=_h(ta), json={
        "goals": ["Pass algebra exam"], "weekly_hours": 6})
    conv_id = plan_r.json()["conversation_id"]
    c.post(f"/workspaces/{wa}/agents/{aid}/progress", headers=_h(ta), json={
        "conversation_id": conv_id, "completed_task_ids": ["w1t1"]})
    with TestingSession() as s:
        from app.agents.models import Agent as _A
        from app.workspaces.models import Workspace as _W
        from app.users.models import User as _U
        ws = s.query(_W).filter(_W.id == wa).one()
        user = s.query(_U).filter(_U.email == "a@x.com").one()
        ag = s.query(_A).filter(_A.workspace_id == wa).one()
        conv, state, mem, prompt, model, chat = CS._coach_prompt(
            s, ws, ag, user, "what next?", conv_id)
    assert "[x] w1t1" in prompt  # done vs pending is explicit
    assert "1/1 tasks done" in prompt


def test_coach_tool_loop_marks_progress(client):
    from app.agents.student import service as CS

    c, seed = client
    ta = _token(c, "a@x.com")
    wa = seed["wa"]
    aid = c.post(f"/workspaces/{wa}/agents",
                 json={"key": "student_academic_coach"}, headers=_h(ta)).json()["id"]
    c.post(f"/workspaces/{wa}/agents/{aid}/study-plans", headers=_h(ta), json={
        "goals": ["Pass algebra exam"], "weekly_hours": 6})
    calls = {"tools": 0, "final": 0}

    def fake_tools(messages, tools, model=None, **kw):
        calls["tools"] += 1
        if calls["tools"] == 1:
            return {"text": "", "calls": [
                {"id": "1", "name": "update_progress",
                 "arguments": {"completed_task_ids": ["w1t1"], "note": "easy"}}]}
        return {"text": "ثبت شد، آفرین!", "calls": []}

    def fake_generate(prompt, **kw):
        calls["final"] += 1
        return "FINAL"

    with TestingSession() as s:
        from app.agents.models import Agent as _A
        from app.workspaces.models import Workspace as _W
        from app.users.models import User as _U
        ws = s.query(_W).filter(_W.id == wa).one()
        user = s.query(_U).filter(_U.email == "a@x.com").one()
        ag = s.query(_A).filter(_A.workspace_id == wa).one()
        out = CS.coach_chat(s, ws, ag, user, "w1t1 را تمام کردم",
                            generate_fn=fake_generate,
                            generate_tools_fn=fake_tools)
    assert out.tools_used == ["update_progress"]
    # model gave its own final text on the 2nd turn: used directly, no synthesis call
    assert out.answer == "ثبت شد، آفرین!" and calls["final"] == 0
    assert out.has_plan is True
    with TestingSession() as s:
        from app.conversations.models import Conversation
        convs = s.query(Conversation).all()
        assert any("w1t1" in (c.state.get("progress", {}).get("completed", []))
                   for c in convs)


def test_coach_tool_creates_plan(client, monkeypatch):
    from app.agents.models import Agent
    from app.agents.student import service as CS
    from app.ai import openai_compat as OC

    c, seed = client
    ta = _token(c, "a@x.com")
    wa = seed["wa"]
    aid = c.post(f"/workspaces/{wa}/agents",
                 json={"key": "student_academic_coach"}, headers=_h(ta)).json()["id"]

    def fake_tools(messages, tools, model=None, **kw):
        n = getattr(fake_tools, "n", 0) + 1
        fake_tools.n = n
        if n == 1:
            return {"text": "", "calls": [
                {"id": "1", "name": "create_study_plan",
                 "arguments": {"goals": ["Learn fractions"], "weekly_hours": 4}}]}
        return {"text": "برنامه ساخته شد.", "calls": []}

    monkeypatch.setattr(OC.OpenAICompatProvider, "generate_structured",
                        lambda self, prompt, schema, model=None, **kw: _fake_structured(prompt, schema))
    with TestingSession() as s:
        from app.workspaces.models import Workspace as _W
        from app.users.models import User as _U
        ws = s.query(_W).filter(_W.id == wa).one()
        user = s.query(_U).filter(_U.email == "a@x.com").one()
        ag = s.query(Agent).filter(Agent.workspace_id == wa).one()
        out = CS.coach_chat(s, ws, ag, user, "برایم برنامه بریز",
                            generate_fn=lambda prompt, **kw: "FINAL",
                            generate_tools_fn=fake_tools)
    assert out.tools_used == ["create_study_plan"]
    assert out.has_plan is True and out.answer == "برنامه ساخته شد."
    with TestingSession() as s:
        from app.conversations.models import Conversation
        assert any((c.state.get("plan") or {}).get("weeks") for c in s.query(Conversation).all())


def test_coach_persist_keeps_tool_writes_in_plan_conv(client):
    """Regression: tools acting on the CURRENT plan conversation must not
    be clobbered by the turn persist (stale state snapshot)."""
    from app.agents.models import Agent
    from app.agents.student import service as CS

    c, seed = client
    ta = _token(c, "a@x.com")
    wa = seed["wa"]
    aid = c.post(f"/workspaces/{wa}/agents",
                 json={"key": "student_academic_coach"}, headers=_h(ta)).json()["id"]
    plan_r = c.post(f"/workspaces/{wa}/agents/{aid}/study-plans", headers=_h(ta), json={
        "goals": ["Pass algebra exam"], "weekly_hours": 6})
    conv_id = plan_r.json()["conversation_id"]

    def fake_tools(messages, tools, model=None, **kw):
        n = getattr(fake_tools, "n", 0) + 1
        fake_tools.n = n
        if n == 1:
            return {"text": "", "calls": [
                {"id": "1", "name": "update_progress",
                 "arguments": {"completed_task_ids": ["w1t1"]}}]}
        return {"text": "done", "calls": []}

    with TestingSession() as s:
        from app.workspaces.models import Workspace as _W
        from app.users.models import User as _U
        ws = s.query(_W).filter(_W.id == wa).one()
        user = s.query(_U).filter(_U.email == "a@x.com").one()
        ag = s.query(Agent).filter(Agent.workspace_id == wa).one()
        out = CS.coach_chat(s, ws, ag, user, "w1t1 تمام شد",
                            conversation_id=conv_id,
                            generate_fn=lambda prompt, **kw: "FINAL",
                            generate_tools_fn=fake_tools)
        assert out.tools_used == ["update_progress"]
        s.refresh(ag)
        from app.common.base import coerce_uuid
        from app.conversations.models import Conversation
        conv = s.query(Conversation).filter(
            Conversation.id == coerce_uuid(conv_id)).one()
        assert "w1t1" in (conv.state.get("progress", {}).get("completed", []))
        assert conv.state.get("turns", 0) >= 1


def test_coach_tools_failure_falls_back_to_plain(client):
    from app.agents.models import Agent
    from app.agents.student import service as CS

    c, seed = client
    ta = _token(c, "a@x.com")
    wa = seed["wa"]
    c.post(f"/workspaces/{wa}/agents",
           json={"key": "student_academic_coach"}, headers=_h(ta))

    def boom(messages, tools, model=None, **kw):
        raise RuntimeError("tools down")

    with TestingSession() as s:
        from app.workspaces.models import Workspace as _W
        from app.users.models import User as _U
        ws = s.query(_W).filter(_W.id == wa).one()
        user = s.query(_U).filter(_U.email == "a@x.com").one()
        ag = s.query(Agent).filter(Agent.workspace_id == wa).one()
        out = CS.coach_chat(s, ws, ag, user, "سلام",
                            generate_fn=lambda prompt, **kw: "PLAIN",
                            generate_tools_fn=boom)
    assert out.answer == "PLAIN" and out.tools_used == []


def test_coach_tool_rounds_capped(client):
    from app.agents.models import Agent
    from app.agents.student import service as CS

    c, seed = client
    ta = _token(c, "a@x.com")
    wa = seed["wa"]
    c.post(f"/workspaces/{wa}/agents",
           json={"key": "student_academic_coach"}, headers=_h(ta))
    calls = {"tools": 0, "final": 0}

    def always_calls(messages, tools, model=None, **kw):
        calls["tools"] += 1
        return {"text": "", "calls": [
            {"id": str(calls["tools"]), "name": "get_study_plan", "arguments": {}}]}

    def fake_generate(prompt, **kw):
        calls["final"] += 1
        return "FINAL"

    with TestingSession() as s:
        from app.workspaces.models import Workspace as _W
        from app.users.models import User as _U
        ws = s.query(_W).filter(_W.id == wa).one()
        user = s.query(_U).filter(_U.email == "a@x.com").one()
        ag = s.query(Agent).filter(Agent.workspace_id == wa).one()
        out = CS.coach_chat(s, ws, ag, user, "برنامه‌ام را بگو",
                            generate_fn=fake_generate,
                            generate_tools_fn=always_calls)
    assert calls["tools"] == 3 and calls["final"] == 1
    assert out.tools_used == ["get_study_plan"] * 3 and out.answer == "FINAL"


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
