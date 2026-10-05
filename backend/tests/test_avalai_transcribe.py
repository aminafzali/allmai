"""AvalAI transcription route (gpt-4o-mini-transcribe) + GapGPT route intact.

- Routing is purely by model id (panel `audio.transcription` row wins).
- AvalAI speaks json/text only (no verbose_json timestamps) -> single segment.
- 429/5xx retry with Retry-After; the GapGPT whisper path is untouched.
"""
import io

import httpx
import pytest

from app.knowledge.parsers import audio as A


def _wav(*, seconds=1):
    import math
    import struct
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 16000)))
            for i in range(16000 * seconds)
        ))
    return buf.getvalue()


class Resp:
    def __init__(self, status=200, payload=None, headers=None, raw=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        req = httpx.Request("POST", "http://x")
        if raw is not None:
            self._resp = httpx.Response(status, content=raw.encode(), headers=self.headers, request=req)
        else:
            import json as _json
            self._resp = httpx.Response(status, json=payload, headers=self.headers, request=req)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"{self.status_code}", request=self._resp.request, response=self._resp)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    @property
    def text(self):
        return self._resp.text


def _no_sleep(monkeypatch):
    sleeps = []

    class FakeTime:
        @staticmethod
        def sleep(s):
            sleeps.append(s)

    monkeypatch.setattr(A, "_time", FakeTime)
    return sleeps


def test_avalai_route_hits_avalai_with_json(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        seen["url"] = url
        seen["auth"] = headers.get("Authorization", "")
        seen["fname"] = files["file"][0]
        seen["ctype"] = files["file"][2]
        seen["model"] = data["model"]
        seen["format"] = data["response_format"]
        return Resp(200, {"text": "سلام دنیا"})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(A.get_settings(), "AVALAI_API_KEY", "test-key")
    out = A.transcribe_avalai_api(_wav(), "voice.webm", model="gpt-4o-mini-transcribe")
    assert out == [(0, 0, "سلام دنیا")]
    assert seen["url"] == "https://api.avalai.ir/v1/audio/transcriptions"
    assert seen["auth"] == "Bearer test-key"
    assert seen["model"] == "gpt-4o-mini-transcribe"
    assert seen["format"] == "json"  # NOT verbose_json (GPT-4o models lack it)
    assert seen["fname"] == "audio.webm"  # real container kept (not .mp3)
    assert seen["ctype"] == "audio/webm"


def test_avalai_segments_honored_when_present(monkeypatch):
    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        return Resp(200, {"segments": [
            {"start": 0.0, "end": 1.5, "text": "one"},
            {"start": 1.5, "end": 3.0, "text": "two"},
        ]})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(A.get_settings(), "AVALAI_API_KEY", "k")
    assert A.transcribe_avalai_api(b"x", "a.mp3") == [(0, 1500, "one"), (1500, 3000, "two")]


def test_avalai_text_format_wrapped_or_raw(monkeypatch):
    calls = {"n": 0}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return Resp(200, {"text": "C\n", "usage": None})
        return Resp(200, payload=None, raw="plain raw transcript")

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(A.get_settings(), "AVALAI_API_KEY", "k")
    assert A.transcribe_avalai_api(b"x", "a.mp3") == [(0, 0, "C")]
    assert A.transcribe_avalai_api(b"x", "a.mp3") == [(0, 0, "plain raw transcript")]


def test_language_forwarded_to_both_routes(monkeypatch):
    """fa/en hint reaches the provider (kills cross-language misdetect)."""
    seen = {}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        seen.setdefault("calls", []).append(dict(data or {}))
        return Resp(200, {"text": "x"})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(A.get_settings(), "AVALAI_API_KEY", "k")
    A.transcribe_avalai_api(b"x", "a.mp3", language="fa")
    assert seen["calls"][-1].get("language") == "fa"
    A.transcribe_avalai_api(b"x", "a.mp3")
    assert "language" not in seen["calls"][-1]  # unset = auto-detect
    monkeypatch.setattr(A.get_settings(), "OPENAI_COMPAT_API_KEY", "g")
    A.transcribe_whisper_api(b"ID3", "v.mp3", model="whisper-1", language="en")
    assert seen["calls"][-1].get("language") == "en"


def test_avalai_requires_key(monkeypatch):
    monkeypatch.setattr(A.get_settings(), "AVALAI_API_KEY", "")
    with pytest.raises(A.TranscriptionUnavailable):
        A.transcribe_avalai_api(b"x", "a.mp3")


def test_gapgpt_whisper_route_intact(monkeypatch):
    """The old route is NOT deleted: whisper-1 still hits GapGPT verbose_json."""
    seen = {}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        seen["url"] = url
        seen["format"] = data["response_format"]
        return Resp(200, {"segments": [{"start": 0, "end": 2, "text": "hi"}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    out = A.transcribe_whisper_api(b"ID3...", "v.mp3", model="whisper-1")
    assert out == [(0, 2000, "hi")]
    assert "gapgpt" in seen["url"]
    assert seen["format"] == "verbose_json"


def test_retry_then_success_on_429(monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    calls = {"n": 0}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return Resp(429, {"error": "slow"}, headers={"retry-after": "7"})
        return Resp(200, {"text": "ok"})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(A.get_settings(), "AVALAI_API_KEY", "k")
    assert A.transcribe_avalai_api(b"x", "a.mp3") == [(0, 0, "ok")]
    assert calls["n"] == 2
    assert sleeps and sleeps[0] == 7.0  # Retry-After honored


def test_persistent_429_raises_after_retries(monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    calls = {"n": 0}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        calls["n"] += 1
        return Resp(429, {"error": "busy"})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(A.get_settings(), "AVALAI_API_KEY", "k")
    with pytest.raises(A.TranscriptionUnavailable) as ei:
        A.transcribe_avalai_api(b"x", "a.mp3")
    assert calls["n"] == int(A.get_settings().TRANSCRIBE_MAX_RETRIES) + 1
    assert len(sleeps) == int(A.get_settings().TRANSCRIBE_MAX_RETRIES)
    assert "429" in str(ei.value)


def test_dispatch_by_model_id(monkeypatch):
    """transcribe() routes purely by model id; gemini path untouched."""
    seen = {}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        seen["url"] = url
        return Resp(200, {"text": "t"})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(A.get_settings(), "AVALAI_API_KEY", "k")
    assert A.transcribe(b"x", "a.mp3", model="gpt-4o-mini-transcribe") == [(0, 0, "t")]
    assert seen["url"].startswith("https://api.avalai.ir")
    assert A.is_avalai_transcribe_model("gpt-4o-transcribe")
    assert not A.is_avalai_transcribe_model("whisper-1")


def test_resolve_order_explicit_env_fallback(monkeypatch):
    assert A.resolve_transcription_model(None, explicit="whisper-1") == "whisper-1"
    s = A.get_settings()
    monkeypatch.setattr(s, "WHISPER_API_MODEL", "whisper-1")
    assert A.resolve_transcription_model(None) == "whisper-1"


def test_resolve_db_row_wins(monkeypatch):
    """Panel row overrides ENV (switchable from /admin/ai-settings)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    import app.models  # noqa: F401  (register metadata)
    from app.admin.models import AISetting
    from app.common.base import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = A.get_settings()
    monkeypatch.setattr(s, "WHISPER_API_MODEL", "whisper-1")
    with Session(engine) as db:
        db.add(AISetting(key="audio.transcription",
                         value={"provider": "openai_compat", "model": "gpt-4o-mini-transcribe"}))
        db.commit()
        assert A.resolve_transcription_model(db) == "gpt-4o-mini-transcribe"
