"""Celery app (Redis broker). Workers run as a separate OS process:
``celery -A workers.celery_app.celery worker`` from ``backend/``."""

import socket
from urllib.parse import urlparse

from celery import Celery
from celery.signals import worker_ready

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
    # Stuck-task safety: a task is acked only AFTER it finishes, and a
    # task lost to a dead worker goes back to the queue (max_retries in
    # workers/tasks.py bounds the redelivery). Solo pool + prefetch 1:
    # one document at a time, in order.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
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


@worker_ready.connect
def _reset_orphans_on_start(**kwargs) -> None:
    """A (re)started worker adopts ownerless rows: nothing it finds here
    can belong to a live task (this worker just booted), so anything
    older than INGEST_ORPHAN_S goes back to pending + requeued. Loud."""
    import logging as _logging

    try:
        from workers.tasks import reset_orphaned_ingests

        out = reset_orphaned_ingests()
        _logging.getLogger(__name__).warning(
            "worker orphan reset on start: %s", out)
    except Exception as exc:  # startup path must never kill the worker
        _logging.getLogger(__name__).warning(
            "worker orphan reset failed: %r", exc)
