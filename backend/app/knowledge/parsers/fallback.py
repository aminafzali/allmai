"""Local fallback parsers: deterministic, dependency-light, always available.

Covers pdf/docx/pptx/txt/md/image/url/note. Richer parsing (RAG-Anything,
Docling) plugs in via ParserPort and is selected in parsers/__init__.py.
"""

import io
import json

from app.knowledge.parsers.base import ParsedDocument, ParsedPage


def parse_pdf(blob: bytes) -> ParsedDocument:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(blob))
    pages = []
    for i, page in enumerate(reader.pages):
        pages.append(ParsedPage(page_no=i + 1, text=(page.extract_text() or "").strip()))
    return ParsedDocument(pages=pages, page_count=len(pages))


def parse_docx(blob: bytes) -> ParsedDocument:
    from docx import Document as DocxDocument

    doc = DocxDocument(io.BytesIO(blob))
    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    return ParsedDocument(pages=[ParsedPage(text=text.strip())], page_count=0)


def parse_pptx(blob: bytes) -> ParsedDocument:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(blob))
    pages = []
    for i, slide in enumerate(prs.slides):
        parts = [shape.text for shape in slide.shapes if shape.has_text_frame and shape.text.strip()]
        pages.append(ParsedPage(page_no=i + 1, text="\n".join(parts).strip()))
    return ParsedDocument(pages=pages, page_count=len(pages))


def parse_text(blob: bytes) -> ParsedDocument:
    return ParsedDocument(pages=[ParsedPage(text=blob.decode("utf-8", errors="replace").strip())])


def parse_url_descriptor(blob: bytes, fetch_fn=None) -> ParsedDocument:
    """Live fetch first (full page text); descriptor fallback on failure."""
    from app.knowledge.parsers.url import fetch_url_text

    data = json.loads(blob.decode("utf-8"))
    title = str(data.get("title") or data.get("url") or "url")
    url = str(data.get("url") or "")
    fetch = fetch_fn or fetch_url_text
    if url:
        try:
            text = fetch(url)
            if text and text.strip():
                return ParsedDocument(title=title, pages=[ParsedPage(text=text.strip())])
        except Exception:
            pass
    text = f"{title}\n{url}".strip()
    return ParsedDocument(title=title, pages=[ParsedPage(text=text)])


def parse_note_descriptor(blob: bytes) -> ParsedDocument:
    data = json.loads(blob.decode("utf-8"))
    title = str(data.get("title") or "note")
    return ParsedDocument(title=title, pages=[ParsedPage(text=str(data.get("content", "")).strip())])


def parse_image_meta(blob: bytes, filename: str, describe_fn=None) -> ParsedDocument:
    """Image dimensions via pillow; visual description via `describe_fn`
    (GapGPT vision in production, mocked in tests). Never fails the job:
    worst case the segment carries metadata only."""
    meta = ""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(blob)) as im:
            meta = f"[image {im.width}x{im.height} {im.format or ''} {filename}]".strip()
    except Exception:
        meta = f"[image {filename}]"
    description = ""
    if describe_fn is not None:
        try:
            description = (describe_fn(blob) or "").strip()
        except Exception:
            description = ""
    text = "\n".join(p for p in (meta, description) if p)
    return ParsedDocument(title=filename, pages=[ParsedPage(text=text)])


FALLBACK = {
    "pdf": parse_pdf,
    "docx": parse_docx,
    "pptx": parse_pptx,
    "txt": parse_text,
    "md": parse_text,
    "url": parse_url_descriptor,
    "note": parse_note_descriptor,
}
