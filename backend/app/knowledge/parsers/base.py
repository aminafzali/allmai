"""Parser ports. RAG-Anything lives ONLY behind ``ParserPort``.

Phase 2: the parser subsystem (get_parser("docling") -> parse_document)
produces a content_list that ``normalize`` turns into ``ParsedDocument``
with ``ParsedElement``s (text/table/image/figure/equation + page/bbox).
Full-engine paths (RAGAnything class, LightRAG, ainsert, aquery,
process_document_complete, insert_content_list) are FORBIDDEN in app code
(enforced by tests/test_architecture.py).
"""

from dataclasses import dataclass, field
from typing import Protocol

from app.knowledge.models import AUDIO_EXTENSIONS, SOURCE_TYPES

# Structured element kinds (Phase 2 Document Core).
ELEMENT_KINDS = ("text", "heading", "table", "image", "figure", "equation")


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
    # Phase 2: parse provenance, e.g. {"parser": "docling-fa-refill",
    # "ocr_degraded": True, "ocr_empty_pages": [3]}.
    parse_meta: dict = field(default_factory=dict)


class ParserPort(Protocol):
    def parse(self, source_type: str, filename: str, blob: bytes) -> ParsedDocument:
        ...


def validate_source_type(source_type: str, filename: str = "") -> str:
    """Phase-0 guard: reject video and unknown types loudly."""
    st = source_type.lower().strip()
    if st == "video":
        raise UnsupportedSourceError(
            "video sources are reserved for the future and not processed in MVP."
        )
    if st not in SOURCE_TYPES:
        raise UnsupportedSourceError(f"unsupported source type: {source_type!r}")
    if st == "audio" and filename:
        name = filename.lower()
        if not name.endswith(AUDIO_EXTENSIONS):
            raise UnsupportedSourceError(
                f"audio MVP supports {', '.join(AUDIO_EXTENSIONS)}; got {filename!r}"
            )
    return st
