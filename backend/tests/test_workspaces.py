"""Workspace CRUD + membership isolation (SQLite, no live services)."""

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
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db):
    def override():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def _user(db, email, admin=False) -> User:
    u = User(id=uuid.uuid4(), email=email, password_hash=hash_password("password123"),
             full_name=email, is_admin=admin)
    db.add(u)
    db.commit()
    return u


def _token(client, email) -> str:
    r = client.post("/auth/login", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(token) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_workspace_flow_and_isolation(client, db):
    _user(db, "a@x.com")
    _user(db, "b@x.com")
    ta, tb = _token(client, "a@x.com"), _token(client, "b@x.com")

    assert client.post("/workspaces", json={"name": "X", "type": "weird"}).status_code in (401, 403, 422)
    r = client.post("/workspaces", json={"name": "Chem", "type": "personal"}, headers=_h(ta))
    assert r.status_code == 201, r.text
    ws_id = r.json()["id"]

    # owner sees it; outsider gets 404 (existence not leaked)
    assert client.get(f"/workspaces/{ws_id}", headers=_h(ta)).status_code == 200
    assert client.get(f"/workspaces/{ws_id}", headers=_h(tb)).status_code == 404

    # listing is scoped per user
    assert len(client.get("/workspaces", headers=_h(ta)).json()) == 1
    assert client.get("/workspaces", headers=_h(tb)).json() == []

    # owner adds B as member; then B sees it
    b_id = db.query(User).filter(User.email == "b@x.com").first().id
    add = client.post(f"/workspaces/{ws_id}/members",
                      json={"user_id": str(b_id), "role": "member"}, headers=_h(ta))
    assert add.status_code == 201, add.text
    assert client.get(f"/workspaces/{ws_id}", headers=_h(tb)).status_code == 200
    # duplicate membership rejected
    dup = client.post(f"/workspaces/{ws_id}/members",
                      json={"user_id": str(b_id), "role": "member"}, headers=_h(ta))
    assert dup.status_code == 409

    # plain member cannot add others
    _user(db, "c@x.com")
    tc = _token(client, "c@x.com")
    c_id = db.query(User).filter(User.email == "c@x.com").first().id
    denied = client.post(f"/workspaces/{ws_id}/members",
                         json={"user_id": str(c_id), "role": "member"}, headers=_h(tb))
    assert denied.status_code == 403

    members = client.get(f"/workspaces/{ws_id}/members", headers=_h(ta)).json()
    assert {m["email"] for m in members} == {"a@x.com", "b@x.com"}


def test_admin_sees_all_workspaces(client, db):
    _user(db, "a@x.com")
    _user(db, "root@x.com", admin=True)
    ta = _token(client, "a@x.com")
    tr = _token(client, "root@x.com")
    client.post("/workspaces", json={"name": "A-ws"}, headers=_h(ta))
    all_ws = client.get("/workspaces", headers=_h(tr)).json()
    assert len(all_ws) == 1
