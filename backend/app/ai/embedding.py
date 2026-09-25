"""Embedding abstraction: model + dimension come from configuration so the
embedding model can be swapped later (requires re-index migration)."""

from dataclasses import dataclass

from app.core.config import get_settings


@dataclass(frozen=True)
class EmbeddingConfig:
    model: str
    dim: int


def get_embedding_config() -> EmbeddingConfig:
    s = get_settings()
    return EmbeddingConfig(model=s.EMBEDDING_MODEL, dim=s.EMBEDDING_DIM)
