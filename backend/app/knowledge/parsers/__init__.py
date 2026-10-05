"""Parser dispatcher: Gemini-structured PDFs, flat fallback for the rest.

- pdf: GeminiStructuredParser (Gemini Flash via GapGPT, hybrid
  text-batches + vision for textless pages) -> structured elements with
  section chain + page numbers. Any Gemini failure falls through to the
  flat pypdf path below (loud, degraded flags) — ingestion never crashes
  on an extraction outage.
- docx/pptx/txt/md: deterministic flat local parsers (docx keeps tables
  in document order).
- Text-empty PDF pages on the flat path are refilled with direct
  GapGPT-routed Gemini OCR when the admin-selected ocr.provider is
  "gemini"; otherwise they are flagged degraded, never silently empty.
- Audio/image/video/url/excel/csv have their own paths.

Docling/RAG-Anything were removed from the extraction path entirely.
"""

from app.core.config import get_settings
from app.knowledge.parsers.audio import chapterize, transcribe
from app.knowledge.parsers.base import (
    ParsedDocument,
    ParsedElement,
    ParsedPage,
    validate_source_type,
)
from app.knowledge.parsers.fallback import FALLBACK, parse_image_meta


def _chapter_elements(segments: list) -> list:
    """Chapter headings (سرفصل) for timestamped transcripts.

    No diarization: elements carry time ranges + titles only, never
    speaker labels. Chunking stamps each audio window with its chapter
    (section metadata); the transcript view derives the same list.
    """
    elements = []
    for i, ch in enumerate(chapterize(segments)):
        eid = f"ch{i + 1}"
        elements.append(ParsedElement(
            element_id=eid, kind="chapter", page_no=None,
            reading_order=i + 1, text_markdown=ch["title"],
            metadata={"element_id": eid, "reading_order": i + 1,
                      "kind": "chapter", "section": "",
                      "parent_id": "", "document_order": i + 1,
                      "start_ms": ch["start_ms"], "end_ms": ch["end_ms"],
                      "title": ch["title"]}))
    return elements


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
    """Direct Gemini OCR for textless PDF pages on the flat fallback path.

    Runs only when Gemini structured extraction is off or fell through:
    pages with no extractable text are rasterized + transcribed with the
    existing GapGPT-routed `gemini_ocr_pages`. Mutates doc.pages +
    doc.parse_meta in place. Only strictly-empty pages are refilled
    (never overwrites parsed text). Degraded, never silent: a non-gemini
    provider or any failure flags ocr_degraded + ocr_empty_pages, so the
    worker's locked rule turns a zero-chunk result into a loud failure
    instead of a green Ready.
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
        reason = "easyocr-retired"
    else:
        reason = f"unknown-ocr-provider:{provider}"
    meta.update({"ocr_degraded": True, "ocr_empty_pages": sorted(empty),
                 "ocr_reason": reason})


def _adapt_vision_seam(describe_fn, pdf_vision_fn):
    """Adapt a describe-style image->text seam (one arg) to the vision
    contract (image, prompt, model, ...). Explicit pdf_vision_fn wins;
    None means the parser default (usage-capturing)."""
    if pdf_vision_fn is not None:
        return pdf_vision_fn
    if describe_fn is None:
        return None
    _describe = describe_fn

    def _vision(image_bytes, prompt="", model=None,
                max_tokens=3000, timeout=120.0):
        return _describe(image_bytes)

    return _vision


def _parse_pdf(blob: bytes, filename: str,
               ocr_provider: str | None, ocr_model: str | None,
               pdf_model: str | None,
               pdf_generate_fn=None, pdf_vision_fn=None) -> ParsedDocument:
    """PDF route: Gemini structured first, flat fallback (with refill) on
    any failure. Flat is the safety net, never the first answer."""
    s = get_settings()
    if s.USE_GEMINI_PDF:
        try:
            from app.knowledge.parsers.gemini_structure import (
                GeminiStructuredParser,
            )

            return GeminiStructuredParser().parse(
                "pdf", filename, blob, model=pdf_model,
                generate_fn=pdf_generate_fn, vision_fn=pdf_vision_fn)
        except Exception:
            pass
    from app.knowledge.parsers.fallback import parse_pdf

    doc = parse_pdf(blob)
    _refill_blank_pdf_pages(doc, blob, ocr_provider, ocr_model)
    return doc


def parse_source(
    source_type: str,
    filename: str,
    blob: bytes,
    transcribe_fn=None,
    describe_fn=None,
    url_fetch_fn=None,
    ocr_provider: str | None = None,
    ocr_model: str | None = None,
    pdf_model: str | None = None,
    pdf_generate_fn=None,
    pdf_vision_fn=None,
) -> ParsedDocument:
    """Entry point for the worker. transcribe_fn/describe_fn/url_fetch_fn
    are seams for tests (production defaults hit real APIs).
    ocr_provider (admin-selected, resolved by the caller): gemini|
    disabled. None means unresolved -> disabled (safe default until the
    admin chooses in the panel).
    ocr_model: GapGPT-routed model id for the "gemini" refill path.
    pdf_model: GapGPT-routed Gemini id for structured PDF extraction
    (from the `extraction.pdf` ai_setting); None -> setting default.
    pdf_generate_fn/pdf_vision_fn: hermetic seams for the structured PDF
    path (each may return str or (text, usage)); describe_fn doubles as
    the vision seam when pdf_vision_fn is unset.
    """
    source_type = validate_source_type(source_type, filename)
    if source_type == "audio":
        segments = (transcribe_fn or transcribe)(blob, filename)
        duration = max((e for _, e, _ in segments), default=0)
        return ParsedDocument(
            title=filename,
            pages=[ParsedPage(start_ms=s, end_ms=e, text=t) for s, e, t in segments],
            duration_ms=duration,
            elements=_chapter_elements(segments),
        )
    if source_type == "video":
        # Audio-track transcription only (no frame analysis). The worker's
        # transcribe_fn seam doubles as the per-piece transcriber so tests
        # stay offline; duration/size guards live in the video module.
        from app.knowledge.parsers.video import transcribe_video

        segments = transcribe_video(blob, filename, transcribe_fn=transcribe_fn)
        duration = max((e for _, e, _ in segments), default=0)
        return ParsedDocument(
            title=filename,
            pages=[ParsedPage(start_ms=s, end_ms=e, text=t) for s, e, t in segments],
            duration_ms=duration,
            elements=_chapter_elements(segments),
            parse_meta={"parser": "video-whisper"},
        )
    if source_type == "image":
        return parse_image_meta(blob, filename, describe_fn or describe_image_default)
    if source_type == "url":
        from app.knowledge.parsers.fallback import parse_url_descriptor

        return parse_url_descriptor(blob, fetch_fn=url_fetch_fn,
                                    transcribe_fn=transcribe_fn)
    if source_type == "excel":
        from app.knowledge.parsers.excel import parse_excel

        return parse_excel(blob)
    if source_type == "csv":
        from app.knowledge.parsers.excel import parse_csv

        return parse_csv(blob, filename=filename)
    if source_type == "pdf":
        return _parse_pdf(blob, filename, ocr_provider, ocr_model, pdf_model,
                          pdf_generate_fn=pdf_generate_fn,
                          pdf_vision_fn=_adapt_vision_seam(describe_fn,
                                                           pdf_vision_fn))
    # docx/pptx/txt/md/note: flat local parsers (structured plain-text
    # elements for txt/md/note; tables preserved for docx).
    return FALLBACK[source_type](blob)
