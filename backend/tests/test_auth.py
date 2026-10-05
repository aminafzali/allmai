"""Auth + RBAC + audit + admin AI-settings tests (SQLite, no live services)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register metadata)
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
    # Fresh tables per test.
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


def _register(client, email="t@example.com", password="secret123"):
    return client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "Test"},
    )


def _login(client, email="t@example.com", password="secret123"):
    return client.post("/auth/login", json={"email": email, "password": password})


def _make_admin(db: Session) -> User:
    admin = User(
        id=uuid.uuid4(), email="admin@example.com",
        password_hash=hash_password("admin1234"), full_name="Admin", is_admin=True,
    )
    db.add(admin)
    db.commit()
    return admin


def test_register_login_me_refresh_logout_flow(client, db):
    assert _register(client).status_code == 201
    assert _register(client).status_code == 409  # duplicate

    r = _login(client)
    assert r.status_code == 200
    pair = r.json()
    headers = {"Authorization": f"Bearer {pair['access_token']}"}

    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == "t@example.com"

    r2 = client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r2.status_code == 200
    # rotation: old refresh token is dead
    assert client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]}).status_code == 401

    assert client.post("/auth/logout", json={"refresh_token": r2.json()["refresh_token"]}).status_code == 200
    assert client.post("/auth/refresh", json={"refresh_token": r2.json()["refresh_token"]}).status_code == 401


def test_login_wrong_password_401_and_audit(client, db):
    _register(client)
    assert _login(client, password="nope-not-this").status_code == 401
    assert client.get("/auth/me").status_code in (401, 403)
    from app.common.audit import AuditLog

    actions = [a for (a,) in db.query(AuditLog.action).all()]
    assert "auth.register" in actions
    assert "auth.login_failed" in actions


def test_admin_settings_rbac_and_update(client, db):
    # anonymous + plain user are blocked
    assert client.get("/admin/ai-settings").status_code in (401, 403)
    _register(client)
    user_headers = {"Authorization": f"Bearer {_login(client).json()['access_token']}"}
    assert client.get("/admin/ai-settings", headers=user_headers).status_code == 403

    admin = _make_admin(db)
    admin_headers = {
        "Authorization": f"Bearer {_login(client, email='admin@example.com', password='admin1234').json()['access_token']}"
    }
    assert admin.email == "admin@example.com"

    r = client.get("/admin/ai-settings", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    # cheap-model defaults (mirror ENV, work without seeded DB rows)
    assert body["chat.default"]["model"] == "gpt-4o-mini"
    assert body["embedding.default"]["model"] == "text-embedding-3-small"
    assert body["embedding.default"]["dim"] == 1536
    assert body["audio.transcription"]["model"] == "gpt-4o-mini-transcribe"

    up = client.put(
        "/admin/ai-settings",
        headers=admin_headers,
        json={"key": "chat.default", "value": {"model": "gpt-4.1-nano", "temperature": 0.5}},
    )
    assert up.status_code == 200
    assert up.json()["chat.default"]["model"] == "gpt-4.1-nano"
    assert up.json()["chat.default"]["temperature"] == 0.5
    # unspecified fields survive the merge
    assert up.json()["chat.default"]["provider"] == "openai_compat"

    bad = client.put(
        "/admin/ai-settings", headers=admin_headers,
        json={"key": "nope.unknown", "value": {}},
    )
    assert bad.status_code == 422


def test_audit_logs_admin_only(client, db):
    _register(client)
    user_headers = {"Authorization": f"Bearer {_login(client).json()['access_token']}"}
    assert client.get("/admin/audit-logs", headers=user_headers).status_code == 403
    _make_admin(db)
    admin_headers = {
        "Authorization": f"Bearer {_login(client, email='admin@example.com', password='admin1234').json()['access_token']}"
    }
    rows = client.get("/admin/audit-logs", headers=admin_headers).json()
    assert any(r["action"] == "auth.register" for r in rows)
