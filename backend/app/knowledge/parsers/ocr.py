"""OCR provider router (page-level refill for textless PDF pages).

Exactly ONE provider handles OCR per ingestion:
- "gemini": rasterize pages with pypdfium2, transcribe each via the SAME
  OpenAI-compatible integration (gapgpt.app) used everywhere else.
  The model comes from the `ocr.provider` ai_setting (`model` field,
  GapGPT-routed Gemini id) and falls back to CHAT_MODEL when unset —
  never a direct Google call.
- "disabled" (SAFE DEFAULT until the admin chooses in the
  panel):
  no OCR at all; pages stay empty and the caller flags degraded.

OCR (page/image -> text) and Vision (figure -> description) are
independent capabilities sharing only OpenAICompatProvider.describe_image.
"""

OCR_PROVIDERS = ("gemini", "disabled")

FA_OCR_PROMPT = (
    "Transcribe all visible text in this page image exactly as written, "
    "in Persian. Return only the transcription, no commentary."
)

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
    """Single entry point. 'disabled' -> {} (caller flags degraded).

    `model` only applies to the "gemini" (GapGPT-routed) path.
    `pdf_path`/`langs` are kept for signature compatibility and ignored.
    """
    provider = (provider or "disabled").strip().lower()
    if provider not in OCR_PROVIDERS:
        raise ValueError(f"unknown OCR provider: {provider!r}")
    if provider == "disabled" or not empty_pages:
        return {}
    return gemini_ocr_pages(pdf_bytes, empty_pages, model=model)
