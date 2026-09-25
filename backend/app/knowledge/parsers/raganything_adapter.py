"""RAG-Anything parser-subsystem adapter (Phase 2, parse-only).

Verified against raganything 1.4.1 source (spike):
- ``get_parser("docling")`` -> ``DoclingParser`` (parser registry).
- ``DoclingParser.parse_document(file_path, method="auto",
  output_dir=..., lang=...) -> List[Dict]`` with item schemas:
  text{table_body...} (see parsers/normalize.py for the full contract).
- The parser writes a Docling export dict
  (``<out>/<stem>/docling/<stem>.json``) whose blocks carry the REAL
  ``prov[].page_no`` — read BEFORE cleanup to fix the content_list
  page heuristic (P1: every item arrived with page_idx=0).

HARD BOUNDARIES (locked, enforced by tests/test_architecture.py):
- Only ``raganything.parser`` is ever imported, and only here.
- NEVER: RAGAnything engine class, LightRAG instantiation/storage,
  ainsert/insert_content_list/process_document_complete/aquery.
- No embedding and no LLM/vision calls happen on this path: no model
  callbacks are constructed at all.
- Parser output_dir artifacts are temporary and removed in ``finally``.

OCR routing (Phase 2 top-up, admin-selected, mutually exclusive):
- provider "easyocr": Docling + EasyOCR refill of text-empty pages
  (Persian 'fa' mandatory). Only this path imports docling/easyocr.
- provider "gemini": rasterize + gapgpt-compatible transcription.
  No docling/easyocr imports on this path.
- provider "disabled" (SAFE DEFAULT): no OCR; empty pages are flagged
  degraded, never treated as successful-empty.
"""

import glob
import json
import os
import shutil
import tempfile

from app.knowledge.parsers.base import ParsedDocument
from app.knowledge.parsers.normalize import (
    normalize_content_list,
    page_map_from_docling_dict,
)


def _text_chars(blob: bytes) -> tuple[int, int]:
    """Cheap text-layer probe: (non-whitespace chars, page count) via pypdf."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(__import__("io").BytesIO(blob))
        total = 0
        for page in reader.pages:
            total += len("".join((page.extract_text() or "").split()))
        return total, len(reader.pages)
    except Exception:
        return 0, 0


def _scan_threshold() -> int:
    from app.core.config import get_settings

    try:
        return max(1, int(get_settings().SCAN_TEXT_THRESHOLD))
    except (TypeError, ValueError):
        return 20


def _read_docling_dict(out_dir: str) -> dict:
    found = sorted(glob.glob(os.path.join(out_dir, "*", "*", "docling", "*.json")))
    if not found:
        found = sorted(glob.glob(os.path.join(out_dir, "**", "*.json"), recursive=True))
    for path in found:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "body" in data:
                return data
        except (OSError, ValueError):
            continue
    return {}


class RAGAnythingParser:
    """Adapter implementing ParserPort via the RAG-Anything parser subsystem."""

    def parse(self, source_type: str, filename: str, blob: bytes,
              ocr_provider: str | None = None,
              ocr_model: str | None = None) -> ParsedDocument:
        try:
            from raganything.parser import get_parser
        except ImportError as exc:
            raise RuntimeError(
                "RAG-Anything is not installed; fallback parser handles MVP types."
            ) from exc
        provider = (ocr_provider or "disabled").strip().lower()
        tmp_in = None
        out_dir = None
        try:
            langs = self._ocr_langs()
            fd, tmp_in = tempfile.mkstemp(suffix=".pdf")
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
            # B: scanned early-detect — no usable text layer means the full
            # RA parse would only burn minutes; go straight to OCR.
            # Rule (configurable): avg chars/page < SCAN_TEXT_THRESHOLD.
            chars, npages = _text_chars(blob)
            out_dir = tempfile.mkdtemp(prefix="ra_parse_")
            if chars < _scan_threshold() * max(npages, 1):
                return self._parse_scanned(tmp_in, filename, blob, provider, langs,
                                            npages=npages, ocr_model=ocr_model)
            try:
                parser = get_parser("docling")
                items = parser.parse_document(tmp_in, method="auto", output_dir=out_dir)
            except Exception as exc:
                raise RuntimeError(f"RAG-Anything docling parse failed: {exc}") from exc
            page_map, unverified, page_map_ok = self._page_map(out_dir, items)
            doc = normalize_content_list(items, title=filename,
                                         page_map=page_map, unverified=unverified)
            parse_meta: dict = {"parser": "raganything-docling",
                                "ocr_provider": provider, "ocr_langs": langs}
            if ocr_model:
                parse_meta["ocr_model"] = ocr_model
            if not page_map_ok:
                parse_meta["page_map"] = "unavailable-fallback-page-idx"
            # Refill text-empty pages via the selected OCR provider.
            covered = self._covered_pages(items)
            empty = {p for p, ok in covered.items() if not ok}
            if empty:
                self._refill(doc, items, tmp_in, blob, empty, provider, langs,
                              parse_meta, ocr_model=ocr_model)
            doc.parse_meta = parse_meta
            return doc
        finally:
            try:
                if tmp_in and os.path.isfile(tmp_in):
                    os.unlink(tmp_in)
            except OSError:
                pass
            try:
                if out_dir and os.path.isdir(out_dir):
                    shutil.rmtree(out_dir, ignore_errors=True)
            except OSError:
                pass

    # ----- internals (unit-seams; pure where possible) -----

    @staticmethod
    def _page_map(out_dir: str, items: list) -> tuple[dict, set, bool]:
        doc_dict = _read_docling_dict(out_dir)
        if not doc_dict:
            return {}, set(), False
        page_map, unverified = page_map_from_docling_dict(doc_dict, items)
        return page_map, unverified, True

    @staticmethod
    def _covered_pages(items: list[dict]) -> dict[int, bool]:
        covered: dict[int, bool] = {}
        for it in items or []:
            if not isinstance(it, dict):
                continue
            txt = ""
            if it.get("type") == "table":
                txt = str(it.get("table_body", "") or "") + str(it.get("table_caption", "") or "")
            elif it.get("type") == "equation":
                txt = str(it.get("text", "") or "")
            elif it.get("type") == "image":
                txt = str(it.get("image_caption", it.get("img_caption", "")) or "")
            else:
                txt = str(it.get("text", "") or "")
            try:
                page = int(it.get("page_idx", 0))
            except (TypeError, ValueError):
                page = 0
            if txt.strip():
                covered[page] = True
            else:
                covered.setdefault(page, False)
        return covered

    def _refill(self, doc, items, tmp_in, blob, empty, provider, langs,
                parse_meta, ocr_model: str | None = None) -> None:
        from app.knowledge.parsers.ocr import ocr_pages

        if provider == "disabled":
            parse_meta.update({"ocr_degraded": True,
                               "ocr_empty_pages": sorted(empty),
                               "ocr_reason": "ocr-provider-disabled"})
            return
        try:
            refilled = ocr_pages(tmp_in, blob, empty, provider, langs,
                                 model=ocr_model)
        except Exception as exc:
            parse_meta.update({"ocr_refill_failed": f"{type(exc).__name__}: {exc}"[:200],
                               "ocr_degraded": True,
                               "ocr_empty_pages": sorted(empty)})
            return
        for idx, text in refilled.items():
            if text.strip():
                items.append({"type": "text", "text": text.strip(),
                              "page_idx": idx, "ocr_refill": True,
                              "ocr_provider": provider})
        still_empty = sorted(p for p in empty if not refilled.get(p, "").strip())
        if provider == "easyocr":
            parse_meta["parser"] = "raganything-docling+easyocr-fa-refill"
        else:
            parse_meta["parser"] = f"raganything-docling+{provider}-refill"
        parse_meta["ocr_refilled_pages"] = sorted(empty - set(still_empty))
        parse_meta["ocr_empty_pages"] = still_empty
        if still_empty:
            parse_meta["ocr_degraded"] = True
        # Re-normalize appended refill items into the SAME document.
        extra = normalize_content_list(
            [i for i in items if isinstance(i, dict) and i.get("ocr_refill")],
            title="")
        doc.elements.extend(extra.elements)
        by_page: dict[int, list[str]] = {}
        for el in doc.elements:
            if el.page_no is not None:
                by_page.setdefault(el.page_no, []).append(el.text_markdown)
        from app.knowledge.parsers.base import ParsedPage

        doc.pages = [ParsedPage(page_no=p, text="\n\n".join(by_page[p]))
                     for p in sorted(by_page)]
        doc.page_count = max(doc.page_count, max(by_page) if by_page else 0)

    def _parse_scanned(self, tmp_in, filename, blob, provider, langs,
                       npages: int = 0,
                       ocr_model: str | None = None) -> ParsedDocument:
        """Scanned PDF: skip the full RA parse, go straight to OCR."""
        from app.knowledge.parsers.ocr import ocr_pages

        if npages <= 0:
            try:
                from pypdf import PdfReader

                npages = len(PdfReader(__import__("io").BytesIO(blob)).pages)
            except Exception:
                npages = 0
        parse_meta: dict = {"parser": f"{provider}-ocr-direct",
                            "ocr_provider": provider, "ocr_langs": langs,
                            "scanned_early_detect": True}
        if ocr_model:
            parse_meta["ocr_model"] = ocr_model
        if provider == "disabled":
            parse_meta.update({"ocr_degraded": True,
                               "ocr_empty_pages": list(range(max(npages, 1))),
                               "ocr_reason": "ocr-provider-disabled"})
            doc = ParsedDocument(title=filename)
            doc.parse_meta = parse_meta
            return doc
        try:
            refilled = ocr_pages(tmp_in, blob, set(range(max(npages, 1))),
                                 provider, langs, model=ocr_model)
        except Exception as exc:
            parse_meta.update({"ocr_refill_failed": f"{type(exc).__name__}: {exc}"[:200],
                               "ocr_degraded": True,
                               "ocr_empty_pages": list(range(max(npages, 1)))})
            doc = ParsedDocument(title=filename)
            doc.parse_meta = parse_meta
            return doc
        items = [{"type": "text", "text": t.strip(), "page_idx": i,
                  "ocr_refill": True, "ocr_provider": provider}
                 for i, t in sorted(refilled.items()) if t.strip()]
        still = sorted(set(range(max(npages, 1))) - set(refilled))
        parse_meta["ocr_empty_pages"] = still
        if still:
            parse_meta["ocr_degraded"] = True
        doc = normalize_content_list(items, title=filename)
        doc.parse_meta = parse_meta
        return doc

    @staticmethod
    def _ocr_langs() -> list[str]:
        from app.core.config import get_settings

        raw = (get_settings().DOC_OCR_LANGS or "fa").strip() or "fa"
        langs = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
        if "fa" not in langs:
            langs = ["fa"] + langs  # Persian OCR is mandatory, never dropped
        return langs
