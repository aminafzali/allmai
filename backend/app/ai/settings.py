"""Effective AI settings: DB rows (admin panel) override ENV defaults.

Cheap-model defaults (verified live against GapGPT model list):
- chat: gpt-4o-mini (cheap, good Persian)
- embedding: text-embedding-3-small (1536d, matches chunks.embedding)
- audio: whisper-1 (+ gapgpt/whisper-1 alias) via Transcription API only
Cheaper alternates the admin can switch to later: gpt-4.1-nano,
gemini-2.5-flash-lite, deepseek-v4.1-flash.
"""

from sqlalchemy.orm import Session

from app.admin.models import AISetting
from app.core.config import get_settings

# Resolution order per key: ai_settings row -> this default (mirrors ENV).
DEFAULT_AI_SETTINGS: dict[str, dict] = {
    "chat.default": {
        "provider": "openai_compat",
        "model": "gpt-4o-mini",
        "temperature": 0.7,
        "max_tokens": 1500,
    },
    "embedding.default": {
        "provider": "openai_compat",
        "model": "text-embedding-3-small",
        "dim": 1536,
    },
    "audio.transcription": {
        "provider": "openai_compat",
        "model": "whisper-1",
    },
    "agent.teacher_lesson_planner": {"model": "gpt-4o-mini"},
    "agent.student_academic_coach": {"model": "gpt-4o-mini"},
    "retrieval.rerank": {"enabled": True, "method": "heuristic"},
    # Phase 2 top-up: system-level OCR/Vision selection (Admin panel only).
    # ocr.provider: "gemini" (DEFAULT: spike-proven on Persian) | "easyocr"
    # (offline fallback) | "disabled" (no OCR, degraded flagged).
    # NOTE (GapGPT setup): "gemini" does NOT call Google directly — it
    # rasterizes pages and transcribes them through the SAME OpenAI-
    # compatible endpoint (OPENAI_COMPAT_BASE_URL, i.e. GapGPT) using
    # the `model` below. No GEMINI_API_KEY is needed for this path.
    "ocr.provider": {"provider": "gemini", "model": "gemini-2.5-flash-lite"},
    # vision.provider: Phase 3 (figure -> description), also routed via
    # GapGPT/OpenAI-compatible describe_image (no Google key). `model` is
    # honored by the worker; the on/off switch stays DOCUMENT_VISION_ENABLED.
    "vision.provider": {"provider": "openai_compat",
                        "model": "gemini-2.5-flash-lite", "enabled": False},
}


def _env_default(key: str) -> dict:
    s = get_settings()
    if key == "chat.default":
        return {"provider": s.AI_PROVIDER, "model": s.CHAT_MODEL}
    if key == "embedding.default":
        return {"provider": s.AI_PROVIDER, "model": s.EMBEDDING_MODEL, "dim": s.EMBEDDING_DIM}
    return dict(DEFAULT_AI_SETTINGS[key])


def list_settings(db: Session | None) -> dict[str, dict]:
    """Merged view: defaults under DB rows. Works without a DB too."""
    merged = {k: dict(v) for k, v in DEFAULT_AI_SETTINGS.items()}
    if db is None:
        return merged
    try:
        for row in db.query(AISetting).all():
            if row.key in merged:
                merged[row.key] = {**merged[row.key], **(row.value or {})}
            else:
                merged[row.key] = dict(row.value or {})
    except Exception:
        pass  # table missing (e.g. pre-migration): ENV defaults stand
    return merged


def get_setting(db: Session | None, key: str) -> dict:
    if key not in DEFAULT_AI_SETTINGS:
        raise KeyError(f"unknown AI setting: {key!r}")
    merged = list_settings(db)
    env = _env_default(key)
    return {**env, **merged[key]}


def upsert_setting(db: Session, key: str, value: dict) -> dict:
    if key not in DEFAULT_AI_SETTINGS:
        raise KeyError(f"unknown AI setting: {key!r}")
    if not isinstance(value, dict):
        raise ValueError("value must be an object")
    row = db.query(AISetting).filter(AISetting.key == key).first()
    if row is None:
        row = AISetting(key=key, value=dict(DEFAULT_AI_SETTINGS[key]))
        db.add(row)
    row.value = {**(row.value or {}), **value}
    db.commit()
    db.refresh(row)
    return dict(row.value)
