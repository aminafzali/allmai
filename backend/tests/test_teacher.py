"""Teacher agent tests: structured plan, grounded references, persistence."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.agents.teacher.prompts import build_lesson_prompt
from app.agents.teacher.schemas import LessonPlanBody
from app.agents.teacher.service import get_generate_structured_fn
from app.common.base import Base
from app.core.database import get_db
from app.core.security import hash_password
from app.knowledge.models import Chunk, Document, KnowledgeBase, PageSegment, Source
from app.knowledge.retrieval.hybrid import get_embed_fn
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
    kb = uuid.uuid4()
    db.add(KnowledgeBase(id=kb, workspace_id=wa, title="Chem"))
    src = Source(id=uuid.uuid4(), workspace_id=wa, kb_id=kb, type="note",
                 filename="acids.pdf", status="ready")
    db.add(src)
    db.flush()
    doc = Document(id=uuid.uuid4(), workspace_id=wa, source_id=src.id, title="d")
    db.add(doc)
    db.flush()
    seg = PageSegment(id=uuid.uuid4(), workspace_id=wa, document_id=doc.id, page_no=7,
                      text="acids donate protons in water")
    db.add(seg)
    db.flush()
    chunk = Chunk(id=uuid.uuid4(), workspace_id=wa, kb_id=kb, segment_id=seg.id,
                  content="acids donate protons in water", tokens=5,
                  embedding=[1.0, 0.0], chunk_metadata={})
    db.add(chunk)
    db.commit()
    return {"wa": wa, "wb": wb, "kb": kb, "chunk": chunk}


def _fake_generate_structured(prompt, schema, **kw):
    PROMPTS.append(prompt)
    return LessonPlanBody(
        title="Acids lesson",
        objectives=["Define acids"],
        prerequisites=["Atoms"],
        topics=[{"title": "Intro", "points": ["pH"]}],
        activities=["Demo"],
        examples=["Lemon"],
        questions=["What is pH?"],
        assessment=["Quiz"],
    )


@pytest.fixture()
def client(db):
    _seed(db)
    PROMPTS.clear()

    def override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_embed_fn] = lambda: (lambda texts: [[1.0, 0.0] for _ in texts])
    app.dependency_overrides[get_generate_structured_fn] = lambda: _fake_generate_structured
    yield TestClient(app)
    app.dependency_overrides.clear()


def _token(c, email):
    return c.post("/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]


def _h(t):
    return {"Authorization": f"Bearer {t}"}


def _make_agent(c, ta, wa, key="teacher_lesson_planner"):
    return c.post(f"/workspaces/{wa}/agents", json={"key": key}, headers=_h(ta)).json()["id"]


def test_lesson_plan_structured_and_grounded(client):
    c = client
    ta = _token(c, "a@x.com")
    with TestingSession() as s:
        wa = s.query(Workspace).filter(Workspace.name == "A").one().id
        kb = s.query(KnowledgeBase).filter(KnowledgeBase.title == "Chem").one().id
        chunk = s.query(Chunk).first()
    agent_id = _make_agent(c, ta, wa)

    r = c.post(f"/workspaces/{wa}/agents/{agent_id}/lesson-plans", headers=_h(ta), json={
        "kb_id": str(kb), "chapter": "Acids and bases", "grade": "9th",
        "duration_minutes": 60, "teaching_style": "group work",
        "instructions": "Focus on lab safety.",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    plan = body["plan"]
    assert plan["title"] == "Acids lesson"
    assert plan["objectives"] == ["Define acids"]
    assert plan["topics"][0]["title"] == "Intro"
    assert plan["assessment"] == ["Quiz"]
    # references are grounded in REAL retrieval, not the LLM
    assert len(plan["references"]) == 1
    ref = plan["references"][0]
    assert ref["chunk_id"] == str(chunk.id)
    assert ref["page_no"] == 7 and ref["source"] == "acids.pdf"
    assert body["citations"] == plan["references"]
    # prompt carried chapter + style + context
    assert "Acids and bases" in PROMPTS[-1] and "group work" in PROMPTS[-1]
    assert "lab safety" in PROMPTS[-1] or "Lab safety" in PROMPTS[-1] or "safety" in PROMPTS[-1]

    # persisted as conversation messages (plan detail view reads these)
    msgs = c.get(f"/workspaces/{wa}/conversations/{body['conversation_id']}/messages",
                 headers=_h(ta)).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert "Acids lesson" in msgs[1]["content"]

    # structured row: list + detail (Source of Truth, not just chat text)
    plans = c.get(f"/workspaces/{wa}/agents/{agent_id}/lesson-plans", headers=_h(ta)).json()
    assert len(plans) == 1 and plans[0]["chapter"] == "Acids and bases"
    assert plans[0]["id"] == body["lesson_plan_id"]
    detail = c.get(
        f"/workspaces/{wa}/agents/{agent_id}/lesson-plans/{body['lesson_plan_id']}",
        headers=_h(ta)).json()
    assert detail["plan"]["title"] == "Acids lesson"
    assert detail["plan"]["references"][0]["page_no"] == 7
    assert detail["kb_id"] == str(kb)
    # outsider cannot read the plan (IDOR)
    tb = _token(c, "b@x.com")
    assert c.get(
        f"/workspaces/{wa}/agents/{agent_id}/lesson-plans/{body['lesson_plan_id']}",
        headers=_h(tb)).status_code == 404
    # by-conversation structured lookup (detail view Source of Truth)
    byconv = c.get(
        f"/workspaces/{wa}/lesson-plans/by-conversation/{body['conversation_id']}",
        headers=_h(ta)).json()
    assert byconv["plan"]["title"] == "Acids lesson"
    assert c.get(
        f"/workspaces/{wa}/lesson-plans/by-conversation/{body['conversation_id']}",
        headers=_h(tb)).status_code == 404


def test_lesson_plan_wrong_agent_and_isolation(client):
    c = client
    ta, tb = _token(c, "a@x.com"), _token(c, "b@x.com")
    with TestingSession() as s:
        wa = s.query(Workspace).filter(Workspace.name == "A").one().id
        kb = s.query(KnowledgeBase).filter(KnowledgeBase.title == "Chem").one().id
    student_id = _make_agent(c, ta, wa, key="student_academic_coach")
    payload = {"kb_id": str(kb), "chapter": "Acids", "grade": "9th"}
    assert c.post(f"/workspaces/{wa}/agents/{student_id}/lesson-plans",
                  headers=_h(ta), json=payload).status_code == 422
    teacher_id = _make_agent(c, ta, wa)
    assert c.post(f"/workspaces/{wa}/agents/{teacher_id}/lesson-plans",
                  headers=_h(tb), json=payload).status_code == 404


def test_prompt_builder_pure():
    p = build_lesson_prompt("Acids", "9th", 45, "lecture", "", "ctx")
    assert "Acids" in p and "45" in p and "ctx" in p


def test_lesson_prompt_template_parity():
    from app.agents.prompt_templates import DEFAULTS, render

    out = render(DEFAULTS["lesson_plan"], {
        "chapter": "Acids", "grade": "9th", "duration_minutes": 45,
        "teaching_style": "lecture",
        "instructions_block": "\nTeacher's extra instructions: Focus.",
        "context": "ctx"})
    assert out == build_lesson_prompt("Acids", "9th", 45, "lecture", "Focus.", "ctx")
    assert render("a {nope} b", {}) is None  # unrenderable -> default
    assert render("", {}) is None and render(None, {}) is None


def test_lesson_plan_custom_template_used(db, monkeypatch):
    from app.agents.definitions_models import AgentDefinition
    from app.agents.models import Agent
    from app.agents.teacher.schemas import LessonPlanIn
    from app.agents.teacher.service import build_lesson_plan

    _seed(db)
    with TestingSession() as s:
        wa = s.query(Workspace).filter(Workspace.name == "A").one()
        kb = s.query(KnowledgeBase).filter(KnowledgeBase.title == "Chem").one()
        ua = s.query(User).filter(User.email == "a@x.com").one()
        wa_id, kb_id, ua_id = wa.id, kb.id, ua.id
    definition = AgentDefinition(
        key="teacher_lesson_planner", title="T", instructions="Teach.",
        prompt_templates={"lesson_plan": "CUSTOM[{chapter}|{grade}|{duration_minutes}|"
                                         "{teaching_style}|{instructions_block}|{context}]"})
    db.add(definition)
    db.commit()
    db.add(Agent(workspace_id=wa_id, key="teacher_lesson_planner",
                 type="agent", name="T", definition_id=definition.id))
    db.commit()
    with TestingSession() as s:
        agent = s.query(Agent).filter(Agent.workspace_id == wa_id).one()
        ws = s.query(Workspace).filter(Workspace.id == wa_id).one()
        user = s.query(User).filter(User.id == ua_id).one()
        body = LessonPlanIn(kb_id=kb_id, chapter="Acids", grade="9th")
        out = build_lesson_plan(s, ws, agent, user, body,
                                embed_fn=lambda texts: [[1.0, 0.0] for _ in texts],
                                generate_structured_fn=_fake_generate_structured)
    assert out.plan.title == "Acids lesson"
    assert PROMPTS[-1].startswith("CUSTOM[Acids|9th|45|")
    assert "curriculum designer" not in PROMPTS[-1]


def test_lesson_plan_uses_agent_provider_and_model(db, monkeypatch):
    from app.agents.models import Agent
    from app.agents.teacher.schemas import LessonPlanIn
    from app.agents.teacher.service import build_lesson_plan
    from app.ai import factory as F
    from app.ai.settings import upsert_setting

    _seed(db)
    with TestingSession() as s:
        wa = s.query(Workspace).filter(Workspace.name == "A").one()
        kb = s.query(KnowledgeBase).filter(KnowledgeBase.title == "Chem").one()
        ua = s.query(User).filter(User.email == "a@x.com").one()
        wa_id, kb_id, ua_id = wa.id, kb.id, ua.id
    upsert_setting(db, "agent.teacher_lesson_planner",
                   {"provider": "gemini", "model": "gemini-2.5-flash"})
    seen = {}

    class FakeProvider:
        def generate_structured(self, prompt, schema, model=None, **kw):
            seen["provider"] = "gemini"
            seen["model"] = model
            return _fake_generate_structured(prompt, schema)

    def fake_get_provider(name="openai_compat"):
        seen["asked"] = name
        return FakeProvider()

    monkeypatch.setattr(F, "get_provider", fake_get_provider)
    db.add(Agent(workspace_id=wa_id, key="teacher_lesson_planner",
                 type="agent", name="T"))
    db.commit()
    with TestingSession() as s:
        agent = s.query(Agent).filter(Agent.workspace_id == wa_id).one()
        ws = s.query(Workspace).filter(Workspace.id == wa_id).one()
        user = s.query(User).filter(User.id == ua_id).one()
        body = LessonPlanIn(kb_id=kb_id, chapter="Acids", grade="9th")
        out = build_lesson_plan(s, ws, agent, user, body,
                                embed_fn=lambda texts: [[1.0, 0.0] for _ in texts],
                                generate_structured_fn=None)
    assert seen["asked"] == "gemini" and seen["model"] == "gemini-2.5-flash"
    assert out.plan.title == "Acids lesson"
