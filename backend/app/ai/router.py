from fastapi import APIRouter

from app.ai.embedding import get_embedding_config
from app.core.config import get_settings

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/providers")
def list_providers() -> dict:
    """Non-secret provider info (never expose API keys)."""
    s = get_settings()
    emb = get_embedding_config()
    return {
        "active": s.AI_PROVIDER,
        "chat_model": s.CHAT_MODEL,
        "gemini_model": s.GEMINI_MODEL,
        "embedding": {"model": emb.model, "dim": emb.dim},
    }
