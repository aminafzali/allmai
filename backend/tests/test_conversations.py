"""Conversation titles (AI, once) + user edit/pin + pinned-first list."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.agents import service as svc
from app.agents.models import Agent
from app.common.base import Base
from app.conversations.models import Conversation
from app.core.security import hash_password
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


def _seed(db):
    u = User(id=uuid.uuid4(), email="u@x.com",
             password_hash=hash_password("password123"))
    w = Workspace(id=uuid.uuid4(), name="W", type="shared", owner_user_id=u.id)
    a = Agent(id=uuid.uuid4(), workspace_id=w.id, key="note_taking_assistant",
              type="agent", name="N")
    db.add_all([u, w, a])
    db.commit()
    return u, w, a


def _conv(db, w, a, u, title=""):
    c = Conversation(id=uuid.uuid4(), workspace_id=w.id, agent_id=a.id,
                     user_id=u.id, state={}, title=title)
    db.add(c)
    db.commit()
    return c


def test_maybe_title_once_and_failopen(db):
    u, w, a = _seed(db)
    c = _conv(db, w, a, u)
    seen = {}

    def fake_gen(prompt, **kw):
        seen["prompt"] = prompt
        return '  "خلاصه منابع"  '

    assert svc.maybe_title_conversation(db, c, "خلاصه منابع را بگو", fake_gen) == "خلاصه منابع"
    assert "کاربر" in seen["prompt"]
    # second call keeps the existing title (no second LLM call)
    seen.clear()
    assert svc.maybe_title_conversation(db, c, "چیز دیگر", fake_gen) == "خلاصه منابع"
    assert seen == {}
    # raising generator -> fail-open
    c2 = _conv(db, w, a, u)

    def boom(prompt, **kw):
        raise RuntimeError("down")

    assert svc.maybe_title_conversation(db, c2, "سلام", boom) == ""
    assert svc.maybe_title_conversation(db, c2, "سلام", None) == ""


def test_patch_and_pinned_first(db):
    from fastapi import HTTPException

    u, w, a = _seed(db)
    c1 = _conv(db, w, a, u, title="اول")
    c2 = _conv(db, w, a, u, title="دوم")
    out = svc.patch_conversation(db, w, a, u, c1.id, title="اولِ ویراسته", pinned=True)
    assert out.title == "اولِ ویراسته" and out.pinned is True
    ids = [c.id for c in svc.list_conversations(db, w, a, u)]
    assert ids == [c1.id, c2.id]  # pinned first
    other_agent = Agent(id=uuid.uuid4(), workspace_id=w.id, key="x",
                        type="agent", name="X")
    db.add(other_agent)
    db.commit()
    with pytest.raises(HTTPException):
        svc.patch_conversation(db, w, other_agent, u, c1.id, title="hijack")
