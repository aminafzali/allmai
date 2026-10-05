"""Word-based chunking that preserves page numbers and audio timestamps.

- Text pages: ~400 words per chunk, 60-word overlap, paragraph-aware.
- Audio: merge transcript segments into ~45s windows.
- Phase 2 structured elements: text elements split like pages (with
  element metadata attached); tables/equations are ATOMIC (never cut
  mid-table; oversized tables split by row groups with header repeat);
  images/figures become one caption placeholder draft each.
Empty pages/segments produce no chunks (never index empty content).
"""

from dataclasses import dataclass, field

from app.knowledge.parsers.base import ParsedDocument

TARGET_WORDS = 400
OVERLAP_WORDS = 60
AUDIO_WINDOW_MS = 45_000


@dataclass
class ChunkDraft:
    text: str
    tokens: int
    page_no: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    # Phase 2: lands in Chunk.chunk_metadata; blob goes to StorageProvider.
    metadata: dict = field(default_factory=dict)
    blob: bytes | None = None


def _split_words(text: str, page_no: int | None,
                 metadata: dict | None = None) -> list[ChunkDraft]:
    words = text.split()
    if not words:
        return []
    chunks: list[ChunkDraft] = []
    step = max(TARGET_WORDS - OVERLAP_WORDS, 1)
    for start in range(0, len(words), step):
        piece = words[start : start + TARGET_WORDS]
        if len(piece) < OVERLAP_WORDS and chunks:
            break  # trailing stub: skip instead of indexing a sliver
        content = " ".join(piece)
        chunks.append(ChunkDraft(text=content, tokens=int(len(piece) * 1.3),
                                 page_no=page_no,
                                 metadata=dict(metadata or {})))
        if start + TARGET_WORDS >= len(words):
            break
    return chunks


def _chapter_for(chapters, win_start: int) -> dict | None:
    """Chapter containing a window start (chapters sorted by start_ms)."""
    hit = None
    for ch in chapters or []:
        try:
            start = int(ch.get("start_ms", 0))
        except (TypeError, ValueError):
            continue
        if start <= (win_start or 0):
            hit = ch
        else:
            break
    return hit


def _split_audio(pages, chapters=None) -> list[ChunkDraft]:
    """Merge transcript pages into ~45s windows, stamping each window
    with its chapter (section metadata) so RAG cites سرفصل + timestamp."""
    chunks: list[ChunkDraft] = []

    def _emit(buf, win_start, win_end):
        content = " ".join(buf)
        meta: dict = {"kind": "transcript"}
        ch = _chapter_for(chapters, win_start or 0)
        if ch:
            meta["section"] = str(ch.get("title", ""))
            meta["chapter_start_ms"] = int(ch.get("start_ms", 0) or 0)
        chunks.append(ChunkDraft(text=content, tokens=int(len(content.split()) * 1.3),
                                 start_ms=win_start, end_ms=win_end,
                                 metadata=meta))

    buf: list[str] = []
    win_start: int | None = None
    win_end: int | None = None
    for p in pages:
        if not p.text.strip():
            continue
        if win_start is None:
            win_start = p.start_ms or 0
        buf.append(p.text.strip())
        win_end = p.end_ms or win_start
        if win_end - (win_start or 0) >= AUDIO_WINDOW_MS:
            _emit(buf, win_start, win_end)
            buf, win_start, win_end = [], None, None
    if buf:
        _emit(buf, win_start or 0, win_end or 0)
    return chunks


def _audio_chapters(doc) -> list[dict]:
    """Chapter dicts from parsed chapter elements (metadata-carried)."""
    out = []
    for el in getattr(doc, "elements", None) or []:
        meta = getattr(el, "metadata", None) or {}
        if meta.get("kind") != "chapter":
            continue
        try:
            out.append({"start_ms": int(meta.get("start_ms", 0) or 0),
                        "end_ms": int(meta.get("end_ms", 0) or 0),
                        "title": str(meta.get("title", "") or "")})
        except (TypeError, ValueError):
            continue
    return sorted(out, key=lambda c: c["start_ms"])


def chunk_parsed(doc: ParsedDocument, is_audio: bool = False) -> list[ChunkDraft]:
    if is_audio:
        drafts = _split_audio(doc.pages, _audio_chapters(doc))
    elif getattr(doc, "elements", None):
        drafts = _split_elements(doc.elements)
    else:
        # Timestamped transcript pages from non-audio sources (e.g. URL video
        # transcripts) merge into time windows exactly like audio, so
        # start_ms/end_ms survive to citations. Plain text pages chunk by words.
        timed = [p for p in doc.pages if (p.text or "").strip()]
        if timed and all(p.start_ms is not None and p.end_ms is not None
                         for p in timed):
            drafts = _split_audio(doc.pages, _audio_chapters(doc))
        else:
            drafts = []
            for page in doc.pages:
                drafts.extend(_split_words(page.text, page.page_no))
    # Locked invariant (see normalize: "never index empty content"): no
    # blank draft may reach embedding — strict providers 400 the WHOLE
    # batch on a single empty input, failing an otherwise healthy source.
    kept = [d for d in drafts if (d.text or "").strip()]
    dropped = len(drafts) - len(kept)
    if dropped:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "chunking dropped %d blank drafts", dropped)
    return kept


def pdf_figure_drafts(blob: bytes, max_figures: int = 5,
                      min_px: int = 120) -> list[ChunkDraft]:
    """Extract embedded PDF images as vision-candidate drafts (fallback path).

    Separate wiring so figures get Vision descriptions without docling:
    pypdf enumerates page images, sub-`min_px` icons are dropped, the rest
    are normalized to PNG and capped largest-first. Triage (caption /
    page-rich / over-cap) still happens later in the worker vision stage.
    Never raises: extraction is best-effort (odd encodings are skipped,
    never fail the text ingest).
    """
    try:
        import io as _io

        from pypdf import PdfReader

        pages = list(PdfReader(_io.BytesIO(blob)).pages)
    except Exception:
        return []
    cands: list[tuple[int, int, bytes, int]] = []  # area, page_no, png, idx
    for i, page in enumerate(pages):
        try:
            images = list(page.images or [])
        except Exception:
            continue
        for j, img in enumerate(images):
            try:
                pil = img.image
                w, h = int(pil.width), int(pil.height)
                if w < min_px or h < min_px:
                    continue
                if pil.mode != "RGB":
                    pil = pil.convert("RGB")
                buf = _io.BytesIO()
                pil.save(buf, format="PNG")
                cands.append((w * h, i + 1, buf.getvalue(), j))
            except Exception:
                continue
    cands.sort(key=lambda c: c[0], reverse=True)
    return [
        ChunkDraft(
            text=f"[Figure on page {page_no}]", tokens=8, page_no=page_no,
            metadata={"kind": "figure",
                      "element_id": f"fallback-p{page_no}-img{j}",
                      "caption": ""},
            blob=png,
        )
        for _area, page_no, png, j in cands[:max(0, max_figures)]
    ]


def _split_table(text: str, page_no: int | None, metadata: dict) -> list[ChunkDraft]:
    """Atomic tables: small ones stay whole; oversized ones split by row
    groups with the markdown header repeated in every part."""
    words = text.split()
    if len(words) <= int(TARGET_WORDS * 1.5):
        return [ChunkDraft(text=text, tokens=int(len(words) * 1.3),
                           page_no=page_no, metadata=dict(metadata))]
    lines = text.split("\n")
    header = [ln for ln in lines[:3] if ln.strip().startswith("|")]
    body = [ln for ln in lines if ln.strip().startswith("|")][len(header):]
    if not body:
        return _split_words(text, page_no, metadata)
    out: list[ChunkDraft] = []
    buf: list[str] = []
    buf_words = 0
    parts = 0
    for ln in body:
        buf.append(ln)
        buf_words += len(ln.split())
        if buf_words >= TARGET_WORDS:
            parts += 1
            content = "\n".join(header + buf)
            meta = dict(metadata, part=parts, of=None, atomic=False)
            out.append(ChunkDraft(text=content, tokens=int(len(content.split()) * 1.3),
                                  page_no=page_no, metadata=meta))
            buf, buf_words = [], 0
    if buf:
        parts += 1
        content = "\n".join(header + buf)
        out.append(ChunkDraft(text=content, tokens=int(len(content.split()) * 1.3),
                              page_no=page_no,
                              metadata=dict(metadata, part=parts, of=None, atomic=False)))
    for d in out:
        d.metadata["of"] = len(out)
    return out or [ChunkDraft(text=text, tokens=int(len(words) * 1.3),
                              page_no=page_no, metadata=dict(metadata))]


def _split_elements(elements) -> list[ChunkDraft]:
    out: list[ChunkDraft] = []
    for el in sorted(elements, key=lambda e: (e.reading_order or 0)):
        meta = dict(el.metadata or {})
        meta.setdefault("kind", el.kind)
        meta.setdefault("element_id", el.element_id)
        if el.page_no is not None:
            meta.setdefault("page_no", el.page_no)
        if el.kind == "table":
            out.extend(_split_table(el.text_markdown, el.page_no, meta))
        elif el.kind == "equation":
            text = (el.text_markdown or "").strip()
            if text:
                out.append(ChunkDraft(text=text, tokens=int(len(text.split()) * 1.3),
                                      page_no=el.page_no, metadata=meta))
        elif el.kind in ("image", "figure"):
            text = (el.text_markdown or "").strip() or "[Figure]"
            out.append(ChunkDraft(text=text, tokens=int(len(text.split()) * 1.3),
                                  page_no=el.page_no, metadata=meta, blob=el.blob))
        else:  # text / heading / unknown kinds chunk like pages
            out.extend(_split_words(el.text_markdown or "", el.page_no, meta))
    return out
