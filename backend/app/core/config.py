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

    # --- Agent search tools (per-definition toggles in Agent Studio,
    # default OFF; admin enables + provides keys) ---
    # web_search backend: Gemini + Google Search Grounding (direct Google
    # REST, same pattern as GeminiProvider; needs GEMINI_API_KEY).
    WEB_SEARCH_MODEL: str = "gemini-2.5-flash"
    # maps_search backend: Google Maps Platform Places API (New) Text
    # Search. Needs a key with Places API enabled (Google-side billing).
    GOOGLE_MAPS_API_KEY: str = ""

    # --- Search provider chains (swappable backends, see ai/*_providers) ---
    # web_search DEFAULT is GapGPT (Responses API + web_search tool,
    # proven live): same trust domain + billing as every chat call, no new
    # keys. Browser DDG is unreachable from Iranian networks, so the
    # client-side web path is currently dead (kept as selectable impl).
    WEB_SEARCH_PROVIDER: str = "gapgpt"
    WEB_SEARCH_FALLBACKS: str = "ddg"
    WEB_SEARCH_SIDE: str = "server"  # client | server
    GAPGPT_SEARCH_MODEL: str = "gpt-4o-mini"
    # maps_search DEFAULT is browser-first Overpass (no keys, no cost);
    # overpass-server runs only when the browser fails (resume remainder).
    PLACES_PROVIDER: str = "overpass"
    PLACES_FALLBACKS: str = "overpass-server"  # + "google-places" (needs key)

    # --- Embeddings (swappable; DIM must match chunks.embedding) ---
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIM: int = 1536

    # --- Audio (transcription API only; no local models in MVP) ---
    # Transcription model selector (also the ENV fallback under the
    # panel-editable `audio.transcription` ai_setting; the DB row wins).
    # - "gpt-4o-mini-transcribe" (DEFAULT): AvalAI route below.
    # - "whisper-1": GapGPT /audio/transcriptions (kept, panel-switchable).
    # - "gemini-*": Gemini-via-GapGPT chat with audio input.
    WHISPER_API_MODEL: str = "gpt-4o-mini-transcribe"
    # AvalAI OpenAI-compatible gateway (transcription only for now).
    # Docs: https://docs.avalai.ir/fa/ (audio/transcriptions, Bearer key).
    AVALAI_BASE_URL: str = "https://api.avalai.ir/v1"
    AVALAI_API_KEY: str = ""
    # Per-request payload cap of the transcription endpoint (~25MB on both
    # gateways): longer audio is split into pieces (timestamps shifted).
    WHISPER_MAX_BYTES: int = 24 * 1024 * 1024
    # Transcription retries on 429/5xx (honors Retry-After, capped).
    TRANSCRIBE_MAX_RETRIES: int = 3
    TRANSCRIBE_RETRY_CAP_S: float = 20.0

    # --- Video (audio-track transcription only, via imageio-ffmpeg) ---
    # Pre-ingest guards, both tunable without code changes. Oversized or
    # over-long videos are rejected loudly instead of stalling the worker.
    VIDEO_MAX_BYTES: int = 100 * 1024 * 1024
    VIDEO_MAX_DURATION_S: int = 1200  # 20 minutes
    # Audio extraction shape: mono 16kHz mp3 (small, whisper-friendly).
    VIDEO_AUDIO_BITRATE: str = "32k"

    # --- Excel (structured sheets via DuckDB, see knowledge/excel/) ---
    # Hard row cap per sheet: ingesting more is rejected loudly (memory bound).
    EXCEL_MAX_ROWS_PER_SHEET: int = 200_000
    EXCEL_MAX_SHEETS: int = 50
    # Query guardrails for excel_query (agent tool).
    EXCEL_QUERY_MAX_ROWS: int = 200
    EXCEL_QUERY_TIMEOUT_S: int = 15

    # --- Ingestion ---
    # Structured PDF extraction route: Gemini Flash via GapGPT
    # (GeminiStructuredParser). Docling/RAG-Anything were removed from the
    # extraction path entirely; the flat pypdf fallback stays as the loud
    # safety net when Gemini is unreachable.
    USE_GEMINI_PDF: bool = True
    # GapGPT-routed model id for structured PDF extraction (overridden by
    # the `extraction.pdf` ai_setting row when the admin sets one).
    GEMINI_STRUCT_MODEL: str = "gemini-2.5-flash"
    # How many text-rich pages go into ONE structuring call (cost/latency
    # knob: a 200-page book needs ~40 calls at 5 pages each).
    GEMINI_STRUCT_PAGES_PER_CALL: int = 5
    # Per-call timeout for structuring/vision calls (seconds).
    GEMINI_STRUCT_TIMEOUT_S: float = 120.0
    # Loud guard: PDFs with more pages are rejected instead of burning
    # unbounded model budget (tunable, never silent truncation).
    GEMINI_STRUCT_MAX_PAGES: int = 300
    # Stuck-task watchdog: processing/pending rows older than this (minutes)
    # are marked failed with a retry hint on list reads (no cron needed).
    INGEST_STALE_MINUTES: int = 30
    # Liveness heartbeat during long parses (seconds): the worker refreshes
    # processing_started_at on its own session so slow-but-alive runs are
    # distinguishable from dead workers. 0 disables.
    INGEST_HEARTBEAT_S: int = 120
    # Orphan reset at worker start (seconds): processing rows older than
    # this are presumed ownerless (no live task can legally run longer
    # than DOCUMENT_PROCESSING_TIMEOUT + slack) and go back to pending.
    INGEST_ORPHAN_S: int = 2400

    # --- Document extraction (Docling/RAG-Anything removed) ---
    # Worker shape for heavy documents: one at a time by default.
    # No hard limits in code; Celery reads these (see workers/celery_app.py).
    DOCUMENT_PROCESSING_CONCURRENCY: int = 1
    DOCUMENT_PROCESSING_TIMEOUT: int = 1800
    # Page classifier: average extractable chars per page below this
    # threshold means "no usable text layer" -> the page goes through
    # Gemini vision instead of text structuring (configurable, not hardcoded).
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
