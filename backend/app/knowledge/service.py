"""Knowledge service: KB CRUD + source intake (file upload / url / note).

Everything is workspace-scoped by construction (workspace comes from
`resolve_workspace`). Raw bytes ALWAYS land in object storage —
even notes/urls (stored as small descriptor objects) — so PostgreSQL
keeps metadata only. Parsing/embedding happens in the Celery worker.
"""

import json
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth.service import audit
from app.common.base import coerce_uuid, utcnow
from app.knowledge.models import KnowledgeBase, Source
from app.knowledge.parsers.base import UnsupportedSourceError, validate_source_type
from app.storage.validation import storage_key, validate_upload
from app.users.models import User
from app.workspaces.models import Workspace


def _checked_type(source_type: str, filename: str = "") -> str:
    try:
        return validate_source_type(source_type, filename)
    except UnsupportedSourceError as exc:
        raise HTTPException(422, str(exc))


def create_kb(db: Session, ws: Workspace, user: User, title: str, description: str = "") -> KnowledgeBase:
    title = title.strip()
    if not title:
        raise HTTPException(422, "title is required")
    kb = KnowledgeBase(workspace_id=ws.id, title=title[:300], description=description,
                       scope="workspace")
    db.add(kb)
    db.commit()
    db.refresh(kb)
    audit(db, "knowledge.kb_create", actor_user_id=user.id, workspace_id=ws.id,
          entity="knowledge_base", entity_id=kb.id)
    return kb


def create_global_kb(db: Session, user: User, title: str,
                     description: str = "") -> KnowledgeBase:
    """Admin-only: a global KB (workspace_id NULL, scope='global')."""
    title = title.strip()
    if not title:
        raise HTTPException(422, "title is required")
    kb = KnowledgeBase(workspace_id=None, title=title[:300],
                       description=description, scope="global")
    db.add(kb)
    db.commit()
    db.refresh(kb)
    audit(db, "knowledge.global_kb_create", actor_user_id=user.id,
          entity="knowledge_base", entity_id=kb.id)
    return kb


def list_global_kbs(db: Session) -> list[KnowledgeBase]:
    return (
        db.query(KnowledgeBase)
        .filter(KnowledgeBase.scope == "global")
        .order_by(KnowledgeBase.created_at.desc())
        .all()
    )


def list_kbs(db: Session, ws: Workspace) -> list[KnowledgeBase]:
    return (
        db.query(KnowledgeBase)
        .filter(KnowledgeBase.workspace_id == ws.id)
        .order_by(KnowledgeBase.created_at.desc())
        .all()
    )


def get_kb(db: Session, ws: Workspace, kb_id) -> KnowledgeBase:
    kb = (
        db.query(KnowledgeBase)
        .filter(KnowledgeBase.id == coerce_uuid(kb_id), KnowledgeBase.workspace_id == ws.id)
        .first()
    )
    if kb is None:
        raise HTTPException(404, "knowledge base not found")
    return kb


def _new_source(db: Session, ws: Workspace, kb: KnowledgeBase, type: str) -> Source:
    src = Source(id=uuid4(), workspace_id=ws.id, kb_id=kb.id, type=type, status="pending",
                 processing_started_at=utcnow())
    db.add(src)
    db.flush()
    return src


def enqueue_ingest(source_id: UUID, workspace_id) -> bool:
    """Queue the worker with the tenant context attached (the worker sets
    RLS context from it before touching any row). Global sources pass
    workspace_id=None (worker binds transient admin context). Returns False
    (leaves status pending) if the broker is unreachable instead of failing —
    or hanging — the upload."""
    try:
        from workers.celery_app import broker_available
        from workers.tasks import ingest_source

        if not broker_available():
            return False
        ingest_source.delay(str(source_id),
                            str(workspace_id) if workspace_id is not None else None)
        return True
    except Exception:
        return False


def create_file_source(
    db: Session, ws: Workspace, kb: KnowledgeBase, user: User,
    source_type: str, filename: str, blob: bytes, storage,
) -> Source:
    _checked_type(source_type, filename)
    mime = validate_upload(source_type, filename, blob)
    src = _new_source(db, ws, kb, source_type)
    key = storage_key(ws.id, kb.id, src.id, filename)
    storage.put(key, blob, content_type=mime)
    src.storage_key = key
    src.filename = filename[:512]
    src.mime = mime
    src.size_bytes = len(blob)
    if enqueue_ingest(src.id, ws.id):
        _mark_processing(src)
    db.commit()
    db.refresh(src)
    audit(db, "knowledge.source_upload", actor_user_id=user.id, workspace_id=ws.id,
          entity="source", entity_id=src.id,
          meta={"type": source_type, "bytes": len(blob), "status": src.status})
    return src


def create_meeting_source(
    db: Session, ws: Workspace, kb: KnowledgeBase, user: User,
    title: str, text: str, audio_filename: str, audio_blob: bytes | None,
    storage, embed_fn=None,
) -> Source:
    """Create and index a meeting ONLY on explicit user save.

    The recorded audio (when present) is stored as the source's original
    blob; the approved live transcript + notes are chunked/embedded
    directly into the SAME source — never re-transcribed by a second STT
    pass. No source/RAG rows exist while the session is an unsaved draft.
    """
    title = (title or "").strip()
    text = (text or "").strip()
    if not title:
        raise HTTPException(422, "title is required")
    if not text:
        raise HTTPException(422, "meeting transcript is empty")
    if len(text) > MANUAL_TEXT_MAX_CHARS:
        raise HTTPException(422, f"text too long (max {MANUAL_TEXT_MAX_CHARS} chars)")

    blob = audio_blob or b""
    if blob:
        source_type = "video"  # browser MediaRecorder container: webm/mp4
        filename = (audio_filename or "meeting.webm").strip()
        mime = validate_upload(source_type, filename, blob)
    else:
        source_type = "note"
        filename = f"{title[:450]}.md"
        mime = "text/markdown"
    src = _new_source(db, ws, kb, source_type)
    key = storage_key(ws.id, kb.id, src.id, filename)
    storage.put(key, blob if blob else text.encode("utf-8"), content_type=mime)
    src.storage_key = key
    src.filename = filename[:512]
    src.mime = mime
    src.size_bytes = len(blob) if blob else len(text.encode("utf-8"))
    src.parse_meta = {**dict(src.parse_meta or {}), "meeting_session": True,
                      "meeting_title": title}
    db.flush()

    indexed = replace_source_text(
        db, ws, kb, user, src.id, text, storage, embed_fn=embed_fn,
    )
    db.refresh(src)
    audit(db, "knowledge.meeting_save", actor_user_id=user.id,
          workspace_id=ws.id, entity="source", entity_id=src.id,
          meta={"title": title, "has_audio": bool(blob),
                "chars": len(text), "chunks": indexed.get("chunks", 0)})
    return src


def create_global_file_source(
    db: Session, kb: KnowledgeBase, user: User,
    source_type: str, filename: str, blob: bytes, storage,
) -> Source:
    """Admin-only intake into a GLOBAL KB (workspace_id NULL).

    The worker processes it under a transient admin context; retrieval
    stays gated by agent_definition_knowledge assignment (Phase 1 RLS).
    """
    from app.common.base import coerce_uuid

    if kb.workspace_id is not None or getattr(kb, "scope", "") != "global":
        raise HTTPException(422, "knowledge base is not global")
    _checked_type(source_type, filename)
    mime = validate_upload(source_type, filename, blob)
    src = Source(id=uuid4(), workspace_id=None, kb_id=coerce_uuid(kb.id),
                 type=source_type, status="pending",
                 processing_started_at=utcnow())
    db.add(src)
    db.flush()
    from app.storage.validation import safe_filename

    key = f"global/kb/{kb.id}/sources/{src.id}/{safe_filename(filename)}"
    storage.put(key, blob, content_type=mime)
    src.storage_key = key
    src.filename = filename[:512]
    src.mime = mime
    src.size_bytes = len(blob)
    if enqueue_ingest(src.id, None):
        _mark_processing(src)
    db.commit()
    db.refresh(src)
    audit(db, "knowledge.global_source_upload", actor_user_id=user.id,
          entity="source", entity_id=src.id,
          meta={"type": source_type, "bytes": len(blob), "status": src.status})
    return src


def create_global_link_source(
    db: Session, kb: KnowledgeBase, user: User,
    source_type: str, title: str, payload: dict, storage,
) -> Source:
    """Admin-only URL/note intake into a GLOBAL KB (workspace_id NULL).

    Mirrors create_link_source: descriptor stored as an object, worker
    ingests under a transient admin context. Retrieval stays gated by
    definition assignment (Phase 1 RLS).
    """
    from uuid import uuid4

    from app.auth.service import audit

    if kb.workspace_id is not None or getattr(kb, "scope", "") != "global":
        raise HTTPException(422, "knowledge base is not global")
    _checked_type(source_type)
    if source_type not in ("url", "note"):
        raise HTTPException(422, "use file upload for binary source types")
    if source_type == "url" and not (payload.get("url") or "").strip():
        raise HTTPException(422, "url is required")
    if source_type == "note" and not (payload.get("content") or "").strip():
        raise HTTPException(422, "content is required")
    src = Source(id=uuid4(), workspace_id=None, kb_id=coerce_uuid(kb.id),
                 type=source_type, status="pending",
                 processing_started_at=utcnow())
    db.add(src)
    db.flush()
    descriptor = json.dumps(
        {"title": title, **payload}, ensure_ascii=False
    ).encode("utf-8")
    if len(descriptor) > 5 * 1024 * 1024:
        raise HTTPException(413, "note too large (max 5 MB)")
    key = f"global/kb/{kb.id}/sources/{src.id}/descriptor.json"
    storage.put(key, descriptor, content_type="application/json")
    src.storage_key = key
    src.filename = (title or source_type)[:512]
    src.mime = "application/json" if source_type == "url" else "text/markdown"
    src.size_bytes = len(descriptor)
    if enqueue_ingest(src.id, None):
        _mark_processing(src)
    db.commit()
    db.refresh(src)
    audit(db, "knowledge.global_source_create", actor_user_id=user.id,
          entity="source", entity_id=src.id,
          meta={"type": source_type, "status": src.status})
    return src


def create_link_source(
    db: Session, ws: Workspace, kb: KnowledgeBase, user: User,
    source_type: str, title: str, payload: dict, storage,
) -> Source:
    """URL / note intake. Payload descriptor is stored as an object too."""
    _checked_type(source_type)
    if source_type not in ("url", "note"):
        raise HTTPException(422, "use file upload for binary source types")
    if source_type == "url" and not (payload.get("url") or "").strip():
        raise HTTPException(422, "url is required")
    if source_type == "note" and not (payload.get("content") or "").strip():
        raise HTTPException(422, "content is required")
    src = _new_source(db, ws, kb, source_type)
    descriptor = json.dumps(
        {"title": title, **payload}, ensure_ascii=False
    ).encode("utf-8")
    if len(descriptor) > 5 * 1024 * 1024:
        raise HTTPException(413, "note too large (max 5 MB)")
    key = storage_key(ws.id, kb.id, src.id, "descriptor.json")
    storage.put(key, descriptor, content_type="application/json")
    src.storage_key = key
    src.filename = (title or source_type)[:512]
    src.mime = "application/json" if source_type == "url" else "text/markdown"
    src.size_bytes = len(descriptor)
    if enqueue_ingest(src.id, ws.id):
        _mark_processing(src)
    db.commit()
    db.refresh(src)
    audit(db, "knowledge.source_create", actor_user_id=user.id, workspace_id=ws.id,
          entity="source", entity_id=src.id,
          meta={"type": source_type, "status": src.status})
    return src


def list_sources(db: Session, ws: Workspace, kb: KnowledgeBase) -> list[Source]:
    mark_stale_ingests(db, ws.id)
    return (
        db.query(Source)
        .filter(Source.kb_id == kb.id, Source.workspace_id == ws.id)
        .order_by(Source.created_at.desc())
        .all()
    )


def _mark_processing(src: Source) -> None:
    """Heartbeat: task queued/started (watchdog judges by this clock)."""
    src.status = "processing"
    src.processing_started_at = utcnow()


def _clear_processing(src: Source, status: str, error: str = "") -> None:
    src.status = status
    src.error = error
    src.processing_started_at = None


def mark_stale_ingests(db: Session, workspace_id=None,
                       timeout_min: int | None = None) -> int:
    """Lazy watchdog (no cron): rows stuck in processing/pending beyond
    the threshold become failed with a clear Persian message + retry hint.
    workspace_id None = global rows only. Returns the marked count."""
    from datetime import timedelta

    from app.core.config import get_settings

    timeout = timeout_min if timeout_min is not None else int(
        get_settings().INGEST_STALE_MINUTES)
    cutoff = utcnow() - timedelta(minutes=max(1, timeout))
    q = db.query(Source).filter(Source.status.in_(("processing", "pending")))
    if workspace_id is not None:
        q = q.filter(Source.workspace_id == coerce_uuid(workspace_id))
    else:
        q = q.filter(Source.workspace_id.is_(None))
    n = 0
    for src in q.all():
        started = src.processing_started_at or src.created_at
        if started is not None and started.tzinfo is None:
            from datetime import timezone

            started = started.replace(tzinfo=timezone.utc)
        if started is not None and started > cutoff:
            continue
        src.status = "failed"
        src.error = ("پردازش ناتمام ماند (احتمالاً وقفه ورکر). "
                     "«استخراج مجدد» را بزنید.")
        src.processing_started_at = None
        n += 1
    if n:
        db.commit()
    return n


def retry_source(db: Session, ws: Workspace, user: User, source_id) -> Source:
    """Re-queue a pending/failed/processing-stuck source for ingestion."""
    from app.auth.service import audit

    src = get_source(db, ws, source_id)
    src.status = "pending"
    src.error = ""
    src.processing_started_at = utcnow()
    db.commit()
    if enqueue_ingest(src.id, ws.id):
        _mark_processing(src)
        db.commit()
    db.refresh(src)
    audit(db, "knowledge.source_retry", actor_user_id=user.id, workspace_id=ws.id,
          entity="source", entity_id=src.id, meta={"status": src.status})
    return src


def _purge_source(db: Session, src: Source, storage) -> dict:
    """Delete EVERYTHING a source learned: ingest tree (documents/segments/
    chunks incl. embeddings + FTS), figure/duckdb blobs, and the source blob
    itself. Shared by workspace + global delete. An in-flight worker task
    later no-ops (run_ingest returns 'source not found')."""
    from workers.tasks import _cleanup_previous_ingest

    removed = _cleanup_previous_ingest(db, storage, src)
    for key in {src.storage_key or ""}:
        if not key:
            continue
        try:
            storage.delete(key)
            removed["blobs"] += 1
        except Exception:
            pass
    return removed


def delete_source(db: Session, ws: Workspace, kb: KnowledgeBase, user: User,
                  source_id, storage) -> dict:
    """Delete a workspace source + its full RAG footprint + storage blobs."""
    src = get_source(db, ws, source_id)
    if str(src.kb_id) != str(kb.id):
        raise HTTPException(404, "source not found")
    removed = _purge_source(db, src, storage)
    name = src.filename or str(src.id)
    db.delete(src)
    db.commit()
    audit(db, "knowledge.source_delete", actor_user_id=user.id, workspace_id=ws.id,
          entity="source", entity_id=source_id, meta={"filename": name, **removed})
    return {"deleted": str(src.id), "filename": name, **removed}


def delete_global_source(db: Session, kb: KnowledgeBase, admin: User,
                         source_id, storage) -> dict:
    """Delete a GLOBAL source + its full RAG footprint + storage blobs.

    Admin-gated at the router (is_admin RLS context); the row must belong
    to this global KB (workspace_id NULL enforced).
    """
    from app.common.base import coerce_uuid as _coerce

    if getattr(kb, "scope", "") != "global" or kb.workspace_id is not None:
        raise HTTPException(422, "knowledge base is not global")
    src = (
        db.query(Source)
        .filter(Source.id == _coerce(source_id), Source.kb_id == _coerce(kb.id),
                Source.workspace_id.is_(None))
        .first()
    )
    if src is None:
        raise HTTPException(404, "source not found")
    removed = _purge_source(db, src, storage)
    name = src.filename or str(src.id)
    db.delete(src)
    db.commit()
    audit(db, "knowledge.global_source_delete", actor_user_id=admin.id,
          entity="source", entity_id=source_id, meta={"filename": name, **removed})
    return {"deleted": str(src.id), "filename": name, **removed}


def get_source(db: Session, ws: Workspace, source_id) -> Source:
    src = (
        db.query(Source)
        .filter(Source.id == coerce_uuid(source_id), Source.workspace_id == ws.id)
        .first()
    )
    if src is None:
        raise HTTPException(404, "source not found")
    return src


def processing_stages(src: Source) -> list[dict]:
    """Single source of truth for the per-document pipeline display.

    Stages: Upload -> Parsing -> OCR -> Vision -> Chunking -> Ready,
    with Degraded / Failed overlays. The admin UX renders exactly this.
    """
    meta = dict(src.parse_meta or {})
    stages = [{"key": "upload", "label": "Upload", "state": "done"}]
    if src.status in ("pending",):
        stages.append({"key": "parsing", "label": "Parsing", "state": "waiting"})
        return stages
    parser = meta.get("parser")
    stages.append({"key": "parsing", "label": "Parsing", "state": "done",
                   "detail": str(parser or "fallback")})
    ocr_empty = list(meta.get("ocr_empty_pages") or [])
    ocr_failed = bool(meta.get("ocr_refill_failed"))
    if meta.get("ocr_degraded") or ocr_empty or ocr_failed:
        stages.append({"key": "ocr", "label": "OCR", "state": "degraded",
                       "detail": f"empty pages: {ocr_empty}" if ocr_empty
                       else str(meta.get("ocr_reason", meta.get("ocr_refill_failed", "")))[:200]})
    elif meta.get("ocr_refilled_pages") or "ocr" in str(parser or ""):
        stages.append({"key": "ocr", "label": "OCR", "state": "done",
                       "detail": f"refilled pages: {list(meta.get('ocr_refilled_pages') or [])}"})
    else:
        stages.append({"key": "ocr", "label": "OCR", "state": "skipped",
                       "detail": "no OCR needed"})
    vision = meta.get("vision") or {}
    if vision.get("failed"):
        stages.append({"key": "vision", "label": "Vision", "state": "degraded",
                       "detail": f"processed={vision.get('processed', 0)} "
                                 f"failed={vision.get('failed')}"})
    elif vision.get("processed"):
        stages.append({"key": "vision", "label": "Vision", "state": "done",
                       "detail": f"processed={vision.get('processed')} "
                                 f"skipped={vision.get('skipped', 0)}"})
    else:
        stages.append({"key": "vision", "label": "Vision", "state": "skipped",
                       "detail": "disabled or no figures"})
    chunks = meta.get("chunks")
    stages.append({"key": "chunking", "label": "Chunking", "state": "done",
                   "detail": f"chunks={chunks}" if chunks is not None else ""})
    if src.status == "failed":
        stages.append({"key": "result", "label": "Failed", "state": "failed",
                       "detail": (src.error or "")[:300]})
    elif meta.get("ocr_degraded") or vision.get("failed"):
        stages.append({"key": "result", "label": "Degraded", "state": "degraded",
                       "detail": "ready with warnings (see OCR/Vision)"})
    else:
        stages.append({"key": "result", "label": "Ready", "state": "done"})
    return stages


def source_processing_detail(db: Session, src: Source) -> dict:
    """Source + derived stages + figure chunks (workspace-agnostic core;
    callers enforce workspace/admin gating before calling)."""
    from app.knowledge.models import Chunk, Document, PageSegment

    doc_ids = [d.id for d in db.query(Document).filter(Document.source_id == src.id).all()]
    figures: list[dict] = []
    chunk_count = 0
    if doc_ids:
        seg_ids = [s.id for s in db.query(PageSegment).filter(
            PageSegment.document_id.in_(doc_ids)).all()]
        seg_page = {s.id: s.page_no for s in db.query(PageSegment).filter(
            PageSegment.id.in_(seg_ids)).all()} if seg_ids else {}
        chunks = db.query(Chunk).filter(Chunk.segment_id.in_(seg_ids)).all() \
            if seg_ids else []
        chunk_count = len(chunks)
        for c in chunks:
            meta = dict(c.chunk_metadata or {})
            if meta.get("kind") not in ("image", "figure"):
                continue
            status = "described" if meta.get("vision_description") else "placeholder"
            if meta.get("vision_failed"):
                status = "failed"
            elif meta.get("vision_skipped"):
                status = f"skipped:{meta['vision_skipped']}"
            figures.append({
                "chunk_id": str(c.id),
                "element_id": str(meta.get("element_id", "")),
                "page_no": seg_page.get(c.segment_id),
                "caption": str(meta.get("caption", "") or ""),
                "vision_status": status,
                "vision_uncertain": bool(meta.get("vision_uncertain", False)),
                "storage_key": str(meta.get("storage_key", "") or ""),
                "excerpt": (c.content or "")[:300],
            })
    return {
        "source_id": str(src.id),
        "filename": src.filename,
        "type": src.type,
        "status": src.status,
        "error": src.error,
        "parse_meta": dict(src.parse_meta or {}),
        "stages": processing_stages(src),
        "chunks": chunk_count,
        "figures": figures,
    }


def source_transcript(db: Session, src: Source) -> dict:
    """Full extracted text of a source, assembled from segments.

    Powers transcript view/download (audio transcription included):
    segments ordered by document, page, then audio timestamp. For
    timestamped sources (audio/video) the same deterministic chapterize()
    used at ingest derives سرفصل on the fly — no schema change, no
    diarization (titles + time ranges only). Workspace-agnostic core;
    callers enforce gating before calling.
    """
    from app.knowledge.models import Document, PageSegment

    docs = (db.query(Document).filter(Document.source_id == src.id)
            .order_by(Document.created_at).all())
    segments: list[dict] = []
    for doc in docs:
        segs = (db.query(PageSegment)
                .filter(PageSegment.document_id == doc.id).all())
        segs.sort(key=lambda s: (s.page_no or 0, s.start_ms or 0))
        for s in segs:
            text = (s.text or "").strip()
            if not text:
                continue
            segments.append({"page_no": s.page_no,
                             "start_ms": s.start_ms, "end_ms": s.end_ms,
                             "text": text})
    chapters: list[dict] = []
    if src.type in ("audio", "video") and any(
            s["start_ms"] is not None and s["end_ms"] is not None
            for s in segments):
        from app.knowledge.parsers.audio import chapterize

        chapters = chapterize([(s["start_ms"] or 0, s["end_ms"] or 0,
                                s["text"]) for s in segments])
    return {
        "source_id": str(src.id),
        "filename": src.filename,
        "type": src.type,
        "status": src.status,
        "segments": segments,
        "chapters": chapters,
        "text": "\n\n".join(s["text"] for s in segments),
    }


MANUAL_TEXT_MAX_CHARS = 200_000


def replace_source_text(db: Session, ws: Workspace, kb: KnowledgeBase,
                        user: User, source_id, text: str, storage,
                        embed_fn=None) -> dict:
    """Replace a source's extracted text (manual edit) and re-index it.

    The old ingest tree (documents/segments/chunks incl. embeddings) is
    purged, then the new text is chunked/embedded/persisted through the
    same pipeline shape as the worker (normalized content, tsv on PG).
    The original blob stays untouched (original download keeps working);
    a later retry/re-ingest overwrites this edit (recorded in parse_meta).
    """
    from app.knowledge.chunking import chunk_parsed
    from app.knowledge.models import Chunk, Document, PageSegment
    from app.knowledge.normalize import normalize_fa
    from app.knowledge.parsers.base import ParsedDocument, ParsedPage
    from workers.tasks import _cleanup_previous_ingest

    src = get_source(db, ws, source_id)
    if str(src.kb_id) != str(kb.id):
        raise HTTPException(404, "source not found")
    text = (text or "").strip()
    if not text:
        raise HTTPException(422, "text is empty")
    if len(text) > MANUAL_TEXT_MAX_CHARS:
        raise HTTPException(422, f"text too long (max {MANUAL_TEXT_MAX_CHARS} chars)")

    # pseudo-pages (~1500 chars on paragraph boundaries) for chunking
    paras, buf = [p for p in text.split("\n\n") if p.strip()], []
    pages, current = [], ""
    for p in paras:
        if len(current) + len(p) + 2 > 1500 and current:
            pages.append(current)
            current = p
        else:
            current = (current + "\n\n" + p) if current else p
    if current:
        pages.append(current)
    if not pages:
        pages = [text[:1500]]
    parsed = ParsedDocument(
        title=src.filename or "",
        pages=[ParsedPage(page_no=i + 1, text=p) for i, p in enumerate(pages)],
        page_count=len(pages))
    drafts = chunk_parsed(parsed)
    if not drafts:
        raise HTTPException(422, "no indexable content in text")

    removed = _cleanup_previous_ingest(db, storage, src)
    doc = Document(workspace_id=src.workspace_id, source_id=src.id,
                   title=normalize_fa(src.filename or ""), page_count=len(pages))
    db.add(doc)
    db.flush()
    if embed_fn is None:
        from app.ai.factory import get_embedding_provider

        embed_fn = get_embedding_provider(db).embed
    final_texts = [normalize_fa(d.text) for d in drafts]
    vectors = embed_fn(final_texts)
    if len(vectors) != len(drafts):
        raise HTTPException(500, "embedding count mismatch")
    for draft, vector, content in zip(drafts, vectors, final_texts):
        seg = PageSegment(workspace_id=src.workspace_id, document_id=doc.id,
                          page_no=draft.page_no, text=draft.text)
        db.add(seg)
        db.flush()
        db.add(Chunk(workspace_id=src.workspace_id, kb_id=src.kb_id,
                     segment_id=seg.id, content=content,
                     tokens=int(len(content.split()) * 1.3),
                     embedding=list(vector),
                     chunk_metadata=dict(draft.metadata or {})))
    db.flush()
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        from sqlalchemy import func, update

        db.execute(
            update(Chunk)
            .where(Chunk.segment_id.in_(
                db.query(PageSegment.id).filter(PageSegment.document_id == doc.id)))
            .values(content_tsv=func.to_tsvector("simple", Chunk.content)))
    src.status = "ready"
    src.error = ""
    src.processing_started_at = None
    meta = dict(src.parse_meta or {})
    from datetime import datetime, timezone

    meta["manual_text"] = {"chars": len(text),
                           "at": datetime.now(timezone.utc).isoformat()}
    src.parse_meta = meta
    db.commit()
    audit(db, "knowledge.source_text_replace", actor_user_id=user.id,
          workspace_id=ws.id, entity="source", entity_id=src.id,
          meta={"filename": src.filename, "chars": len(text),
                "chunks": len(drafts), **removed})
    return {"id": str(src.id), "filename": src.filename, "status": src.status,
            "chars": len(text), "chunks": len(drafts), **removed}


def suggest_doc_title(db: Session, text: str) -> str:
    """AI short title for an Add-to-Docs save (fail-open to heuristic).

    One tiny LLM call (temperature 0.3, 60 tokens). Any failure returns
    the first ~6 words so the UX never blocks.
    """
    clean = (text or "").strip()
    if not clean:
        raise HTTPException(422, "text is empty")
    capped = clean[:2000]
    fallback = " ".join(capped.split()[:6])[:80] or "یادداشت"
    try:
        from app.ai.factory import get_chat_provider

        prompt = (
            "یک عنوان فارسی خیلی کوتاه (حداکثر ۶ کلمه) برای این متن بساز. "
            "فقط خود عنوان را برگردان، بدون نقل‌قول و توضیح:\n"
            f"{capped}"
        )
        title = str(
            get_chat_provider(db).generate(prompt, temperature=0.3, max_tokens=60)
            or ""
        ).strip().strip("\"'«»").strip()[:80]
        return title if len(title) >= 2 else fallback
    except Exception:
        return fallback


def rename_source(db: Session, ws: Workspace, kb: KnowledgeBase,
                  user: User, source_id, title: str) -> Source:
    """Rename a source's display title (sources.filename).

    Used by Add-to-Docs for voice/meeting audio docs: the user picks the
    title AFTER hearing/reading, and the already-ingested audio doc keeps
    its RAG footprint (no re-ingest, only the filename changes).
    """
    src = get_source(db, ws, source_id)
    if str(src.kb_id) != str(kb.id):
        raise HTTPException(404, "source not found")
    title = (title or "").strip()
    if not title:
        raise HTTPException(422, "title is required")
    # Keep the original extension so downloads keep working.
    orig = src.filename or ""
    ext = ""
    if "." in orig and len(orig.rsplit(".", 1)[-1]) <= 12:
        ext = "." + orig.rsplit(".", 1)[-1]
    base = title[:480 - len(ext)] if ext else title[:480]
    src.filename = f"{base}{ext}" if ext else base
    db.commit()
    db.refresh(src)
    audit(db, "knowledge.source_rename", actor_user_id=user.id, workspace_id=ws.id,
          entity="source", entity_id=src.id, meta={"filename": src.filename})
    return src


def revise_source_text(db: Session, ws: Workspace, kb: KnowledgeBase,
                       user: User, source_id, instruction: str) -> dict:
    """AI-assisted re-extraction preview (NOT saved).

    The model rewrites the current extracted text per the user's
    instruction (what to extract / how); the frontend previews it and
    saves via replace_source_text. Input capped to bound cost.
    """
    from app.ai.factory import get_chat_provider

    src = get_source(db, ws, source_id)
    if str(src.kb_id) != str(kb.id):
        raise HTTPException(404, "source not found")
    instruction = (instruction or "").strip()
    if not instruction:
        raise HTTPException(422, "instruction is empty")
    current = (source_transcript(db, src).get("text") or "").strip()
    if not current:
        raise HTTPException(422, "source has no extracted text yet")
    capped = current[:30000]
    prompt = (
        "متن استخراج‌شده یک سند در ادامه آمده. دقیقاً طبق «دستور» کاربر آن را "
        "بازنویسی/استخراج مجدد کن و فقط متن نهایی را برگردان (بدون توضیح اضافه):\n\n"
        f"دستور: {instruction[:2000]}\n\nمتن:\n{capped}"
        + ("…(truncated)" if len(current) > 30000 else ""))
    try:
        revised = str(get_chat_provider(db).generate(
            prompt, temperature=0.2, max_tokens=6000) or "").strip()
    except Exception as exc:
        raise HTTPException(502, f"AI revision failed: {exc}")
    if not revised:
        raise HTTPException(502, "AI returned empty revision")
    return {"revised": revised, "chars": len(revised),
            "source_chars": len(current)}
