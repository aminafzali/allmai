"""Plain-text structuring for txt/md/note content.

Splits flat text into heading/paragraph/list blocks with the section chain
(section/parent_id/document_order) that chunking/RAG consumes. The same
block semantics are reused by the Gemini structured PDF parser
(`gemini_structure.py`), which adds markdown-table blocks on top.
"""

import re

from app.knowledge.parsers.base import ParsedDocument, ParsedElement

# Conservative heading/list detection for text items (precision first:
# a missed heading only loses section context; a false heading poisons
# every following element's section).
_HEADING_MAX_CHARS = 100
_HEADING_MAX_WORDS = 12
_HEADING_BAD_END = (".", "!", "?", "؟", "…", ":", "؛", ",", "،", ";")
_LIST_MARK_RE = re.compile(r"^\s*(?:[-*\u2022\u2013\u2014]|\d{1,3}[.)\u2013-])\s+")
_ATX_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def _strip_atx(line: str) -> tuple[int, str] | None:
    m = _ATX_RE.match(line.strip())
    if not m:
        return None
    return len(m.group(1)), m.group(2).strip()


def _looks_like_heading(line: str) -> bool:
    t = line.strip()
    if not t or len(t) > _HEADING_MAX_CHARS or "\n" in t:
        return False
    if t.startswith("["):
        # degraded failure markers / citations are content, never headings
        return False
    if _LIST_MARK_RE.match(t):
        return False
    if t[-1] in _HEADING_BAD_END:
        return False
    if len(t.split()) > _HEADING_MAX_WORDS:
        return False
    return True


def _looks_like_list(block: str) -> bool:
    lines = [ln for ln in block.split("\n") if ln.strip()]
    if not lines:
        return False
    marked = sum(1 for ln in lines if _LIST_MARK_RE.match(ln))
    return marked >= 1 and marked >= len(lines) / 2


def _split_text_blocks(text: str) -> list[tuple[str, str]]:
    """Split a text item into (kind, clean_text) blocks.

    kinds: heading | list | paragraph. Markdown ATX headings always win;
    otherwise short single-line non-terminal blocks become headings,
    list-marked blocks become lists, everything else stays paragraph.
    """
    blocks: list[tuple[str, str]] = []
    for raw in str(text or "").split("\n\n"):
        chunk = raw.strip("\n ")
        if not chunk.strip():
            continue
        first = chunk.strip().split("\n", 1)[0].strip()
        atx = _strip_atx(first)
        if atx:
            blocks.append(("heading", atx[1]))
            rest = "\n".join(chunk.strip().split("\n")[1:]).strip()
            if rest:
                if _looks_like_list(rest):
                    blocks.append(("list", rest))
                elif "\n" not in rest and _looks_like_heading(rest):
                    blocks.append(("heading", rest))
                else:
                    blocks.append(("paragraph", rest))
            continue
        if _looks_like_list(chunk):
            blocks.append(("list", chunk.strip()))
            continue
        if "\n" not in chunk.strip() and _looks_like_heading(chunk):
            blocks.append(("heading", chunk.strip()))
            continue
        blocks.append(("paragraph", chunk.strip()))
    return blocks or ([("paragraph", str(text or "").strip())]
                      if str(text or "").strip() else [])


def structure_plain_text(text: str, title: str = "") -> ParsedDocument:
    """Lightweight heading/paragraph/list parser for txt/md/note content.

    Same section/parent_id/document_order contract the chunking/RAG path
    consumes downstream.
    """
    doc = ParsedDocument(title=title or "")
    order = 0
    section = ""
    section_eid = ""
    for bkind, btext in _split_text_blocks(text):
        if not btext:
            continue
        order += 1
        eid = f"e{order}"
        if bkind == "heading":
            meta = {"element_id": eid, "reading_order": order,
                    "kind": "heading", "section": section,
                    "parent_id": section_eid, "document_order": order}
            doc.elements.append(ParsedElement(
                element_id=eid, kind="heading", page_no=None,
                reading_order=order, text_markdown=btext, metadata=meta))
            section = btext
            section_eid = eid
            continue
        kind = "list" if bkind == "list" else "paragraph"
        meta = {"element_id": eid, "reading_order": order, "kind": kind,
                "section": section, "parent_id": section_eid,
                "document_order": order}
        doc.elements.append(ParsedElement(
            element_id=eid, kind=kind, page_no=None,
            reading_order=order, text_markdown=btext, metadata=meta))
    return doc
