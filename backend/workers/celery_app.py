"""Celery app (Redis broker). Workers run as a separate OS process:
``celery -A workers.celery_app.celery worker`` from ``backend/``."""

import socket
from urllib.parse import urlparse

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery = Celery(
    "allmai",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)
celery.conf.update(
    task_track_started=True,
    imports=("workers.tasks",),
    # Phase 2 Document Core: heavy documents are processed one at a time
    # by default; tune via ENV (no hard limits in ingestion code).
    worker_concurrency=settings.DOCUMENT_PROCESSING_CONCURRENCY,
    task_time_limit=settings.DOCUMENT_PROCESSING_TIMEOUT,
)


def broker_available(timeout: float = 1.0) -> bool:
    """Fast TCP pre-check so API uploads never hang when Redis is down."""
    try:
        parts = urlparse(settings.CELERY_BROKER_URL)
        if parts.scheme not in ("redis", "rediss"):
            return True  # unknown scheme: let Celery decide
        sock = socket.create_connection(
            (parts.hostname or "localhost", parts.port or 6379), timeout=timeout
        )
        sock.close()
        return True
    except OSError:
        return False
