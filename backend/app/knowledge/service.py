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
from app.common.base import coerce_uuid
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
    src = Source(id=uuid4(), workspace_id=ws.id, kb_id=kb.id, type=type, status="pending")
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
        src.status = "processing"
    db.commit()
    db.refresh(src)
    audit(db, "knowledge.source_upload", actor_user_id=user.id, workspace_id=ws.id,
          entity="source", entity_id=src.id,
          meta={"type": source_type, "bytes": len(blob), "status": src.status})
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
                 type=source_type, status="pending")
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
        src.status = "processing"
    db.commit()
    db.refresh(src)
    audit(db, "knowledge.global_source_upload", actor_user_id=user.id,
          entity="source", entity_id=src.id,
          meta={"type": source_type, "bytes": len(blob), "status": src.status})
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
        src.status = "processing"
    db.commit()
    db.refresh(src)
    audit(db, "knowledge.source_create", actor_user_id=user.id, workspace_id=ws.id,
          entity="source", entity_id=src.id,
          meta={"type": source_type, "status": src.status})
    return src


def list_sources(db: Session, ws: Workspace, kb: KnowledgeBase) -> list[Source]:
    return (
        db.query(Source)
        .filter(Source.kb_id == kb.id, Source.workspace_id == ws.id)
        .order_by(Source.created_at.desc())
        .all()
    )


def retry_source(db: Session, ws: Workspace, user: User, source_id) -> Source:
    """Re-queue a pending/failed/processing-stuck source for ingestion."""
    from app.auth.service import audit

    src = get_source(db, ws, source_id)
    src.status = "pending"
    src.error = ""
    db.commit()
    if enqueue_ingest(src.id, ws.id):
        src.status = "processing"
        db.commit()
    db.refresh(src)
    audit(db, "knowledge.source_retry", actor_user_id=user.id, workspace_id=ws.id,
          entity="source", entity_id=src.id, meta={"status": src.status})
    return src


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
    segments ordered by document, page, then audio timestamp. Workspace-
    agnostic core; callers enforce gating before calling.
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
    return {
        "source_id": str(src.id),
        "filename": src.filename,
        "type": src.type,
        "status": src.status,
        "segments": segments,
        "text": "\n\n".join(s["text"] for s in segments),
    }
