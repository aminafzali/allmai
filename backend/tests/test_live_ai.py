"""LIVE AI integration test — real GapGPT/Gemini calls, tiny cost.

Runs ONLY when RUN_LIVE_AI=1 and OPENAI_COMPAT_API_KEY is configured.
Nothing here is mocked: chat generate, embeddings (+dimension parity
with EMBEDDING_DIM / chunks.embedding), and structured generation.

    RUN_LIVE_AI=1 .venv/Scripts/python.exe -m pytest tests/test_live_ai.py -q
"""

import os

import pytest

from app.ai.embedding import get_embedding_config
from app.ai.openai_compat import OpenAICompatProvider
from app.core.config import get_settings

LIVE = os.environ.get("RUN_LIVE_AI") == "1" and bool(get_settings().OPENAI_COMPAT_API_KEY)

pytestmark = pytest.mark.skipif(not LIVE, reason="RUN_LIVE_AI != 1 (live AI test skipped)")


def test_live_chat_generate():
    p = OpenAICompatProvider()
    out = p.generate("Reply with exactly: OK", max_tokens=5)
    assert "OK" in out


def test_live_embeddings_dim_parity():
    p = OpenAICompatProvider()
    vecs = p.embed(["acids donate protons", "hello world"])
    assert len(vecs) == 2
    assert len(vecs[0]) == get_embedding_config().dim == get_settings().EMBEDDING_DIM


def test_live_generate_structured():
    from pydantic import BaseModel

    from app.agents.teacher.schemas import LessonPlanBody

    p = OpenAICompatProvider()
    plan = p.generate_structured(
        "Make a tiny lesson plan about acids for grade 9.", LessonPlanBody, max_tokens=800
    )
    assert isinstance(plan, LessonPlanBody)
    assert plan.title


def test_live_models_endpoint_reachable():
    import httpx

    s = get_settings()
    r = httpx.get(
        s.OPENAI_COMPAT_BASE_URL.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {s.OPENAI_COMPAT_API_KEY}"},
        timeout=30,
    )
    assert r.status_code == 200
    ids = [m["id"] for m in r.json().get("data", [])]
    assert s.CHAT_MODEL in ids or "gpt-4o-mini" in ids
