"""API smoke tests: no external services required."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_version():
    r = client.get("/version")
    assert r.status_code == 200
    assert "version" in r.json()


def test_ai_providers_no_secrets():
    r = client.get("/ai/providers")
    assert r.status_code == 200
    body = r.json()
    assert "embedding" in body
    dumped = r.text.lower()
    assert "api_key" not in dumped
    assert "secret" not in dumped


def test_cors_allows_configured_web_origins():
    r = client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:3001",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3001"
