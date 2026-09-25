"""Admin user management tests (SQLite)."""

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


def _user(db, email, admin=False) -> User:
    u = User(id=uuid.uuid4(), email=email, password_hash=hash_password("password123"),
             full_name=email, is_admin=admin)
    db.add(u)
    db.commit()
    return u


def _token(c, email):
    return c.post("/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]


def _h(t):
    return {"Authorization": f"Bearer {t}"}


def test_admin_users_list_and_toggle(client, db):
    _user(db, "root@x.com", admin=True)
    _user(db, "u@x.com")
    tr = _token(client, "root@x.com")
    tu = _token(client, "u@x.com")

    assert client.get("/admin/users", headers=_h(tu)).status_code == 403
    rows = client.get("/admin/users", headers=_h(tr)).json()
    assert {r["email"] for r in rows} == {"root@x.com", "u@x.com"}
    assert all("password" not in str(r).lower() for r in rows)

    target = next(r for r in rows if r["email"] == "u@x.com")
    up = client.patch(f"/admin/users/{target['id']}", headers=_h(tr),
                      json={"is_admin": True}).json()
    assert up["is_admin"] is True
    assert client.patch("/admin/users/00000000-0000-4000-8000-000000000000",
                        headers=_h(tr), json={"is_admin": True}).status_code == 404
