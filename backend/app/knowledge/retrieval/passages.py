"""Passage building on top of fused hits (quality layer, not ranking).

Two jobs:

1. expand_hit_texts: merge each hit with its NEIGHBORING chunks of the
   same document (ordered by page/timestamp) into one coherent passage
   (capped). The LLM stops seeing mid-sentence fragments; citations keep
   pointing at the primary chunk, so downstream code is untouched.
2. maybe_source_fulltext: when the merged hits are dominated by ONE
   source (or the user names the article outright), pull that source's
   full extracted text (capped) as an extra context block. This is how
   "ask about this article" gets the whole article, not fragments.
"""

from __future__ import annotations

from app.common.base import coerce_uuid
from app.knowledge.models import Chunk, PageSegment
from app.knowledge.normalize import fa_tokens

EXPAND_CHARS = 2000
FULLTEXT_MAX_CHARS = 12000
FULLTEXT_DOMINANCE = 0.6


def _ordered_doc_chunks(db, document_id) -> list:
    """(chunk, segment) of one document in reading order."""
    rows = (
        db.query(Chunk, PageSegment)
        .join(PageSegment, PageSegment.id == Chunk.segment_id)
        .filter(PageSegment.document_id == coerce_uuid(document_id))
        .all()
    )
    out = []
    for c, s in rows:
        out.append((c, s.page_no or 0, s.start_ms or 0, str(c.id)))
    out.sort(key=lambda t: (t[1], t[2], t[3]))
    return [(c, s) for c, s, _, _ in out]


def _expand_cap(db=None) -> int:
    try:
        from app.ai.settings import list_settings

        cap = int((list_settings(db).get("retrieval.hybrid") or {}).get(
            "expand_chars", EXPAND_CHARS))
        return max(200, cap)
    except Exception:
        return EXPAND_CHARS


def expand_hit_texts(db, hits: list, max_chars: int | None = None) -> dict[str, str]:
    """primary chunk_id -> expanded passage text (capped, overlap-deduped).

    Windows fully covered by a higher-scored kept window are dropped, so
    the prompt never repeats the same paragraph twice. For structured
    hits (table/figure/equation with a section parent), the parent
    heading text is prefixed — unless already inside the window — so a
    table never arrives without its section context.
    """
    if not hits:
        return {}
    cap = max_chars or _expand_cap(db)
    # group hits per document, best-first
    by_doc: dict[str, list] = {}
    for h in hits:
        seg = getattr(h, "segment", None)
        did = str(getattr(seg, "document_id", "") or "")
        if did:
            by_doc.setdefault(did, []).append(h)
    windows: list[tuple[float, set[str], str]] = []  # (score, chunk_ids, text)
    for did, hs in by_doc.items():
        ordered = _ordered_doc_chunks(db, did)
        if not ordered:
            continue
        pos = {str(c.id): i for i, (c, _) in enumerate(ordered)}
        elmap = {}
        for c, _ in ordered:
            eid = ((getattr(c, "chunk_metadata", None) or {}).get("element_id"))
            if eid:
                elmap.setdefault(str(eid), c)
        for h in sorted(hs, key=lambda x: x.score, reverse=True):
            i = pos.get(str(h.chunk.id))
            if i is None:
                continue
            lo = max(0, i - 1)
            hi = min(len(ordered), i + 2)
            ids = {str(ordered[j][0].id) for j in range(lo, hi)}
            prefix = ""
            hmeta = (getattr(h.chunk, "chunk_metadata", None) or {})
            pid = str(hmeta.get("parent_id") or "")
            if pid:
                parent = elmap.get(pid)
                if parent is not None and str(parent.id) not in ids:
                    ptext = (parent.content or "").strip()
                    if ptext:
                        prefix = f"[{ptext}]\n"
            text = prefix + "\n".join((ordered[j][0].content or "") for j in range(lo, hi))
            if len(text) > cap:
                text = text[:cap] + "…"
            windows.append((h.score, ids, text, str(h.chunk.id)))
    windows.sort(key=lambda w: w[0], reverse=True)
    out: dict[str, str] = {}
    covered: set[str] = set()
    for _, ids, text, primary in windows:
        if ids and ids <= covered:
            continue  # fully covered by a better window
        covered |= ids
        out.setdefault(primary, text)
    # hits whose document vanished (deleted mid-flight): raw content
    for h in hits:
        out.setdefault(str(h.chunk.id), h.chunk.content or "")
    return out


def maybe_source_fulltext(db, merged: list[dict], query: str) -> dict | None:
    """Full extracted text of the DOMINANT source, or None.

    Triggers when (a) one source owns >= FULLTEXT_DOMINANCE of the merged
    chunks (min 2), or (b) every query token appears in a source's
    title/filename (the user named the article). Capped at
    FULLTEXT_MAX_CHARS. Callers render it as a separate context block;
    citations stay chunk-level.
    """
    if not merged:
        return None
    by_src: dict[str, list[dict]] = {}
    for c in merged:
        if c.get("source_id"):
            by_src.setdefault(str(c["source_id"]), []).append(c)
    if not by_src:
        return None
    qtoks = set(fa_tokens(query))
    # (b) named-article rule first
    titles = _source_titles(db, list(by_src))
    named = None
    if qtoks:
        for sid, chunks in by_src.items():
            text = f"{titles.get(sid, '')} {(chunks[0].get('source') or '')}"
            if qtoks <= set(fa_tokens(text)):
                named = sid
                break
    if named is not None:
        return _fulltext_of(db, named, by_src[named])
    # (c) filename-mention rule: the user names the FILE ("Executive
    # Summary.docx ... translate it") — any substantive filename token in
    # the query suffices (the full-coverage rule above is too strict when
    # the request carries extra words like "translate"). Only when the
    # message is actually about files (else content questions sharing a
    # token would be hijacked).
    if qtoks and any(w in f" {(query or '').lower()} " for w in
                     ("فایل", "متن", "سند", "مدرک", "file")):
        best, best_n = None, 0
        for sid, chunks in by_src.items():
            fn = {t for t in fa_tokens(chunks[0].get("source") or "")
                  if len(t) > 3}
            n = len(fn & qtoks)
            if n > best_n:
                best, best_n = sid, n
        if best is not None and best_n >= 1:
            return _fulltext_of(db, best, by_src[best])
    # (a) dominance rule
    total = sum(len(v) for v in by_src.values())
    top_sid, top_chunks = max(by_src.items(), key=lambda kv: len(kv[1]))
    if len(top_chunks) >= 2 and len(top_chunks) / max(total, 1) >= FULLTEXT_DOMINANCE:
        return _fulltext_of(db, top_sid, top_chunks)
    return None


def _source_titles(db, source_ids: list[str]) -> dict[str, str]:
    from uuid import UUID as _UUID

    from app.knowledge.models import Document as _Doc

    valid = []
    for s in source_ids or []:
        try:
            valid.append(s if isinstance(s, _UUID) else _UUID(str(s)))
        except (ValueError, AttributeError, TypeError):
            continue  # garbage ids (never from DB rows) are skipped
    if not valid:
        return {}
    rows = (
        db.query(_Doc.source_id, _Doc.title)
        .filter(_Doc.source_id.in_(valid))
        .all()
    )
    out: dict[str, str] = {}
    for sid, title in rows:
        key = str(sid)
        prev = out.get(key, "")
        cand = title or ""
        if len(cand) > len(prev):
            out[key] = cand
    return out


def _fulltext_of(db, source_id: str, chunks: list[dict]) -> dict | None:
    from app.knowledge.service import source_transcript

    from app.knowledge.models import Source as _Source

    src = db.query(_Source).filter(
        _Source.id == coerce_uuid(source_id)).first()
    if src is None:
        return None
    try:
        tr = source_transcript(db, src)
    except Exception:
        return None
    text = (tr.get("text") or "").strip()
    if not text:
        return None
    truncated = len(text) > FULLTEXT_MAX_CHARS
    if truncated:
        text = text[:FULLTEXT_MAX_CHARS] + "\n…(truncated)"
    first = chunks[0] if chunks else {}
    return {
        "source_id": str(source_id),
        "filename": src.filename or first.get("source") or "",
        "title": _source_titles(db, [source_id]).get(str(source_id), ""),
        "text": text,
        "truncated": truncated,
    }
