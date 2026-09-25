"""Async ingestion pipeline: the real worker.

Upload -> Storage -> Source(row) -> this task -> parse -> structure ->
chunk -> embed -> PostgreSQL -> ready/failed.

`run_ingest` is the pure, testable core (injectable session/storage/AI
functions). The Celery task builds production dependencies.
"""

import traceback
from uuid import UUID

from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register all tables for FK metadata)
from app.core.database import SessionLocal, set_workspace_context
from app.knowledge.chunking import chunk_parsed
from app.knowledge.models import Chunk, Document, PageSegment, Source
from app.knowledge.parsers import parse_source
from app.storage.s3 import get_storage


def _download(storage, key: str) -> bytes:
    return storage.get(key)


def _bind_admin_read(db: Session) -> None:
    """System-actuated admin context for GLOBAL sources only.

    Global rows (workspace_id NULL) are invisible to member contexts by
    design (RLS). Intake for them is admin-gated at the API layer, so the
    worker binds a transient admin context here to read/write them.
    Reset by reset_workspace_context on release (own_session path).
    No-op on non-Postgres databases (unit tests).
    """
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    from sqlalchemy import text as _text

    db.execute(_text("SELECT set_config('app.is_admin', 'true', false)"))
    db.execute(_text("SELECT set_config('app.user_id', '', false)"))


def _resolve_ocr_config(db) -> dict:
    """Admin-selected OCR config (system level): {provider, model}.

    `model` is the GapGPT-routed model id for the "gemini" path (Gemini
    via GapGPT's OpenAI-compatible endpoint — no Google key needed).
    SAFE DEFAULT provider 'disabled' until the admin chooses in the
    panel — never EasyOCR by default.
    """
    try:
        from app.ai.settings import get_setting

        row = get_setting(db, "ocr.provider") or {}
        provider = str(row.get("provider") or "disabled").strip().lower()
        model = row.get("model") or None
        return {"provider": provider,
                "model": str(model).strip() if model else None}
    except Exception:
        return {"provider": "disabled", "model": None}


def _run_vision_stage(db, drafts, parsed) -> dict:
    """Phase 3 (D) eager vision: triage image drafts, describe with Gemini,
    stash the description in draft.metadata (chunk content is composed at
    persist time; SEGMENT text stays untouched — locked D3).

    Returns counts {processed, skipped, failed, skipped_reasons}.
    No-op (all zeros) unless DOCUMENT_VISION_ENABLED.
    """
    from app.core.config import get_settings

    report: dict = {"processed": 0, "skipped": 0, "failed": 0,
                    "skipped_reasons": {}}
    s = get_settings()
    if not s.DOCUMENT_VISION_ENABLED:
        return report
    image_idx = [i for i, d in enumerate(drafts)
                 if (d.metadata or {}).get("kind") in ("image", "figure")]
    if not image_idx:
        return report
    page_texts: dict = {}
    for p in getattr(parsed, "pages", None) or []:
        if getattr(p, "page_no", None) is not None:
            page_texts[p.page_no] = getattr(p, "text", "") or ""
    if not page_texts:  # fall back to draft texts grouped by page
        for d in drafts:
            if d.page_no is not None and d.text.strip():
                page_texts[d.page_no] = (page_texts.get(d.page_no, "") + " "
                                         + d.text.strip()).strip()
    from app.knowledge.parsers.multimodal import (
        GeminiVisionProcessor,
        triage_figures,
    )

    model = None
    try:
        from app.ai.settings import get_setting

        model = (get_setting(db, "vision.provider") or {}).get("model") or None
    except Exception:
        model = None
    processor = GeminiVisionProcessor(model=model)
    to_describe, skipped = triage_figures(
        drafts, page_texts, s.DOC_VISION_MAX_FIGURES,
        s.VISION_CAPTION_MIN_CHARS, s.VISION_PAGE_TEXT_MIN_CHARS)
    for i, reason in skipped.items():
        drafts[i].metadata["vision_skipped"] = reason
        report["skipped"] += 1
        report["skipped_reasons"][reason] = report["skipped_reasons"].get(reason, 0) + 1
    from datetime import datetime, timezone

    for i in to_describe:
        d = drafts[i]
        caption = str((d.metadata or {}).get("caption", "") or "")
        try:
            if not (d.blob or b""):
                raise RuntimeError("no figure bytes persisted for vision")
            desc = processor.describe_figure(d.blob, caption)
            d.metadata["vision_description"] = desc
            d.metadata["vision_model"] = model or "default"
            d.metadata["vision_at"] = datetime.now(timezone.utc).isoformat()
            if _sounds_uncertain(desc):
                # Honest limitation flag: the model itself hedged — the
                # description stays (it says what is unclear), but downstream
                # must treat it as uncertain, never as verified fact.
                d.metadata["vision_uncertain"] = True
            report["processed"] += 1
        except Exception as exc:
            # Degraded, never confabulated: keep OUR placeholder text.
            d.metadata["vision_failed"] = f"{type(exc).__name__}: {exc}"[:200]
            report["failed"] += 1
    return report


_UNCERTAIN_MARKERS = ("نامشخص", "واضح نیست", "مشخص نیست", "نمی‌بینم",
                      "نمی‌توانم", "دیده نمی‌شود", "unclear", "cannot see")


def _sounds_uncertain(text: str) -> bool:
    lowered = (text or "").lower()
    return any(m in lowered for m in _UNCERTAIN_MARKERS)


def _vision_enabled() -> bool:
    try:
        from app.core.config import get_settings

        return bool(get_settings().DOCUMENT_VISION_ENABLED)
    except Exception:
        return False


def _fallback_figure_drafts(blob: bytes) -> list:
    """Best-effort figure candidates for fallback PDFs (no docling).

    Never raises: extraction must not fail the text ingest.
    """
    try:
        from app.core.config import get_settings
        from app.knowledge.chunking import pdf_figure_drafts

        s = get_settings()
        return pdf_figure_drafts(blob, max_figures=s.DOC_VISION_MAX_FIGURES,
                                 min_px=s.VISION_MIN_IMAGE_PX)
    except Exception:
        return []


def _cleanup_previous_ingest(db: Session, storage, src) -> dict:
    """Delete the previous ingest tree so retry/re-ingest never duplicates.

    Removes figure blobs from storage (best-effort per key) + chunks,
    segments, documents of this source. Runs under the already-bound
    tenant context, so RLS scoping holds. DB failures propagate loudly
    (a half-cleaned retry must not silently accumulate duplicates).
    """
    removed = {"documents": 0, "segments": 0, "chunks": 0, "blobs": 0}
    delete = getattr(storage, "delete", None)
    docs = db.query(Document).filter(Document.source_id == src.id).all()
    for doc in docs:
        segs = db.query(PageSegment).filter(PageSegment.document_id == doc.id).all()
        for seg in segs:
            for chunk in db.query(Chunk).filter(Chunk.segment_id == seg.id).all():
                key = (chunk.chunk_metadata or {}).get("storage_key")
                if key and callable(delete):
                    try:
                        delete(key)
                        removed["blobs"] += 1
                    except Exception:
                        pass
                db.delete(chunk)
                removed["chunks"] += 1
            db.delete(seg)
            removed["segments"] += 1
        db.delete(doc)
        removed["documents"] += 1
    db.flush()
    return removed


def _figure_key(src, chunk_index: int, element_id: str) -> str:
    base = f"workspaces/{src.workspace_id}/kb/{src.kb_id}/sources/{src.id}"
    if src.workspace_id is None:
        base = f"global/kb/{src.kb_id}/sources/{src.id}"
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in (element_id or "fig"))
    return f"{base}/figures/{chunk_index:04d}_{safe[:40]}.png"


def run_ingest(
    source_id: UUID | str,
    workspace_id: UUID | str | None = None,
    db: Session | None = None,
    storage=None,
    embed_fn=None,
    transcribe_fn=None,
    describe_fn=None,
    url_fetch_fn=None,
) -> dict:
    """Process one source end-to-end. Never raises: failures land on the row."""
    from app.common.base import coerce_uuid

    own_session = db is None
    db = db or SessionLocal()
    storage = storage or get_storage()
    parsed = None
    try:
        # Tenant-first: the worker pool starts context-free, so bind from
        # the task message before RLS-gated reads. Explicit arg wins;
        # otherwise fall back to the source row's own workspace (works
        # when the caller session already carries context, e.g. tests).
        if workspace_id is not None:
            set_workspace_context(db, workspace_id)
        src = db.query(Source).filter(Source.id == coerce_uuid(source_id)).first()
        if src is None and workspace_id is None:
            # Global source (workspace NULL): member contexts cannot even
            # read the row (RLS by design) — bind transient admin context.
            _bind_admin_read(db)
            src = db.query(Source).filter(Source.id == coerce_uuid(source_id)).first()
        if src is None:
            return {"ok": False, "error": "source not found"}
        if src.workspace_id is not None:
            set_workspace_context(db, src.workspace_id)
        else:
            _bind_admin_read(db)
        src.status = "processing"
        src.error = ""
        db.commit()
        replaced = _cleanup_previous_ingest(db, storage, src)

        try:
            blob = _download(storage, src.storage_key)
        except Exception as exc:
            raise RuntimeError(f"Download: {type(exc).__name__}: {exc}") from exc
        try:
            ocr_cfg = _resolve_ocr_config(db)
            parsed = parse_source(
                src.type, src.filename or "file", blob,
                transcribe_fn=transcribe_fn, describe_fn=describe_fn,
                url_fetch_fn=url_fetch_fn,
                ocr_provider=ocr_cfg["provider"],
                ocr_model=ocr_cfg.get("model"),
            )
        except Exception as exc:
            raise RuntimeError(f"Parse: {type(exc).__name__}: {exc}") from exc
        is_audio = src.type == "audio"

        doc = Document(
            workspace_id=src.workspace_id, source_id=src.id,
            title=parsed.title or (src.filename or ""),
            page_count=parsed.page_count, duration_ms=parsed.duration_ms,
        )
        db.add(doc)
        db.flush()

        try:
            drafts = chunk_parsed(parsed, is_audio=is_audio)
        except Exception as exc:
            raise RuntimeError(f"Chunk: {type(exc).__name__}: {exc}") from exc
        if (not is_audio and src.type == "pdf"
                and _vision_enabled()
                and not any((d.metadata or {}).get("kind") in ("image", "figure")
                            for d in drafts)):
            # Separate fallback wiring (no docling): embedded PDF figures
            # become vision candidates for the stage below.
            drafts += _fallback_figure_drafts(blob)
        if not drafts and (parsed.parse_meta or {}).get("ocr_degraded"):
            # Locked rule: a degraded-OCR document with zero chunks is a
            # FAILURE with a clear reason — never a successful empty ingest.
            empty = (parsed.parse_meta or {}).get("ocr_empty_pages", [])
            reason = (parsed.parse_meta or {}).get(
                "ocr_reason", (parsed.parse_meta or {}).get("ocr_refill_failed", "no text"))
            raise RuntimeError(f"OCR: pages without text: {empty}: {reason}")
        # D2 eager vision runs BEFORE embedding (embed sees final content).
        vision_report = _run_vision_stage(db, drafts, parsed)
        # Locked D3 composition (single place): chunk content = original
        # draft text + vision description. Segments keep draft.text.
        final_texts = []
        for d in drafts:
            vdesc = str((d.metadata or {}).get("vision_description", "") or "").strip()
            final_texts.append(d.text + (("\n" + vdesc) if vdesc else ""))
        if embed_fn is None:
            from app.ai.openai_compat import OpenAICompatProvider

            embed_fn = OpenAICompatProvider().embed
        try:
            vectors = embed_fn(final_texts) if drafts else []
        except Exception as exc:
            raise RuntimeError(f"Embed: {type(exc).__name__}: {exc}") from exc

        new_chunks: list[Chunk] = []
        try:
            for i, (draft, vector, content) in enumerate(zip(drafts, vectors, final_texts)):
                # SEGMENT text stays original (locked D3); the CHUNK carries
                # caption + vision description (composed here, embedded above).
                seg = PageSegment(
                    workspace_id=src.workspace_id, document_id=doc.id,
                    page_no=draft.page_no, start_ms=draft.start_ms, end_ms=draft.end_ms,
                    text=draft.text,
                )
                db.add(seg)
                db.flush()
                meta = dict(draft.metadata or {})
                if draft.blob:
                    key = _figure_key(src, i, str(meta.get("element_id", "")))
                    storage.put(key, draft.blob, content_type="image/png")
                    meta["storage_key"] = key
                    meta["bytes"] = len(draft.blob)
                # content already composed above (== draft.text + description).
                chunk = Chunk(
                    workspace_id=src.workspace_id, kb_id=src.kb_id, segment_id=seg.id,
                    content=content, tokens=int(len(content.split()) * 1.3),
                    embedding=list(vector), chunk_metadata=meta,
                )
                db.add(chunk)
                new_chunks.append(chunk)
            db.flush()
        except Exception as exc:
            raise RuntimeError(f"Persist: {type(exc).__name__}: {exc}") from exc
        if new_chunks and db.bind is not None and db.bind.dialect.name == "postgresql":
            from sqlalchemy import func, update

            db.execute(
                update(Chunk)
                .where(Chunk.id.in_([c.id for c in new_chunks]))
                .values(content_tsv=func.to_tsvector("simple", Chunk.content))
            )
        src.status = "ready"
        src.parse_meta = {**(parsed.parse_meta or {}),
                          "vision": vision_report,
                          "chunks": len(drafts),
                          "replaced": replaced}
        db.commit()
        return {"ok": True, "chunks": len(drafts),
                "parser": (parsed.parse_meta or {}).get("parser", "fallback"),
                "vision": vision_report}
    except Exception as exc:
        try:
            src = db.query(Source).filter(Source.id == coerce_uuid(source_id)).first()
            if src is not None:
                src.status = "failed"
                src.error = f"{type(exc).__name__}: {exc}"[:500]
                if parsed is not None:
                    src.parse_meta = dict(parsed.parse_meta or {})
                db.commit()
        except Exception:
            db.rollback()
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if own_session:
            from app.core.database import reset_workspace_context

            reset_workspace_context(db)
            db.close()


def _task():
    from workers.celery_app import celery

    @celery.task(name="ingest_source", bind=True, max_retries=3)
    def ingest_source(self, source_id: str, workspace_id: str | None = None) -> dict:
        # Tenant travels in the task message; run_ingest binds RLS context
        # on its own session before touching any row.
        try:
            return run_ingest(source_id, workspace_id=workspace_id)
        except Exception as exc:  # broker-visible retry for infra errors
            traceback.print_exc()
            raise self.retry(exc=exc, countdown=60)

    return ingest_source


ingest_source = _task()
