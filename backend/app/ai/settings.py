"""Effective AI settings: DB rows (admin panel) override ENV defaults.

Cheap-model defaults (verified live against GapGPT model list):
- chat: gpt-4o-mini (cheap, good Persian)
- embedding: text-embedding-3-small (1536d, matches chunks.embedding)
- audio: gpt-4o-mini-transcribe via AvalAI (live-verified 200 on both
  json/text formats); whisper-1 via GapGPT stays panel-switchable
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
        "model": "gpt-4o-mini-transcribe",
    },
    "agent.teacher_lesson_planner": {"model": "gemini-2.5-flash"},
    "agent.student_academic_coach": {"model": "gemini-2.5-flash"},
    "retrieval.rerank": {"enabled": True, "method": "api", "provider": "avalai",
                         "model": "qwen3-rerank", "timeout_s": 12,
                         "max_chars_per_doc": 3000},
    # Retrieval hardening (P1): single source of truth is
    # candidates_multiplier — per-stream limit = final_k * multiplier.
    # retrieval_top_k declares the contract for the DEFAULT final_k
    # (8*3=24); mismatches are logged loudly, never silently absorbed.
    # min_score=0.0: threshold infrastructure + logging only, drops nothing.
    "retrieval.hybrid": {
        "rrf_k": 60,
        "candidates_multiplier": 3,
        "retrieval_top_k": 24,
        "default_final_k": 8,
        "min_score": 0.0,
        "expand_chars": 2000,
        "query_expansion_enabled": True,
        "ready_only": True,
    },
    # Structured PDF extraction (replaces Docling): Gemini Flash via the
    # SAME OpenAI-compatible endpoint (OPENAI_COMPAT_BASE_URL, i.e. GapGPT)
    # — no GEMINI_API_KEY needed. `model` is the GapGPT-routed Gemini id
    # used by GeminiStructuredParser; the DB row (admin panel) wins over
    # the ENV fallback GEMINI_STRUCT_MODEL.
    "extraction.pdf": {"provider": "openai_compat", "model": "gemini-2.5-flash"},
    # Page-level OCR refill for textless pages (Admin panel only).
    # ocr.provider: "gemini" (DEFAULT: GapGPT-routed, no Google key) |
    # "disabled" (no OCR, degraded flagged).
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


# Providers the agent chat resolver accepts. Anything else falls back to
# the chat.default provider (admin typos must never 500 a chat turn).
_KNOWN_AGENT_PROVIDERS = ("openai_compat", "gemini")

_AGENT_CHAT_KEYS = ("provider", "model", "temperature", "max_tokens")


def resolve_agent_chat(db: Session | None, agent_key: str, definition=None) -> dict:
    """Effective chat config for one agent: layers
    chat.default <- agent.<key> <- definition.model_defaults.

    Returns {provider, model, temperature, max_tokens, sources} where
    sources maps each key to the winning layer (shown in Agent Studio).
    Unknown agent keys (custom keys with no ai_settings row) fall back
    to chat.default; unknown providers fall back to the chat provider.
    """
    base = get_setting(db, "chat.default")
    cfg = {"provider": base.get("provider", "openai_compat"),
           "model": base.get("model"),
           "temperature": base.get("temperature", 0.7),
           "max_tokens": base.get("max_tokens", 1500)}
    sources = {k: "chat.default" for k in _AGENT_CHAT_KEYS}
    try:
        overlay = get_setting(db, f"agent.{agent_key}") or {}
    except KeyError:
        overlay = {}
    if isinstance(overlay, dict):
        for k in _AGENT_CHAT_KEYS:
            if overlay.get(k) is not None:
                cfg[k] = overlay[k]
                sources[k] = f"agent.{agent_key}"
    md = getattr(definition, "model_defaults", None) if definition is not None else None
    if isinstance(md, dict):
        for k in _AGENT_CHAT_KEYS:
            if md.get(k) is not None:
                cfg[k] = md[k]
                sources[k] = "definition.model_defaults"
    if cfg.get("provider") not in _KNOWN_AGENT_PROVIDERS:
        cfg["provider"] = base.get("provider", "openai_compat")
        sources["provider"] = "chat.default (fallback)"
    cfg["sources"] = sources
    return cfg


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
