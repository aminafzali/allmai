"""Normalize RAG-Anything content_list items into AllMai ParsedElements.

Input schema (verified against raganything 1.4.1 source, parser.py):
- text:     {type, text, page_idx}
- table:    {type, table_body (cells dict|list|str), table_caption,
             table_footnote, img_path, page_idx}
- image:    {type, img_path (absolute file), image_caption|img_caption,
             image_footnote|img_footnote, page_idx}
- equation: {type, text (latex), text_format, img_path, page_idx}
- degraded: {type: text, text: "[Table/Image processing failed: ...]"}
  (kept as text WITH metadata{parse_warning} — never dropped silently)

page_idx is 0-based -> page_no is 1-based (AllMai citation contract).
"""

import os

from app.knowledge.parsers.base import ParsedDocument, ParsedElement, ParsedPage

_FAILURE_MARKERS = ("[Table processing failed:", "[Image processing failed:")


def _table_cells_to_markdown(body) -> tuple[str, dict]:
    """Best-effort cells -> markdown. Returns (markdown, shape_meta)."""
    if isinstance(body, str):
        text = body.strip()
        return text, {"table_format": "raw", "rows": 0, "cols": 0}
    cells = []
    if isinstance(body, dict):
        cells = body.get("table_cells") or body.get("cells") or []
    elif isinstance(body, list):
        cells = body
    grid: dict[tuple[int, int], str] = {}
    max_r = max_c = 0
    for c in cells:
        if not isinstance(c, dict):
            continue
        r = int(c.get("start_row_offset_idx", c.get("row", 0)) or 0)
        col = int(c.get("start_col_offset_idx", c.get("col", 0)) or 0)
        txt = str(c.get("text", "")).strip().replace("\n", " ")
        grid[(r, col)] = txt
        max_r, max_c = max(max_r, r), max(max_c, col)
    if not grid:
        return "", {"table_format": "empty", "rows": 0, "cols": 0}
    rows = max_r + 1
    cols = max_c + 1
    lines = []
    for r in range(rows):
        lines.append("| " + " | ".join(grid.get((r, c), "") for c in range(cols)) + " |")
        if r == 0:
            lines.append("| " + " | ".join("---" for _ in range(cols)) + " |")
    return "\n".join(lines), {"table_format": "markdown", "rows": rows, "cols": cols}


def _read_blob(path: str) -> bytes | None:
    try:
        if path and os.path.isfile(path):
            with open(path, "rb") as f:
                return f.read()
    except OSError:
        pass
    return None


def _norm_text(s: str) -> str:
    return " ".join(str(s or "").split())


def _block_match_text(node: dict) -> str:
    label = str(node.get("label", "") or "")
    if label == "formula":
        return _norm_text(node.get("orig", ""))
    if label in ("table", "table_item"):
        data = node.get("data")
        cells_txt = ""
        if isinstance(data, dict):
            cells = data.get("table_cells") or data.get("cells") or []
            cells_txt = " ".join(
                str(c.get("text", "")) for c in cells if isinstance(c, dict))
        else:
            cells_txt = str(data or "")
        cap = node.get("caption", "")
        if isinstance(cap, dict):
            cap = cap.get("text", "")
        foot = node.get("footnote", "")
        if isinstance(foot, dict):
            foot = foot.get("text", "")
        return _norm_text(f"{cap} {foot} {cells_txt}"[:800])
    if label in ("picture", "figure"):
        cap = node.get("caption", "")
        if isinstance(cap, dict):
            cap = cap.get("text", "")
        return _norm_text(cap)
    return _norm_text(node.get("orig", node.get("text", "")))


def _resolve_ref(doc_dict: dict, node: dict) -> dict:
    ref = node.get("$ref", "") if isinstance(node, dict) else ""
    if isinstance(ref, str) and ref.startswith("#/"):
        cur: object = doc_dict
        for part in ref[2:].split("/"):
            if isinstance(cur, dict):
                cur = cur.get(part)
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                return {}
        return cur if isinstance(cur, dict) else {}
    return node if isinstance(node, dict) else {}


def _walk_body(doc_dict: dict, node: dict, seq: list) -> None:
    node = _resolve_ref(doc_dict, node)
    if not node:
        return
    label = str(node.get("label", "") or "")
    children = node.get("children") or []
    if children and label not in ("text", "table", "table_item", "picture",
                                  "figure", "formula", "section_header",
                                  "title", "list_item"):
        for ch in children:
            _walk_body(doc_dict, ch, seq)
        return
    if label in ("text", "table", "table_item", "picture", "figure",
                 "formula", "section_header", "title", "list_item"):
        page_no = None
        try:
            prov = node.get("prov") or []
            if prov:
                page_no = int(prov[0].get("page_no"))
        except (TypeError, ValueError, IndexError, AttributeError):
            page_no = None
        seq.append((_block_match_text(node), page_no))


def page_map_from_docling_dict(doc_dict: dict, items: list[dict]):
    """Map content_list index -> real page_no from Docling's export dict.

    Returns (page_map, unverified): page_map[index] = page_no for confident
    matches (text equality against unused blocks, else positional fallback);
    unverified = indices whose page could not be confirmed.
    """
    seq: list[tuple[str, int | None]] = []
    try:
        _walk_body(doc_dict, doc_dict.get("body", {}), seq)
    except Exception:
        return {}, set(range(len(items or [])))
    page_map: dict[int, int] = {}
    unverified: set[int] = set()
    used = [False] * len(seq)

    def _close_enough(want: str, txt: str) -> bool:
        return bool(want and txt and (want == txt or want in txt or txt in want))

    for j, it in enumerate(items or []):
        if not isinstance(it, dict):
            unverified.add(j)
            continue
        want = _norm_text(it.get("text", "") or it.get("table_caption", "") or
                          it.get("image_caption", it.get("img_caption", "")) or "")[:500]
        hit = None
        if want:
            for k, (txt, pg) in enumerate(seq):
                if not used[k] and pg is not None and _close_enough(want, txt):
                    hit = (k, pg)
                    break
        if hit is None and j < len(seq):
            txt, pg = seq[j]
            if pg is not None and (not want or _close_enough(want, txt)):
                hit = (j, pg)
        if hit is None:
            unverified.add(j)
            continue
        used[hit[0]] = True
        page_map[j] = hit[1]
    return page_map, unverified


def normalize_content_list(items: list[dict], title: str = "",
                           page_map: dict[int, int] | None = None,
                           unverified: set[int] | None = None) -> ParsedDocument:
    """Pure function: content_list -> structured ParsedDocument."""
    doc = ParsedDocument(title=title or "")
    order = 0
    max_page = 0
    unverified = set(unverified or [])
    for idx, it in enumerate(items or []):
        if not isinstance(it, dict):
            continue
        kind = str(it.get("type", "text"))
        page_idx = it.get("page_idx", 0)
        try:
            page_no = int(page_idx) + 1
        except (TypeError, ValueError):
            page_no = None
        # P1: real page from the Docling export dict overrides page_idx.
        if page_map and idx in page_map:
            page_no = page_map[idx]
        elif idx in unverified:
            pass  # keep heuristic page, flagged below
        if page_no is not None:
            max_page = max(max_page, page_no)
        order += 1
        eid = f"e{order}"
        meta: dict = {"element_id": eid, "reading_order": order}
        if idx in unverified:
            meta["page_unverified"] = True

        if kind == "table":
            md, shape = _table_cells_to_markdown(it.get("table_body", ""))
            caption = str(it.get("table_caption", "") or "").strip()
            footnote = str(it.get("table_footnote", "") or "").strip()
            head = f"Table{f' ({caption})' if caption else ''}:\n" if (md or caption) else ""
            text = f"{head}{md}"
            if footnote:
                text += f"\n{footnote}"
            if not text.strip():
                text = "[Empty table]"
                meta["parse_warning"] = "empty-table"
            meta.update({"kind": "table", **shape,
                         "caption": caption, "footnote": footnote})
            doc.elements.append(ParsedElement(
                element_id=eid, kind="table", page_no=page_no,
                reading_order=order, text_markdown=text.strip(),
                caption=caption, metadata=meta))
        elif kind == "image":
            caption = it.get("image_caption", it.get("img_caption", "")) or ""
            if isinstance(caption, list):
                caption = " ".join(str(x) for x in caption).strip()
            else:
                caption = str(caption).strip()
            footnote = it.get("image_footnote", it.get("img_footnote", "")) or ""
            if isinstance(footnote, list):
                footnote = " ".join(str(x) for x in footnote).strip()
            else:
                footnote = str(footnote).strip()
            blob = _read_blob(str(it.get("img_path", "") or ""))
            label = caption or "figure without caption"
            text = f"[Figure on page {page_no or '?'}: {label}]"
            if footnote:
                text += f" {footnote}"
            meta.update({"kind": "image", "caption": caption,
                         "footnote": footnote, "needs_vision": True,
                         "has_bytes": bool(blob)})
            if blob is None:
                meta["parse_warning"] = "image-bytes-missing"
            doc.elements.append(ParsedElement(
                element_id=eid, kind="image", page_no=page_no,
                reading_order=order, text_markdown=text,
                blob=blob, caption=caption, metadata=meta))
        elif kind == "equation":
            latex = str(it.get("text", "") or "").strip()
            text = f"${latex}$" if latex else "[Empty equation]"
            if not latex:
                meta["parse_warning"] = "empty-equation"
            meta.update({"kind": "equation", "text_format": str(it.get("text_format", "") or "")})
            doc.elements.append(ParsedElement(
                element_id=eid, kind="equation", page_no=page_no,
                reading_order=order, text_markdown=text, metadata=meta))
        else:  # text (incl. degraded failure markers)
            text = str(it.get("text", "") or "")
            meta.update({"kind": "text"})
            for marker in _FAILURE_MARKERS:
                if marker in text:
                    meta["parse_warning"] = marker.strip("[]:")
                    break
            if text.strip():
                doc.elements.append(ParsedElement(
                    element_id=eid, kind="text", page_no=page_no,
                    reading_order=order, text_markdown=text.strip(),
                    metadata=meta))
            # empty text items are skipped (never index empty content)
    # Legacy-compatible pages view: one joined text per page.
    by_page: dict[int, list[str]] = {}
    for el in doc.elements:
        if el.page_no is not None:
            by_page.setdefault(el.page_no, []).append(el.text_markdown)
    doc.pages = [ParsedPage(page_no=p, text="\n\n".join(by_page[p]))
                 for p in sorted(by_page)]
    doc.page_count = max_page
    return doc
