"""Parser dispatcher: structured parsers only when explicitly enabled.

USE_DOCLING=true (or legacy USE_RAGANYTHING=true) -> RAGAnythingParser
(parse-only: get_parser("docling") + mandatory Persian-OCR refill; falls
back locally if the packages are missing or parsing fails).
Default: deterministic local fallback parsers. Text-empty PDF pages on the
fallback path are refilled with direct GapGPT-routed Gemini OCR
(pypdfium2 rasterize + describe_image, no docling) when the admin-selected
ocr.provider is "gemini"; otherwise they are flagged degraded, never
silently empty.
Audio and image have their own paths (timestamps / vision).
"""

from app.core.config import get_settings
from app.knowledge.parsers.audio import transcribe
from app.knowledge.parsers.base import ParsedDocument, ParsedPage, validate_source_type
from app.knowledge.parsers.fallback import FALLBACK, parse_image_meta


def describe_image_default(blob: bytes) -> str:
    """Image description via the shared OpenAI-compatible vision call
    (gapgpt.app). Empty string on any failure. Delegates to
    OpenAICompatProvider.describe_image — no parallel vision logic here."""
    from app.ai.openai_compat import OpenAICompatProvider

    try:
        return (OpenAICompatProvider().describe_image(
            blob, "Describe this image briefly in Persian.",
            model="gpt-4o-mini", max_tokens=300) or "").strip()
    except Exception:
        return ""


def _refill_blank_pdf_pages(doc: ParsedDocument, blob: bytes,
                            ocr_provider: str | None,
                            ocr_model: str | None) -> None:
    """Direct Gemini OCR for textless PDF pages on the fallback path.

    Runs only when the docling/RAG-Anything route is off (or fell through):
    pages with no extractable text are rasterized + transcribed with the
    existing GapGPT-routed `gemini_ocr_pages` — no docling/easyocr imports,
    no new dependencies. Mutates doc.pages + doc.parse_meta in place.
    Only strictly-empty pages are refilled (never overwrites parsed text).
    Degraded, never silent: a non-gemini provider or any failure flags
    ocr_degraded + ocr_empty_pages, so the worker's locked rule turns a
    zero-chunk result into a loud failure instead of a green Ready.
    """
    from app.knowledge.parsers import ocr as _ocr

    provider = (ocr_provider or "disabled").strip().lower()
    empty = [i for i, p in enumerate(doc.pages) if not (p.text or "").strip()]
    meta = doc.parse_meta
    meta.setdefault("parser", "fallback")
    if not empty:
        return
    meta["ocr_provider"] = provider
    if ocr_model:
        meta["ocr_model"] = ocr_model
    if provider == "gemini":
        try:
            refilled = _ocr.gemini_ocr_pages(blob, set(empty), model=ocr_model)
        except Exception as exc:
            meta.update({"ocr_refill_failed": f"{type(exc).__name__}: {exc}"[:200],
                         "ocr_degraded": True,
                         "ocr_empty_pages": sorted(empty)})
            return
        for idx, text in (refilled or {}).items():
            if 0 <= idx < len(doc.pages) and (text or "").strip():
                doc.pages[idx].text = text.strip()
        still = sorted(i for i in empty if not (doc.pages[i].text or "").strip())
        meta["parser"] = "fallback+gemini-ocr-refill"
        meta["ocr_refilled_pages"] = sorted(set(empty) - set(still))
        meta["ocr_empty_pages"] = still
        if still:
            meta["ocr_degraded"] = True
        return
    if provider == "disabled":
        reason = "ocr-provider-disabled"
    elif provider == "easyocr":
        reason = "easyocr-requires-docling"
    else:
        reason = f"unknown-ocr-provider:{provider}"
    meta.update({"ocr_degraded": True, "ocr_empty_pages": sorted(empty),
                 "ocr_reason": reason})


def parse_source(
    source_type: str,
    filename: str,
    blob: bytes,
    transcribe_fn=None,
    describe_fn=None,
    url_fetch_fn=None,
    ocr_provider: str | None = None,
    ocr_model: str | None = None,
) -> ParsedDocument:
    """Entry point for the worker. transcribe_fn/describe_fn/url_fetch_fn
    are seams for tests (production defaults hit real APIs).
    ocr_provider (admin-selected, resolved by the caller): gemini|easyocr|
    disabled. None means unresolved -> the adapter treats it as disabled
    (safe default until the admin chooses in the panel).
    ocr_model: GapGPT-routed model id for the "gemini" path (from the
    `ocr.provider` ai_setting); None -> CHAT_MODEL fallback."""
    source_type = validate_source_type(source_type, filename)
    if source_type == "audio":
        segments = (transcribe_fn or transcribe)(blob, filename)
        duration = max((e for _, e, _ in segments), default=0)
        return ParsedDocument(
            title=filename,
            pages=[ParsedPage(start_ms=s, end_ms=e, text=t) for s, e, t in segments],
            duration_ms=duration,
        )
    if source_type == "image":
        return parse_image_meta(blob, filename, describe_fn or describe_image_default)
    if source_type == "url":
        from app.knowledge.parsers.fallback import parse_url_descriptor

        return parse_url_descriptor(blob, fetch_fn=url_fetch_fn)
    s = get_settings()
    # Phase 2 scope is PDF processing only; every other type keeps its
    # dedicated path (audio/image/url/fallback) regardless of flags.
    if source_type == "pdf" and (s.USE_DOCLING or s.USE_RAGANYTHING):
        try:
            from app.knowledge.parsers.raganything_adapter import RAGAnythingParser

            return RAGAnythingParser().parse(source_type, filename, blob,
                                              ocr_provider=ocr_provider,
                                              ocr_model=ocr_model)
        except (ImportError, RuntimeError, NotImplementedError):
            pass
    doc = FALLBACK[source_type](blob)
    if source_type == "pdf":
        _refill_blank_pdf_pages(doc, blob, ocr_provider, ocr_model)
    return doc
