"""Security tests: IDOR, expired tokens, frontend key/vendor hygiene."""

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.common.base import Base
from app.conversations.models import Conversation, Message
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.users.models import User
from app.workspaces.models import Workspace, WorkspaceMember

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)
WEB_DIR = Path(__file__).resolve().parents[2] / "web"


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
    def override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _setup(db):
    ua = User(id=uuid.uuid4(), email="a@x.com", password_hash=hash_password("password123"), full_name="A")
    ub = User(id=uuid.uuid4(), email="b@x.com", password_hash=hash_password("password123"), full_name="B")
    db.add_all([ua, ub])
    wa = uuid.uuid4()
    db.add(Workspace(id=wa, name="A", type="shared", owner_user_id=ua.id))
    db.add(WorkspaceMember(workspace_id=wa, user_id=ua.id, role="owner"))
    db.commit()
    return ua, ub, wa


def _token(c, email):
    return c.post("/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]


def test_conversation_idor_blocked(client, db):
    ua, ub, wa = _setup(db)
    c = client
    ta, tb = _token(c, "a@x.com"), _token(c, "b@x.com")
    agent_id = c.post(f"/workspaces/{wa}/agents",
                      json={"key": "student_academic_coach"},
                      headers={"Authorization": f"Bearer {ta}"}).json()["id"]
    # B is not even a member: 404, not 403 (no existence leak)
    assert c.get(f"/workspaces/{wa}/agents/{agent_id}",
                 headers={"Authorization": f"Bearer {tb}"}).status_code == 404

    # B becomes member: still cannot read A's conversation (user-scoped)
    db.add(WorkspaceMember(workspace_id=wa, user_id=ub.id, role="member"))
    db.commit()
    from app.agents.models import Agent
    from app.common.base import coerce_uuid

    agent = db.query(Agent).filter(Agent.id == coerce_uuid(agent_id)).one()
    conv = Conversation(id=uuid.uuid4(), workspace_id=wa, agent_id=agent.id,
                        user_id=ua.id, state={})
    db.add(conv)
    db.flush()
    db.add(Message(id=uuid.uuid4(), workspace_id=wa, conversation_id=conv.id,
                   role="user", content="secret"))
    db.commit()
    r = c.get(f"/workspaces/{wa}/conversations/{conv.id}/messages",
              headers={"Authorization": f"Bearer {tb}"})
    assert r.status_code == 404
    ok = c.get(f"/workspaces/{wa}/conversations/{conv.id}/messages",
               headers={"Authorization": f"Bearer {ta}"})
    assert ok.status_code == 200 and ok.json()[0]["content"] == "secret"


def test_expired_and_foreign_tokens_rejected(client, db):
    _setup(db)
    c = client
    expired = create_access_token(str(uuid.uuid4()), minutes=-1)
    assert c.get("/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    assert c.get("/auth/me", headers={"Authorization": "Bearer garbage.token.here"}).status_code == 401
    assert c.get("/auth/me").status_code in (401, 403)


def test_frontend_has_no_ai_keys_or_vendor_calls():
    """Next.js may only talk to our own FastAPI. No keys, no vendor SDKs."""
    forbidden = [
        "sk-", "api.openai.com", "generativelanguage", "gapgpt",
        "dify", "raganything", "langgraph", "zep", "OPENAI_COMPAT_API_KEY",
        "GEMINI_API_KEY", "JWT_SECRET",
    ]
    violations = []
    for path in WEB_DIR.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(WEB_DIR).as_posix()
        if rel.startswith(("node_modules/", ".next/", "out/")):
            continue
        if path.suffix not in (".ts", ".tsx", ".js", ".mjs", ".json", ".css"):
            continue
        if path.name in ("package-lock.json",):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        low = text.lower()
        for marker in forbidden:
            if marker.lower() in low:
                # NEXT_PUBLIC_API_BASE default documents our own backend host; fine.
                if marker == "gapgpt" and "api_base" in low:
                    continue
                violations.append(f"{rel}: {marker}")
    assert violations == []
