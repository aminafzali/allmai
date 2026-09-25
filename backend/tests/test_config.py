"""Config + provider conformance (no Docker, no hard-coded vendors)."""

from pathlib import Path

from app.ai.embedding import get_embedding_config
from app.ai.openai_compat import OpenAICompatProvider
from app.core.config import get_settings

ROOT = Path(__file__).resolve().parents[2]


def test_settings_load():
    s = get_settings()
    assert s.ENV
    assert s.DATABASE_URL.startswith("postgresql")
    assert s.REDIS_URL.startswith("redis")
    assert "localhost:9000" in s.S3_ENDPOINT_URL


def test_gapgpt_default_base_url():
    s = get_settings()
    assert "gapgpt" in s.OPENAI_COMPAT_BASE_URL


def test_embedding_config_matches_models():
    from app.common.base import EmbeddingVector

    emb = get_embedding_config()
    col_type = EmbeddingVector(dim=emb.dim)
    assert col_type.dim == emb.dim == get_settings().EMBEDDING_DIM


def test_openai_compat_urls():
    p = OpenAICompatProvider()
    assert p.chat_url.endswith("/chat/completions")
    assert p.embeddings_url.endswith("/embeddings")


def test_env_example_has_no_docker():
    text = (ROOT / ".env.example").read_text(encoding="utf-8").lower()
    assert "docker" not in text


def test_no_docker_files():
    assert not (ROOT / "docker-compose.yml").exists()
    assert not (ROOT / "Dockerfile").exists()
    assert not (ROOT / "backend" / "Dockerfile").exists()
