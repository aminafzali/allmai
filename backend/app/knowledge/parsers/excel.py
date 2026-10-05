"""Excel/CSV entry point for the worker: parse -> RAG summary pages.

Returns a ParsedDocument whose pages are per-sheet summaries (structure +
samples, never full rows) plus the full SheetInfo list in `extra` for the
worker to build DuckDB (single parse, no drift). Limits from Settings
(EXCEL_* caps apply to CSV too).
"""

from app.knowledge.parsers.base import ParsedDocument, ParsedPage


def _to_document(sheets, parser: str) -> ParsedDocument:
    from app.knowledge.excel.parse import detect_relations, summary_text

    relations = detect_relations(sheets)
    pages = [ParsedPage(text=summary_text(sh, relations)) for sh in sheets]
    meta = {"parser": parser,
            "relations": relations,
            "sheets": [{"name": sh.name, "table": sh.table,
                        "rows": sh.n_rows,
                        "columns": [{"name": c.name, "col": c.col,
                                     "dtype": c.dtype} for c in sh.columns]}
                       for sh in sheets]}
    doc = ParsedDocument(title="", pages=pages, page_count=len(pages),
                         parse_meta=meta)
    doc.extra["excel_sheets"] = sheets
    return doc


def parse_excel(blob: bytes) -> ParsedDocument:
    from app.core.config import get_settings
    from app.knowledge.excel.parse import parse_workbook

    s = get_settings()
    sheets = parse_workbook(blob, max_sheets=int(s.EXCEL_MAX_SHEETS),
                            max_rows=int(s.EXCEL_MAX_ROWS_PER_SHEET))
    return _to_document(sheets, "excel-openpyxl")


def parse_csv(blob: bytes, filename: str = "data.csv") -> ParsedDocument:
    from app.core.config import get_settings
    from app.knowledge.excel.parse import parse_csv as _parse_csv

    s = get_settings()
    sheets = _parse_csv(blob, filename=filename,
                        max_rows=int(s.EXCEL_MAX_ROWS_PER_SHEET))
    return _to_document(sheets, "csv")
