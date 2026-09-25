"""OCR provider router (Phase 2 top-up).

Exactly ONE provider handles OCR per ingestion (mutual exclusion):
- "easyocr": Docling + EasyOCR (Persian 'fa' mandatory). Lazy imports:
  nothing from easyocr/docling loads unless selected.
- "gemini": rasterize pages with pypdfium2, transcribe each via the SAME
  OpenAI-compatible integration (gapgpt.app) used everywhere else.
  No docling/easyocr imports on this path. The model comes from the
  `ocr.provider` ai_setting (`model` field, GapGPT-routed Gemini id) and
  falls back to CHAT_MODEL when unset — never a direct Google call.
- "disabled" (SAFE DEFAULT until the admin chooses in the panel):
  no OCR at all; pages stay empty and the caller flags degraded.

OCR (page/image -> text) and Vision (figure -> description) are
independent capabilities sharing only OpenAICompatProvider.describe_image.
"""

OCR_PROVIDERS = ("gemini", "easyocr", "disabled")

FA_OCR_PROMPT = (
    "Transcribe all visible text in this page image exactly as written, "
    "in Persian. Return only the transcription, no commentary."
)


def easyocr_ocr_pages(pdf_path: str, empty_pages: set[int],
                      langs: list[str]) -> dict[int, str]:
    """Docling + EasyOCR refill for OCR-blind pages. Returns {page_idx: text}."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import EasyOcrOptions, PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = True
    opts.force_backend_text = False
    opts.ocr_options = EasyOcrOptions(lang=langs or ["fa"], download_enabled=True)
    conv = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    res = conv.convert(pdf_path)
    out: dict[int, str] = {}
    for item, _level in res.document.iterate_items():
        text = (getattr(item, "text", None) or "").strip()
        if not text:
            continue
        page_no = None
        try:
            prov = getattr(item, "prov", None) or []
            if prov:
                page_no = getattr(prov[0], "page_no", None)
        except Exception:
            page_no = None
        if page_no is None:
            continue
        idx = int(page_no) - 1
        if idx in empty_pages:
            out[idx] = (out.get(idx, "") + "\n" + text).strip()
    return out


def gemini_ocr_pages(pdf_bytes: bytes, empty_pages: set[int],
                     model: str | None = None) -> dict[int, str]:
    """Rasterize pages with pypdfium2, transcribe via gapgpt-compatible API.

    `model` is a GapGPT-routed model id (e.g. a gemini-* name from the
    /models list); None falls back to CHAT_MODEL inside describe_image.
    """
    import pypdfium2 as pdfium

    from app.ai.openai_compat import OpenAICompatProvider

    try:
        doc = pdfium.PdfDocument(pdf_bytes)
        n = len(doc)
    except Exception as exc:
        raise RuntimeError(f"Gemini OCR rasterize failed: {exc}") from exc
    provider = OpenAICompatProvider()
    out: dict[int, str] = {}
    for idx in sorted(empty_pages):
        if idx < 0 or idx >= n:
            continue
        try:
            page = doc[idx]
            bitmap = page.render(scale=2.0)
            pil = bitmap.to_pil()
            import io as _io

            buf = _io.BytesIO()
            pil.save(buf, format="PNG")
            text = provider.describe_image(buf.getvalue(), FA_OCR_PROMPT,
                                           model=model,
                                           max_tokens=3000) or ""
        except Exception as exc:
            raise RuntimeError(
                f"Gemini OCR page {idx + 1} failed: {type(exc).__name__}: {exc}"
            ) from exc
        if text.strip():
            out[idx] = text.strip()
    try:
        doc.close()
    except Exception:
        pass
    return out


def ocr_pages(pdf_path: str, pdf_bytes: bytes, empty_pages: set[int],
              provider: str, langs: list[str],
              model: str | None = None) -> dict[int, str]:
    """Single entry point. 'disabled'/unknown provider -> {} (caller flags).

    `model` only applies to the "gemini" (GapGPT-routed) path; easyocr
    ignores it.
    """
    provider = (provider or "disabled").strip().lower()
    if provider not in OCR_PROVIDERS:
        raise ValueError(f"unknown OCR provider: {provider!r}")
    if provider == "disabled" or not empty_pages:
        return {}
    if provider == "gemini":
        return gemini_ocr_pages(pdf_bytes, empty_pages, model=model)
    return easyocr_ocr_pages(pdf_path, empty_pages, langs)
