"""Structured PDF extraction with Gemini Flash via GapGPT (Docling removed).

This is NOT raw OCR: Gemini returns the document's logical structure —
headings, sections/subsections, paragraphs, lists and markdown tables —
in reading order with page numbers, so the output feeds chunking/RAG
directly with parent-section linkage intact.

Hybrid strategy (cost-bounded for big books):
- Text-rich pages (pypdf text layer): grouped into small batches
  (GEMINI_STRUCT_PAGES_PER_CALL); ONE chat call per batch turns flat
  text into structured markdown with `[PAGE n]` markers.
- Textless pages (scanned): rasterized with pypdfium2 and structured
  with ONE vision call per page (same GapGPT endpoint, no Google key).
- Pages walk in document order with the section chain threaded through,
  so a subsection keeps its parent section even across batch/vision
  boundaries (best-effort: the section NAME always survives).

Failure policy: per-batch/per-page failures degrade loudly (failed lists
in parse_meta + flat structuring of the raw text for that page), never
silently. Total failure (zero content) raises RuntimeError so the
dispatcher falls through to the flat pypdf fallback.

Test seams: `generate_fn`/`vision_fn` injectables. Each may return either
a plain string (legacy describe-style fakes) or a (text, usage) tuple;
usage accumulates into parse_meta["usage"] for the P3 usage ledger.
"""

from __future__ import annotations

import re
import time

from app.knowledge.parsers.base import ParsedDocument, ParsedElement, ParsedPage
from app.knowledge.parsers.normalize import _split_text_blocks

PAGE_MARK_RE = re.compile(r"^\[PAGE\s+(\d+)\]\s*$")

# A table block = 2+ consecutive lines starting with '|' (markdown tables
# as Gemini emits them). Single stray pipe-lines stay plain paragraphs.
_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:\-|]+\|\s*$")

# Per-page input cap (chars): bounds a single pathological page; counted
# in parse_meta, never silently cut.
_PAGE_INPUT_CAP = 12_000

STRUCT_PROMPT = """ساختار منطقی صفحات زیر از یک سند را استخراج کن. متن کامل را حفظ کن؛ خلاصه‌سازی نکن؛ هیچ عدد، نام یا تاریخ را تغییر نده و چیزی اضافه نکن.

قواعد خروجی (فقط همین را برگردان، بدون توضیح اضافه):
- برای هر صفحه دقیقاً با خط `[PAGE n]` (با همان شماره داده‌شده) شروع کن و محتوای همان صفحه را زیرش بنویس.
- تیترها و عنوان فصل/بخش‌ها را با `##` یا `###` مشخص کن.
- جدول‌ها را به شکل جدول مارک‌داون با سطر سرستون بنویس و اگر عنوان/کپشن دارند بالای جدول نگه دار.
- فهرست‌ها را با `-` یا شماره حفظ کن؛ بقیه متن پاراگراف.
- ترتیب مطالب دقیقاً حفظ شود.
{section_hint}"""

VISION_STRUCT_PROMPT = """ساختار منطقی این صفحه سند را استخراج کن. متن کامل را حفظ کن؛ خلاصه‌سازی نکن؛ هیچ عدد، نام یا تاریخ را تغییر نده و چیزی حدس نزن.

قواعد خروجی (فقط همین را برگردان، بدون توضیح اضافه):
- تیترها و عنوان فصل/بخش‌ها را با `##` یا `###` مشخص کن.
- جدول‌ها را به شکل جدول مارک‌داون با سطر سرستون بنویس و اگر عنوان/کپشن دارند بالای جدول نگه دار.
- فهرست‌ها را با `-` یا شماره حفظ کن؛ بقیه متن پاراگراف.
- ترتیب مطالب دقیقاً حفظ شود.
{section_hint}"""


def _section_hint(section: str) -> str:
    section = (section or "").strip()
    if not section:
        return ""
    return (f"- این صفحات ادامه بخش «{section[:200]}» هستند، مگر اینکه تیتر "
            f"جدیدی در متن دیده شود.\n")


def _page_texts(blob: bytes) -> list[str]:
    """Per-page raw text via pypdf (text layer only, no model calls)."""
    import io as _io

    from pypdf import PdfReader

    try:
        reader = PdfReader(_io.BytesIO(blob))
        return [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:
        raise RuntimeError(f"Gemini PDF pre-read failed: {exc}") from exc


def _scan_threshold() -> int:
    try:
        from app.core.config import get_settings

        return max(1, int(get_settings().SCAN_TEXT_THRESHOLD))
    except (TypeError, ValueError):
        return 20


def _resolve_model(explicit: str | None) -> str | None:
    """GapGPT-routed Gemini id: explicit arg wins, else the admin-set
    `extraction.pdf` model, else None (provider default CHAT_MODEL)."""
    if (explicit or "").strip():
        return str(explicit).strip()
    try:
        from app.ai.settings import get_setting

        model = (get_setting(None, "extraction.pdf") or {}).get("model")
        if (model or "").strip():
            return str(model).strip()
    except Exception:
        pass
    return None


def build_struct_prompt(pages: list[tuple[int, str]], section: str = "") -> str:
    """One batch prompt: pages prefixed with [PAGE n] markers."""
    parts = [STRUCT_PROMPT.format(section_hint=_section_hint(section))]
    for page_no, text in pages:
        parts.append(f"[PAGE {page_no}]\n{(text or '').strip()}")
    return "\n\n".join(parts).strip() + "\n"


def split_structured_response(text: str, want_pages: list[int]) -> dict[int, str]:
    """Split a batch response by [PAGE n] markers. Missing pages map to ""
    so the caller falls back to flat text for exactly those pages."""
    got: dict[int, str] = {}
    cur: int | None = None
    buf: list[str] = []
    body = str(text or "").strip()
    # Tolerate fenced replies (```markdown ... ```).
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\s*\n?", "", body)
        body = re.sub(r"\n?```\s*$", "", body)
    for line in body.splitlines():
        m = PAGE_MARK_RE.match(line.strip())
        if m:
            if cur is not None:
                got[cur] = "\n".join(buf).strip()
            cur = int(m.group(1))
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        got[cur] = "\n".join(buf).strip()
    return {p: got.get(p, "") for p in want_pages}


def _split_table_blocks(text: str) -> list[tuple[str, str]]:
    """Split page markdown into ("table"|"text", block) preserving order."""
    out: list[tuple[str, str]] = []
    buf_text: list[str] = []
    buf_table: list[str] = []

    def _flush_text() -> None:
        if buf_text:
            out.append(("text", "\n".join(buf_text)))
            buf_text.clear()

    def _flush_table() -> None:
        # A lone pipe-line is not a table (avoids false tables).
        block = "\n".join(buf_table)
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if len(lines) >= 2 and (
                len(lines) >= 3 or (len(lines) > 1 and _TABLE_SEP_RE.match(lines[1]))):
            out.append(("table", block))
        else:
            _flush_text()
            out.append(("text", block))
        buf_table.clear()

    for line in str(text or "").split("\n"):
        if _TABLE_LINE_RE.match(line):
            if buf_text:
                _flush_text()
            buf_table.append(line)
        else:
            if buf_table:
                _flush_table()
            buf_text.append(line)
    if buf_table:
        _flush_table()
    _flush_text()
    return out


def _table_shape(block: str) -> tuple[int, int]:
    lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
    body = [ln for i, ln in enumerate(lines)
            if not (i == 1 and _TABLE_SEP_RE.match(ln))]
    rows = len(body)
    cols = max((ln.count("|") - 1 for ln in body), default=0)
    return max(rows, 0), max(cols, 0)


class _ElementBuilder:
    """Accumulates page markdown into section-chained ParsedElements."""

    def __init__(self) -> None:
        self.order = 0
        self.section = ""
        self.section_eid = ""
        self.elements: list[ParsedElement] = []

    def _next_eid(self) -> str:
        self.order += 1
        return f"e{self.order}"

    def _stamp(self, meta: dict) -> dict:
        meta["section"] = self.section
        meta["parent_id"] = self.section_eid
        meta["document_order"] = self.order
        return meta

    def _emit_heading(self, text: str, page_no: int | None) -> None:
        eid = self._next_eid()
        meta = self._stamp({"element_id": eid, "reading_order": self.order,
                            "kind": "heading"})
        self.elements.append(ParsedElement(
            element_id=eid, kind="heading", page_no=page_no,
            reading_order=self.order, text_markdown=text.strip(),
            metadata=meta))
        self.section = text.strip()
        self.section_eid = eid

    def add_page(self, page_no: int | None, markdown: str) -> None:
        for kind, block in _split_table_blocks(markdown):
            block = (block or "").strip()
            if not block:
                continue
            if kind == "table":
                rows, cols = _table_shape(block)
                eid = self._next_eid()
                meta = self._stamp({"element_id": eid,
                                    "reading_order": self.order,
                                    "kind": "table", "rows": rows, "cols": cols,
                                    "caption": ""})
                self.elements.append(ParsedElement(
                    element_id=eid, kind="table", page_no=page_no,
                    reading_order=self.order, text_markdown=block,
                    metadata=meta))
                continue
            for bkind, btext in _split_text_blocks(block):
                if not btext:
                    continue
                if bkind == "heading":
                    self._emit_heading(btext, page_no)
                    continue
                ekind = "list" if bkind == "list" else "paragraph"
                eid = self._next_eid()
                meta = self._stamp({"element_id": eid,
                                    "reading_order": self.order,
                                    "kind": ekind})
                self.elements.append(ParsedElement(
                    element_id=eid, kind=ekind, page_no=page_no,
                    reading_order=self.order, text_markdown=btext,
                    metadata=meta))

    def document(self, title: str, page_count: int) -> ParsedDocument:
        doc = ParsedDocument(title=title or "")
        doc.elements = self.elements
        by_page: dict[int, list[str]] = {}
        for el in self.elements:
            if el.page_no is not None:
                by_page.setdefault(el.page_no, []).append(el.text_markdown)
        doc.pages = [ParsedPage(page_no=p, text="\n\n".join(by_page[p]))
                     for p in sorted(by_page)]
        doc.page_count = page_count
        return doc


def rasterize_pages(blob: bytes, indices: set[int],
                    scale: float = 2.0) -> dict[int, bytes]:
    """Render selected 0-based pages to PNG bytes (pypdfium2)."""
    import io as _io

    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(blob)
        n = len(pdf)
    except Exception as exc:
        raise RuntimeError(f"Gemini struct rasterize failed: {exc}") from exc
    out: dict[int, bytes] = {}
    try:
        for idx in sorted(indices):
            if idx < 0 or idx >= n:
                continue
            try:
                pil = pdf[idx].render(scale=scale).to_pil()
                buf = _io.BytesIO()
                pil.save(buf, format="PNG")
                out[idx] = buf.getvalue()
            except Exception as exc:
                raise RuntimeError(
                    f"Gemini struct rasterize page {idx + 1} failed: "
                    f"{type(exc).__name__}: {exc}") from exc
    finally:
        try:
            pdf.close()
        except Exception:
            pass
    return out


def _default_generate(prompt: str, model: str | None, max_tokens: int,
                      timeout: float) -> tuple[str, dict]:
    from app.ai.openai_compat import OpenAICompatProvider

    return OpenAICompatProvider().generate_with_usage(
        prompt, model=model, max_tokens=max_tokens, temperature=0.1,
        timeout=timeout)


def _default_vision(image_bytes: bytes, prompt: str, model: str | None,
                    max_tokens: int, timeout: float) -> tuple[str, dict]:
    from app.ai.openai_compat import OpenAICompatProvider

    return OpenAICompatProvider().describe_image_with_usage(
        image_bytes, prompt, model=model, max_tokens=max_tokens,
        temperature=0.1, timeout=timeout)


def _call(fn, *args, **kwargs) -> tuple[str, dict]:
    """Call a model seam accepting str or (text, usage) returns."""
    out = fn(*args, **kwargs)
    if isinstance(out, (list, tuple)) and len(out) == 2 \
            and isinstance(out[1], dict):
        return str(out[0] or ""), out[1]
    return str(out or ""), {}


def _add_usage(acc: dict, usage: dict) -> None:
    try:
        acc["prompt_tokens"] += int((usage or {}).get("prompt_tokens") or 0)
    except (TypeError, ValueError):
        pass
    try:
        acc["completion_tokens"] += int((usage or {}).get("completion_tokens") or 0)
    except (TypeError, ValueError):
        pass
    acc["calls"] += 1
    acc["total_tokens"] = acc["prompt_tokens"] + acc["completion_tokens"]


class GeminiStructuredParser:
    """Hybrid structured PDF extraction (text batches + vision pages)."""

    def parse(self, source_type: str, filename: str, blob: bytes,
              model: str | None = None,
              generate_fn=None, vision_fn=None,
              max_pages_per_call: int | None = None,
              timeout: float | None = None) -> ParsedDocument:
        t0 = time.perf_counter()
        st = (source_type or "").lower().strip()
        if st != "pdf":
            raise ValueError(f"GeminiStructuredParser supports pdf, got {source_type!r}")
        if not (blob or b""):
            raise RuntimeError("Gemini structured extraction got empty blob")
        from app.core.config import get_settings

        s = get_settings()
        per_call = max(1, int(max_pages_per_call or s.GEMINI_STRUCT_PAGES_PER_CALL))
        wait = float(timeout or s.GEMINI_STRUCT_TIMEOUT_S)
        max_pages = max(1, int(get_settings().GEMINI_STRUCT_MAX_PAGES))
        model = _resolve_model(model)
        gen = generate_fn or _default_generate
        vis = vision_fn or _default_vision

        texts = _page_texts(blob)
        if not texts:
            raise RuntimeError("Gemini structured extraction: PDF has no pages")
        if len(texts) > max_pages:
            raise RuntimeError(
                f"Gemini structured extraction refused: {len(texts)} pages "
                f"exceeds GEMINI_STRUCT_MAX_PAGES={max_pages}")

        # Ordered work items: consecutive rich runs (batched) + vision pages.
        items: list[tuple[str, list[int]]] = []  # ("text", idxs) | ("vision", [idx])
        run: list[int] = []
        for i, text in enumerate(texts):
            if (text or "").strip():
                run.append(i)
                if len(run) >= per_call:
                    items.append(("text", run))
                    run = []
            else:
                if run:
                    items.append(("text", run))
                    run = []
                items.append(("vision", [i]))
        if run:
            items.append(("text", run))

        builder = _ElementBuilder()
        usage = {"model": model or "default", "calls": 0,
                 "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        failed_batches: list[list[int]] = []
        failed_vision: list[int] = []
        truncated: list[int] = []
        vision_done = 0

        for kind, idxs in items:
            if kind == "vision":
                idx = idxs[0]
                page_no = idx + 1
                try:
                    images = rasterize_pages(blob, {idx})
                    png = images.get(idx)
                    if not png:
                        raise RuntimeError(f"page {page_no} rendered empty")
                    prompt = VISION_STRUCT_PROMPT.format(
                        section_hint=_section_hint(builder.section))
                    text, use = _call(vis, png, prompt, model,
                                      3000, wait)
                    _add_usage(usage, use)
                    if not (text or "").strip():
                        raise RuntimeError(f"page {page_no} vision empty")
                    builder.add_page(page_no, text)
                    vision_done += 1
                except Exception:
                    failed_vision.append(page_no)
                continue
            # text batch (page numbers are 1-based in markers/prompts)
            batch_pages: list[tuple[int, str]] = []
            for idx in idxs:
                raw = texts[idx] or ""
                if len(raw) > _PAGE_INPUT_CAP:
                    raw = raw[:_PAGE_INPUT_CAP] + "\n…(truncated)"
                    truncated.append(idx + 1)
                batch_pages.append((idx + 1, raw))
            try:
                prompt = build_struct_prompt(batch_pages, builder.section)
                text, use = _call(gen, prompt, model,
                                  max(4000, 1500 * len(batch_pages)), wait)
                _add_usage(usage, use)
                parts = split_structured_response(
                    text, [p for p, _ in batch_pages])
                if not any(v.strip() for v in parts.values()):
                    raise RuntimeError("batch response held no page content")
                for page_no, _ in batch_pages:
                    md = (parts.get(page_no) or "").strip()
                    if md:
                        builder.add_page(page_no, md)
                    else:
                        # Marker missing for this page: flat-structure the
                        # raw text (degraded but order/page preserved).
                        raw = dict(batch_pages)[page_no]
                        builder.add_page(page_no, raw)
                        failed_batches.append([page_no])
            except Exception:
                for page_no, raw in batch_pages:
                    builder.add_page(page_no, raw)
                failed_batches.append([p for p, _ in batch_pages])

        doc = builder.document(filename, len(texts))
        if not doc.elements:
            raise RuntimeError(
                "Gemini structured extraction produced no content")
        meta: dict = {
            "parser": "gemini-structured",
            "model": model or "default",
            "pages": len(texts),
            "text_batches": sum(1 for k, _ in items if k == "text"),
            "vision_pages": sorted(
                idx + 1 for k, idxs in items for idx in idxs if k == "vision"),
            "vision_done": vision_done,
            "usage": usage,
            "seconds": round(time.perf_counter() - t0, 1),
        }
        if failed_batches:
            meta["failed_batches"] = failed_batches
            meta["degraded"] = True
        if failed_vision:
            meta["failed_vision_pages"] = sorted(failed_vision)
            meta["degraded"] = True
        if truncated:
            meta["truncated_pages"] = sorted(truncated)
        doc.parse_meta = meta
        return doc
