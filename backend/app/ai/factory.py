"""Provider factory: swap GapGPT/OpenAI <-> Gemini via config, not code.

Resolution order for any purpose (chat/embedding/audio/agent):
ai_settings row `provider` field -> ENV AI_PROVIDER -> openai_compat.
"""

from app.ai.gemini import GeminiProvider
from app.ai.openai_compat import OpenAICompatProvider
from app.ai.settings import list_settings
from app.core.config import get_settings

_PROVIDERS = {"openai_compat": OpenAICompatProvider, "gemini": GeminiProvider}


def get_provider(name: str | None = None):
    name = (name or get_settings().AI_PROVIDER or "openai_compat").lower()
    try:
        return _PROVIDERS[name]()
    except KeyError:
        raise ValueError(f"unknown AI provider: {name!r}") from None


def resolve_provider_name(db, setting_key: str, default: str = "openai_compat") -> str:
    """Read the provider name for a setting key (DB row wins over ENV)."""
    try:
        merged = list_settings(db)
        return str((merged.get(setting_key) or {}).get("provider") or default)
    except Exception:
        return default


def get_chat_provider(db=None):
    s = get_settings()
    name = resolve_provider_name(db, "chat.default", s.AI_PROVIDER)
    return get_provider(name)


def get_embedding_provider(db=None):
    s = get_settings()
    name = resolve_provider_name(db, "embedding.default", s.AI_PROVIDER)
    return get_provider(name)
