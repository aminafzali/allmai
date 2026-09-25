"""Production-readiness unit tests: rate limits, PII, rerank, URL fetch,
provider retry/factory, workspace delete. All offline."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.ai.factory import get_provider
from app.common.base import Base
from app.common.pii import redact
from app.common.rate_limit import check, reset
from app.core.database import get_db
from app.core.security import hash_password
from app.knowledge.parsers.fallback import parse_url_descriptor
from app.knowledge.retrieval.rerank import get_rerank_fn, heuristic_rerank
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


def test_rate_limit_blocks_and_resets():
    from fastapi import HTTPException

    reset()
    for _ in range(3):
        check("t", "k", calls=3, period_seconds=60)
    with pytest.raises(HTTPException) as exc:
        check("t", "k", calls=3, period_seconds=60)
    assert exc.value.status_code == 429
    reset()
    check("t", "k", calls=3, period_seconds=60)  # fine again


def test_login_throttled_after_many_failures(client, db):
    reset()
    u = User(id=uuid.uuid4(), email="v@x.com", password_hash=hash_password("password123"), full_name="V")
    db.add(u)
    db.commit()
    statuses = set()
    for _ in range(12):
        r = client.post("/auth/login", json={"email": "v@x.com", "password": "wrong"})
        statuses.add(r.status_code)
    assert 429 in statuses
    reset()


def test_pii_redaction():
    assert redact("mail me at ali@example.com ok") == "mail me at [email] ok"
    assert redact("call +98 912 345 6789") == "call [phone]"
    assert redact("card 4111 1111 1111 1111") == "card [card]"
    assert redact("plain text") == "plain text"
    assert redact("") == ""


def test_audit_meta_redacted(client, db):
    from app.common.audit import AuditLog

    reset()
    u = User(id=uuid.uuid4(), email="w@x.com", password_hash=hash_password("password123"), full_name="W")
    db.add(u)
    db.commit()
    client.post("/auth/login", json={"email": "w@x.com", "password": "nope"})
    row = db.query(AuditLog).filter(AuditLog.action == "auth.login_failed").one()
    assert row.meta.get("email") == "[email]"
    reset()


def test_heuristic_rerank_promotes_exact_match():
    class H:
        def __init__(self, content, score):
            self.chunk = type("C", (), {"content": content})()
            self.score = score

    hits = [H("unrelated filler text about nothing", 0.9), H("acids donate protons", 0.1)]
    out = heuristic_rerank("acids donate protons", hits)
    assert out[0].chunk.content.startswith("acids")
    assert get_rerank_fn(None) is not None


def test_url_fetch_success_and_fallback():
    import json

    blob = json.dumps({"title": "T", "url": "https://example.com/x"}).encode()
    doc = parse_url_descriptor(blob, fetch_fn=lambda url: "Fetched page body here")
    assert doc.pages[0].text == "Fetched page body here"

    def boom(url):
        raise RuntimeError("down")

    doc2 = parse_url_descriptor(blob, fetch_fn=boom)
    assert "example.com" in doc2.pages[0].text  # descriptor fallback


def test_provider_retry_then_success(monkeypatch):
    import httpx
    from app.ai.openai_compat import OpenAICompatProvider

    calls = {"n": 0}

    class Resp:
        status_code = 200

        def raise_for_status(self):
            if calls["n"] < 2:
                raise httpx.HTTPStatusError("bad", request=None, response=self._r503())

        def _r503(self):
            r = httpx.Response(503)
            return r

        def json(self):
            return {"ok": True}

    def fake_post(*a, **k):
        calls["n"] += 1
        return Resp()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("time.sleep", lambda s: None)
    p = OpenAICompatProvider()
    p.api_key = "x"
    assert p._post("http://x", {}, 5) == {"ok": True}
    assert calls["n"] == 2


def test_provider_factory():
    from app.ai.gemini import GeminiProvider
    from app.ai.openai_compat import OpenAICompatProvider

    assert isinstance(get_provider("openai_compat"), OpenAICompatProvider)
    assert isinstance(get_provider("gemini"), GeminiProvider)
    with pytest.raises(ValueError):
        get_provider("nope")


def test_workspace_delete_owner_only(client, db):
    reset()
    for email in ("o@x.com", "m@x.com"):
        db.add(User(id=uuid.uuid4(), email=email, password_hash=hash_password("password123"), full_name=email))
    db.commit()

    def token(email):
        return client.post("/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]

    to, tm = token("o@x.com"), token("m@x.com")
    wid = client.post("/workspaces", json={"name": "W"}, headers={"Authorization": f"Bearer {to}"}).json()["id"]
    mid = db.query(User).filter(User.email == "m@x.com").one().id
    client.post(f"/workspaces/{wid}/members", headers={"Authorization": f"Bearer {to}"},
                json={"user_id": str(mid), "role": "member"})
    # member cannot delete
    assert client.delete(f"/workspaces/{wid}", headers={"Authorization": f"Bearer {tm}"}).status_code == 403
    assert client.delete(f"/workspaces/{wid}", headers={"Authorization": f"Bearer {to}"}).status_code == 200
    assert client.get(f"/workspaces/{wid}", headers={"Authorization": f"Bearer {to}"}).status_code == 404
    reset()
