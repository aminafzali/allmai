from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.ai.settings import get_setting
from app.auth.service import get_current_user
from app.core.database import get_db
from app.knowledge import schemas
from app.knowledge.retrieval.hybrid import (
    get_embed_fn,
    get_generate_fn,
    hybrid_search,
)
from app.knowledge.service import (
    create_file_source,
    create_kb,
    create_link_source,
    delete_source,
    get_kb,
    get_source,
    list_kbs,
    list_sources,
    retry_source,
)
from app.storage.s3 import get_storage
from app.users.models import User
from app.workspaces.models import Workspace
from app.workspaces.service import require_workspace_role, resolve_workspace

router = APIRouter(tags=["knowledge"])


def _kb(ws: Workspace, kb_id, db: Session):
    return get_kb(db, ws, kb_id)


@router.post("/workspaces/{workspace_id}/knowledge-bases",
             response_model=schemas.KnowledgeBaseOut, status_code=201)
def post_kb(
    body: schemas.KnowledgeBaseCreate,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return create_kb(db, ws, user, body.title, body.description)


@router.get("/workspaces/{workspace_id}/knowledge-bases",
            response_model=list[schemas.KnowledgeBaseOut])
def get_kbs(ws: Workspace = Depends(resolve_workspace), db: Session = Depends(get_db)):
    return list_kbs(db, ws)


@router.get("/workspaces/{workspace_id}/knowledge-bases/{kb_id}",
            response_model=schemas.KnowledgeBaseOut)
def get_kb_one(kb_id: str,
               ws: Workspace = Depends(resolve_workspace), db: Session = Depends(get_db)):
    return _kb(ws, kb_id, db)


@router.post("/workspaces/{workspace_id}/knowledge-bases/{kb_id}/sources",
             response_model=schemas.SourceOut, status_code=201)
def post_source_file(
    kb_id: str,
    type: str = Form(...),
    file: UploadFile = File(...),
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage=Depends(get_storage),
):
    kb = _kb(ws, kb_id, db)
    blob = file.file.read()
    return create_file_source(db, ws, kb, user, type, file.filename or "file", blob, storage)


@router.post("/workspaces/{workspace_id}/knowledge-bases/{kb_id}/sources/meeting",
             response_model=schemas.SourceOut, status_code=201)
def post_meeting_source(
    kb_id: str,
    title: str = Form(...),
    text: str = Form(...),
    file: UploadFile | None = File(None),
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage=Depends(get_storage),
    embed_fn=Depends(get_embed_fn),
):
    """Explicitly save a completed meeting (audio + exact transcript) as
    ONE indexed source. No source/RAG rows are created before this request."""
    from app.common.rate_limit import check
    from app.knowledge.service import create_meeting_source

    check("chat", str(user.id), calls=20, period_seconds=60)
    kb = _kb(ws, kb_id, db)
    blob = file.file.read() if file is not None else b""
    return create_meeting_source(
        db, ws, kb, user, title, text,
        file.filename if file is not None else "",
        blob, storage, embed_fn=embed_fn,
    )


@router.post("/workspaces/{workspace_id}/knowledge-bases/{kb_id}/suggest-title",
             response_model=schemas.SuggestTitleOut)
def post_suggest_title(
    kb_id: str,
    body: schemas.SuggestTitleIn,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """AI title for Add-to-Docs: short Persian title for a chat message /
    voice transcript. Fail-open (heuristic) so the UX never blocks."""
    from app.common.rate_limit import check as _check

    from app.knowledge.service import suggest_doc_title

    _check("chat", str(user.id), calls=30, period_seconds=60)
    _kb(ws, kb_id, db)  # membership + KB scoping
    return {"title": suggest_doc_title(db, body.text)}


@router.post("/workspaces/{workspace_id}/transcribe-audio")
def post_transcribe_audio(
    workspace_id: str,
    file: UploadFile = File(...),
    language: str | None = Form(None),
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Fast transcription WITHOUT creating a source (voice/meeting chunks).

    Accepts audio (mp3/wav/m4a) and browser containers (webm/mp4/ogg):
    browser blobs are ffmpeg-extracted to mp3 first, then sent to the
    same route as ingest (AvalAI/GapGPT/gemini by panel model id).
    Optional `language` (fa/en, ISO-639-1) hints the transcription model
    and kills cross-language misdetections on short slices.
    Returns {text, segments}. Membership-gated; rate-limited like chat.
    """
    from app.common.rate_limit import check as _check

    _check("chat", str(user.id), calls=60, period_seconds=60)
    blob = file.file.read() if file is not None else b""
    if not blob:
        from fastapi import HTTPException

        raise HTTPException(422, "empty file")
    if len(blob) > 25 * 1024 * 1024:
        from fastapi import HTTPException

        raise HTTPException(413, "audio too large (max 25 MB per chunk)")
    fname = (file.filename or "chunk.webm").lower()
    text = ""
    segments: list[dict] = []
    try:
        audio_blob, audio_name = blob, fname
        if fname.endswith((".webm", ".mp4", ".mov", ".ogg", ".opus")):
            try:
                import os
                import tempfile

                from app.knowledge.parsers.video import _extract_mp3

                with tempfile.TemporaryDirectory(prefix="allmai-tr-") as tmp:
                    src = os.path.join(tmp, "in.bin")
                    with open(src, "wb") as f:
                        f.write(blob)
                    out = _extract_mp3(blob, tmp)
                    with open(out, "rb") as f:
                        audio_blob = f.read()
                    audio_name = "audio.mp3"
            except Exception:
                audio_blob, audio_name = blob, "audio.mp3"
        from app.knowledge.parsers.audio import transcribe as _transcribe

        lang = (language or "").strip()[:16] or None
        for s_ms, e_ms, t in _transcribe(audio_blob, audio_name, db=db, language=lang):
            t = (t or "").strip()
            if t:
                segments.append({"start_ms": s_ms, "end_ms": e_ms, "text": t})
        text = "\n".join(s["text"] for s in segments).strip()
    except Exception as exc:
        from fastapi import HTTPException

        raise HTTPException(502, f"transcription failed: {exc}"[:300])
    return {"text": text, "segments": segments}


@router.patch("/workspaces/{workspace_id}/knowledge-bases/{kb_id}/sources/{source_id}/rename",
              response_model=schemas.SourceOut)
def patch_source_rename(
    kb_id: str, source_id: str, body: schemas.SourceRenameIn,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Rename a source (Add-to-Docs title for audio/meeting docs)."""
    from app.knowledge.service import rename_source

    return rename_source(db, ws, _kb(ws, kb_id, db), user, source_id, body.title)


@router.post("/workspaces/{workspace_id}/knowledge-bases/{kb_id}/sources/link",
             response_model=schemas.SourceOut, status_code=201)
def post_source_link(
    kb_id: str,
    body: schemas.SourceLinkCreate,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage=Depends(get_storage),
):
    kb = _kb(ws, kb_id, db)
    return create_link_source(db, ws, kb, user, body.type, body.title,
                              {"url": body.url, "content": body.content}, storage)


@router.get("/workspaces/{workspace_id}/knowledge-bases/{kb_id}/sources",
            response_model=list[schemas.SourceOut])
def get_sources(kb_id: str,
                ws: Workspace = Depends(resolve_workspace), db: Session = Depends(get_db)):
    return list_sources(db, ws, _kb(ws, kb_id, db))


@router.get("/workspaces/{workspace_id}/knowledge-bases/{kb_id}/sources/{source_id}/processing")
def get_source_processing(kb_id: str, source_id: str,
                          ws: Workspace = Depends(resolve_workspace),
                          db: Session = Depends(get_db)):
    """Document Core status for one source: stages + figures + OCR/Vision
    metadata. Membership-gated by resolve_workspace + kb scoping."""
    from app.knowledge.service import get_source, source_processing_detail

    kb = _kb(ws, kb_id, db)
    src = get_source(db, ws, source_id)
    if str(src.kb_id) != str(kb.id):
        from fastapi import HTTPException

        raise HTTPException(404, "source not found")
    return source_processing_detail(db, src)


@router.get("/workspaces/{workspace_id}/knowledge-bases/{kb_id}/sources/{source_id}/transcript")
def get_source_transcript(kb_id: str, source_id: str,
                          ws: Workspace = Depends(resolve_workspace),
                          db: Session = Depends(get_db)):
    """Full extracted text (audio transcription included) for viewing and
    downloading. Same membership + KB scoping as the processing endpoint."""
    from app.knowledge.service import get_source, source_transcript

    kb = _kb(ws, kb_id, db)
    src = get_source(db, ws, source_id)
    if str(src.kb_id) != str(kb.id):
        from fastapi import HTTPException

        raise HTTPException(404, "source not found")
    return source_transcript(db, src)


@router.post("/workspaces/{workspace_id}/sources/{source_id}/retry",
             response_model=schemas.SourceOut)
def post_source_retry(source_id: str,
                      ws: Workspace = Depends(resolve_workspace),
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    return retry_source(db, ws, user, source_id)


@router.delete("/workspaces/{workspace_id}/knowledge-bases/{kb_id}/sources/{source_id}")
def delete_source_file(
    kb_id: str, source_id: str,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage=Depends(get_storage),
):
    """Delete a source + EVERYTHING it learned (documents/segments/chunks
    with embeddings, figure/duckdb blobs, source blob). An in-flight
    worker task safely no-ops afterwards."""
    return delete_source(db, ws, _kb(ws, kb_id, db), user, source_id, storage)


@router.put("/workspaces/{workspace_id}/knowledge-bases/{kb_id}/sources/{source_id}/text")
def put_source_text(
    kb_id: str, source_id: str, body: schemas.SourceTextUpdate,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage=Depends(get_storage),
    embed_fn=Depends(get_embed_fn),
):
    """Manual edit of the extracted text: the old ingest tree is purged
    and the new text is chunked/embedded back into RAG. (A later
    retry/re-ingest overwrites the edit — recorded in parse_meta.)"""
    from app.knowledge.service import replace_source_text

    return replace_source_text(db, ws, _kb(ws, kb_id, db), user, source_id,
                               body.text, storage, embed_fn=embed_fn)


@router.post("/workspaces/{workspace_id}/knowledge-bases/{kb_id}/sources/{source_id}/revise")
def post_source_revise(
    kb_id: str, source_id: str, body: schemas.SourceReviseIn,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """AI re-extraction preview (not saved): rewrite the extracted text
    per instruction; the client previews then saves via PUT .../text."""
    from app.common.rate_limit import check
    from app.knowledge.service import revise_source_text

    check("chat", str(user.id), calls=30, period_seconds=60)
    return revise_source_text(db, ws, _kb(ws, kb_id, db), user, source_id,
                              body.instruction)


@router.get("/workspaces/{workspace_id}/sources/{source_id}/download-url")
def get_download_url(source_id: str,
                     ws: Workspace = Depends(resolve_workspace),
                     db: Session = Depends(get_db),
                     storage=Depends(get_storage)):
    """S3 backends: short-lived signed URL. Local backend: use /download."""
    from fastapi import HTTPException

    src = get_source(db, ws, source_id)
    if getattr(storage, "provider_name", "local") != "s3":
        raise HTTPException(409, "use the /download endpoint with the local backend")
    return {"url": storage.presigned_get(src.storage_key), "filename": src.filename}


@router.get("/workspaces/{workspace_id}/sources/{source_id}/download")
def download_source(source_id: str,
                    ws: Workspace = Depends(resolve_workspace),
                    db: Session = Depends(get_db),
                    storage=Depends(get_storage)):
    """Authenticated file download. Membership-checked; never public."""
    from fastapi import HTTPException
    from fastapi.responses import Response

    src = get_source(db, ws, source_id)
    try:
        data = storage.get(src.storage_key)
    except FileNotFoundError:
        raise HTTPException(404, "file not found in storage")
    # Filenames may be Persian/non-ASCII: a raw `filename="..."` header
    # breaks Starlette's latin-1 encoding (-> 500, surfacing in the browser
    # as a CORS error). Use RFC 5987: ascii fallback + UTF-8 filename*.
    from urllib.parse import quote as _quote

    original = src.filename or "file"
    ext = ""
    if "." in original:
        ext = "." + original.rsplit(".", 1)[-1][:12]
        ext = "".join(c for c in ext if c.isascii() and (c.isalnum() or c in (".", "-", "_"))) or ""
    fallback = f"file{ext}" if ext else "file"
    try:
        disposition = (f'attachment; filename="{fallback}"; '
                       f"filename*=UTF-8''{_quote(original, safe='')}")
        # Validate early so a bad name is a loud 500 in logs, not a hang.
        disposition.encode("latin-1")
    except Exception:
        disposition = f'attachment; filename="{fallback}"'
    return Response(
        content=data,
        media_type=src.mime or "application/octet-stream",
        headers={"Content-Disposition": disposition},
    )


def _serialize(sc) -> schemas.RetrievedChunkOut:
    seg, src = sc.segment, sc.source
    return schemas.RetrievedChunkOut(
        chunk_id=sc.chunk.id,
        content=sc.chunk.content,
        score=round(sc.score, 6),
        source_id=src.id if src else None,
        filename=(src.filename if src else ""),
        page_no=seg.page_no if seg else None,
        start_ms=seg.start_ms if seg else None,
        end_ms=seg.end_ms if seg else None,
        vector_rank=sc.vector_rank,
        fts_rank=sc.fts_rank,
    )


@router.post("/workspaces/{workspace_id}/knowledge/search",
             response_model=schemas.SearchOut)
def post_search(
    body: schemas.SearchIn,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    embed_fn=Depends(get_embed_fn),
):
    hits = hybrid_search(db, ws.id, body.query, body.kb_id, body.top_k, embed_fn=embed_fn)
    return schemas.SearchOut(query=body.query, chunks=[_serialize(h) for h in hits])


@router.post("/workspaces/{workspace_id}/knowledge/debug",
             response_model=schemas.DebugOut)
def post_debug(
    body: schemas.SearchIn,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    embed_fn=Depends(get_embed_fn),
    generate_fn=Depends(get_generate_fn),
):
    # Retrieval debugger is an admin tool: owner/admin role required.
    require_workspace_role(db, ws, user, ("owner", "admin"))
    from app.common.rate_limit import check as _check

    _check("debug", str(user.id), calls=30, period_seconds=60)
    import time as _time

    t0 = _time.perf_counter()
    hits = hybrid_search(db, ws.id, body.query, body.kb_id, body.top_k, embed_fn=embed_fn)
    retrieval_ms = int((_time.perf_counter() - t0) * 1000)
    parts = []
    for i, h in enumerate(hits, 1):
        ref = h.source.filename if h.source else "?"
        loc = f"p.{h.segment.page_no}" if h.segment and h.segment.page_no else (
            f"{h.segment.start_ms}-{h.segment.end_ms}ms" if h.segment and h.segment.start_ms is not None else "?")
        parts.append(f"[{i}] ({ref} {loc})\n{h.chunk.content}")
    context = "\n\n".join(parts)
    chat = get_setting(db, "chat.default")
    prompt = (f"Answer in Persian using ONLY the context below. Cite sources like [1], [2].\n\n"
              f"Question: {body.query}\n\nContext:\n{context or '(empty)'}")
    t1 = _time.perf_counter()
    answer = generate_fn(prompt, model=chat.get("model"), temperature=chat.get("temperature", 0.7),
                         max_tokens=chat.get("max_tokens", 1500))
    generation_ms = int((_time.perf_counter() - t1) * 1000)
    return schemas.DebugOut(query=body.query, model=str(chat.get("model")),
                            retrieved=[_serialize(h) for h in hits],
                            context=context, answer=answer,
                            retrieval_ms=retrieval_ms, generation_ms=generation_ms)
