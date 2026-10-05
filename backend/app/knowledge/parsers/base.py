"""Parser ports. Every extractor returns a ``ParsedDocument`` with
``ParsedElement``s (text/heading/paragraph/list/table/image/figure/
equation + page/section chain) that chunking/RAG consumes.

Heavy vendor engines (RAG-Anything, LightRAG, Docling, Zep) are FORBIDDEN
in app code (enforced by tests/test_architecture.py): extraction is our
own structured pipeline (Gemini Flash via GapGPT for PDFs, deterministic
local parsers for the rest) over PostgreSQL + object storage.
"""

from dataclasses import dataclass, field
from typing import Protocol

from app.knowledge.models import AUDIO_EXTENSIONS, SOURCE_TYPES

# Structured element kinds (Phase 2 Document Core + P0 section chain).
ELEMENT_KINDS = ("text", "heading", "paragraph", "list", "list_item",
                 "table", "image", "figure", "equation")


class UnsupportedSourceError(ValueError):
    pass


@dataclass
class ParsedPage:
    page_no: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    text: str = ""


@dataclass
class ParsedElement:
    """One structured unit: text/table/image/figure/equation.

    ``text_markdown`` is what gets chunked/embedded (markdown tables,
    LaTeX equations, caption placeholders for images). ``blob`` carries
    raw image bytes for figures (persisted to StorageProvider by the
    worker; never to PostgreSQL). ``metadata`` lands in
    ``Chunk.chunk_metadata`` (kind/element_id/page/caption/bbox/...).
    """

    element_id: str = ""
    kind: str = "text"
    page_no: int | None = None
    reading_order: int = 0
    bbox: dict | None = None
    text_markdown: str = ""
    blob: bytes | None = None
    caption: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class ParsedDocument:
    title: str = ""
    pages: list[ParsedPage] = field(default_factory=list)
    page_count: int = 0
    duration_ms: int = 0
    # Phase 2: structured elements (empty for legacy fallback parsers).
    elements: list[ParsedElement] = field(default_factory=list)
    # Parse provenance, e.g. {"parser": "gemini-structured",
    # "ocr_degraded": True, "ocr_empty_pages": [3]}.
    parse_meta: dict = field(default_factory=dict)
    # Structured payloads for the worker (NOT embedded): e.g. Excel
    # SheetInfo list under extra["excel_sheets"] for the DuckDB build.
    extra: dict = field(default_factory=dict)


class ParserPort(Protocol):
    def parse(self, source_type: str, filename: str, blob: bytes) -> ParsedDocument:
        ...


def validate_source_type(source_type: str, filename: str = "") -> str:
    """Guard unknown types loudly. Video = audio-track transcription only
    (no frame analysis); excel = .xlsx workbooks; csv = .csv files."""
    from app.knowledge.models import (
        CSV_EXTENSIONS,
        EXCEL_EXTENSIONS,
        VIDEO_EXTENSIONS,
    )

    st = source_type.lower().strip()
    if st not in SOURCE_TYPES:
        raise UnsupportedSourceError(f"unsupported source type: {source_type!r}")
    if st == "audio" and filename:
        name = filename.lower()
        if not name.endswith(AUDIO_EXTENSIONS):
            raise UnsupportedSourceError(
                f"audio MVP supports {', '.join(AUDIO_EXTENSIONS)}; got {filename!r}"
            )
    if st == "video" and filename:
        name = filename.lower()
        if not name.endswith(VIDEO_EXTENSIONS):
            raise UnsupportedSourceError(
                f"video supports {', '.join(VIDEO_EXTENSIONS)}; got {filename!r}"
            )
    if st == "excel" and filename:
        name = filename.lower()
        if not name.endswith(EXCEL_EXTENSIONS):
            raise UnsupportedSourceError(
                f"excel supports {', '.join(EXCEL_EXTENSIONS)}; got {filename!r}"
            )
    if st == "csv" and filename:
        name = filename.lower()
        if not name.endswith(CSV_EXTENSIONS):
            raise UnsupportedSourceError(
                f"csv supports {', '.join(CSV_EXTENSIONS)}; got {filename!r}"
            )
    return st
