"""Central application settings.

Everything (endpoints, keys, model names, dimensions) comes from ENV.
See root ``.env.example``. No Docker-specific values anywhere.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root `.env` (two levels above backend/, one above this file's package root).
# backend/app/core/config.py -> parents[0]=core, [1]=app, [2]=backend, [3]=repo root
_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ROOT_ENV), extra="ignore")

    APP_NAME: str = "AllMai AI Platform"
    ENV: str = "dev"

    # --- Data / infra (direct installs, no Docker) ---
    DATABASE_URL: str = "postgresql+psycopg2://allmai:allmai@localhost:5432/allmai"
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    S3_ENDPOINT_URL: str = "http://localhost:9000"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET: str = "allmai"
    S3_REGION: str = "us-east-1"
    S3_SECURE: bool = False

    # File storage backend: "local" (MVP, server disk) | "s3" (later)
    STORAGE_BACKEND: str = "local"
    STORAGE_LOCAL_DIR: str = "./data/storage"

    # --- Auth ---
    JWT_SECRET: str = "change-me-to-a-long-random-secret"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_MINUTES: int = 15
    REFRESH_TOKEN_DAYS: int = 30

    # --- AI providers (server-side keys only) ---
    AI_PROVIDER: str = "openai_compat"  # openai_compat | gemini
    OPENAI_COMPAT_BASE_URL: str = "https://api.gapgpt.app/v1"
    OPENAI_COMPAT_API_KEY: str = ""
    CHAT_MODEL: str = "gpt-4o-mini"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.0-flash"

    # --- Embeddings (swappable; DIM must match chunks.embedding) ---
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIM: int = 1536

    # --- Audio (transcription API only; no local models in MVP) ---
    WHISPER_API_MODEL: str = "whisper-1"

    # --- Ingestion ---
    USE_RAGANYTHING: bool = False

    # --- Phase 2 Document Core ---
    # USE_DOCLING: route PDFs through the RAG-Anything parser subsystem
    # (get_parser("docling"), parse-only). Default off: pypdf fallback stays.
    USE_DOCLING: bool = False
    # Worker shape for heavy documents: one at a time by default.
    # No hard limits in code; Celery reads these (see workers/celery_app.py).
    DOCUMENT_PROCESSING_CONCURRENCY: int = 1
    DOCUMENT_PROCESSING_TIMEOUT: int = 1800
    # Comma-separated OCR languages for the Persian-OCR refill pass
    # (EasyOCR codes; 'fa' is mandatory — see spike report).
    DOC_OCR_LANGS: str = "fa"
    # Scanned-PDF early detect: average extractable chars per page below
    # this threshold means "no usable text layer" (configurable, not hardcoded).
    SCAN_TEXT_THRESHOLD: int = 20
    # --- Phase 3 Vision (D): triage thresholds, all configurable ---
    DOCUMENT_VISION_ENABLED: bool = False
    # Skip vision when the caption is already data-carrying (>= chars AND a digit).
    VISION_CAPTION_MIN_CHARS: int = 20
    # Skip vision when the figure's page is already text-rich (provisional).
    VISION_PAGE_TEXT_MIN_CHARS: int = 200
    # Max figures described per source (worst-case added latency bound).
    DOC_VISION_MAX_FIGURES: int = 5
    # Fallback figure extraction (no docling): embedded PDF images smaller
    # than this (either side, px) are treated as icons and skipped.
    VISION_MIN_IMAGE_PX: int = 120

    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000,http://127.0.0.1:3001"


@lru_cache
def get_settings() -> Settings:
    return Settings()
