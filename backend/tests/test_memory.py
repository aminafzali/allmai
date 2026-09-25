"""Memory engine tests: facts, recall scoring, summaries, isolation, roles."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.common.base import Base
from app.core.database import get_db
from app.core.security import hash_password
from app.main import app
from app.users.models import User

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


def _user(db, email) -> User:
    u = User(id=uuid.uuid4(), email=email, password_hash=hash_password("password123"), full_name=email)
    db.add(u)
    db.commit()
    return u


def _token(c, email):
    return c.post("/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]


def _h(t):
    return {"Authorization": f"Bearer {t}"}


def test_memory_flow_and_isolation(client, db):
    c = client
    _user(db, "a@x.com")
    _user(db, "b@x.com")
    ta, tb = _token(c, "a@x.com"), _token(c, "b@x.com")

    wa = c.post("/workspaces", json={"name": "WA"}, headers=_h(ta)).json()["id"]
    wb = c.post("/workspaces", json={"name": "WB"}, headers=_h(tb)).json()["id"]

    # A remembers in own workspace
    r = c.post(f"/workspaces/{wa}/memory/facts", headers=_h(ta),
               json={"key": "math_goal", "value": "master calculus by June", "category": "goal"})
    assert r.status_code == 201, r.text
    c.post(f"/workspaces/{wa}/memory/facts", headers=_h(ta),
           json={"key": "fav_subject", "value": "chemistry"})
    # overwrite same key
    c.post(f"/workspaces/{wa}/memory/facts", headers=_h(ta),
           json={"key": "math_goal", "value": "master algebra by May", "category": "goal"})
    facts = c.get(f"/workspaces/{wa}/memory/facts", headers=_h(ta)).json()
    assert len(facts) == 2
    assert next(f for f in facts if f["key"] == "math_goal")["value"] == "master algebra by May"

    # recall ranks the matching fact first (goal boost)
    hits = c.post(f"/workspaces/{wa}/memory/search", headers=_h(ta),
                  json={"query": "algebra goal", "top_k": 5}).json()
    assert hits[0]["key"] == "math_goal"
    assert hits[0]["score"] > 0
    assert c.post(f"/workspaces/{wa}/memory/search", headers=_h(ta),
                  json={"query": "quantum basketball"}).json() == []

    # summaries round-trip + overwrite
    s = c.post(f"/workspaces/{wa}/memory/summaries", headers=_h(ta),
               json={"scope": "study", "summary": "week 1 done"}).json()
    assert s["summary"] == "week 1 done"
    c.post(f"/workspaces/{wa}/memory/summaries", headers=_h(ta),
           json={"scope": "study", "summary": "week 2 done"})
    got = c.get(f"/workspaces/{wa}/memory/summaries", headers=_h(ta)).json()
    assert len(got) == 1 and got[0]["summary"] == "week 2 done"

    # B (outsider) sees nothing of A's workspace; same key in B is separate
    assert c.get(f"/workspaces/{wa}/memory/facts", headers=_h(tb)).status_code == 404
    c.post(f"/workspaces/{wb}/memory/facts", headers=_h(tb),
           json={"key": "math_goal", "value": "B own value"})
    b_facts = c.get(f"/workspaces/{wb}/memory/facts", headers=_h(tb)).json()
    assert b_facts[0]["value"] == "B own value"
    assert c.post(f"/workspaces/{wb}/memory/search", headers=_h(tb),
                  json={"query": "algebra"}).json() == []


def test_cross_user_read_needs_owner_role(client, db):
    c = client
    _user(db, "owner@x.com")
    _user(db, "member@x.com")
    to = _token(c, "owner@x.com")
    tm = _token(c, "member@x.com")

    wa = c.post("/workspaces", json={"name": "W"}, headers=_h(to)).json()["id"]
    member_id = db.query(User).filter(User.email == "member@x.com").one().id
    c.post(f"/workspaces/{wa}/members", headers=_h(to),
           json={"user_id": str(member_id), "role": "member"})
    c.post(f"/workspaces/{wa}/memory/facts", headers=_h(tm),
           json={"key": "k", "value": "member secret"})

    owner_id = db.query(User).filter(User.email == "owner@x.com").one().id
    # member cannot read owner's facts
    assert c.get(f"/workspaces/{wa}/memory/facts", headers=_h(tm),
                 params={"user_id": str(owner_id)}).status_code == 403
    # owner can read member's facts
    got = c.get(f"/workspaces/{wa}/memory/facts", headers=_h(to),
                params={"user_id": str(member_id)}).json()
    assert got[0]["value"] == "member secret"
