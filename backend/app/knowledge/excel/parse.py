"""Workbook parsing (openpyxl, read-only, no macros executed).

One sheet -> SheetInfo(name, table, columns[{name, dtype}], n_rows,
sample_rows). Header = first row; fully-empty rows skipped; column names
normalized + deduplicated (fallback col_N). dtypes inferred from a sample
of values: int -> float -> date -> bool -> text. Limits (max sheets /
rows) come from Settings and are enforced loudly.
"""

import io
import re
from dataclasses import dataclass, field

_NAME_RE = re.compile(r"[^a-z0-9_]")


def sanitize_name(name: str, fallback: str) -> str:
    out = _NAME_RE.sub("_", (name or "").strip().lower())
    out = re.sub(r"_+", "_", out).strip("_")
    if not out or out[0].isdigit():
        return fallback
    return out[:60]


@dataclass
class ExcelColumn:
    name: str       # original header text
    col: str        # sanitized DuckDB identifier
    dtype: str      # int | float | date | bool | text


@dataclass
class SheetInfo:
    name: str
    table: str      # sanitized DuckDB table name
    columns: list = field(default_factory=list)
    n_rows: int = 0
    sample_rows: list = field(default_factory=list)  # list[list[str | None]]
    # Full data rows (raw values, ragged ok). Transient worker memory only:
    # bounded by EXCEL_MAX_ROWS_PER_SHEET; never embedded or stored as-is.
    rows: list = field(default_factory=list)


def _infer(rows: list[list], n_cols: int) -> list[str]:
    import datetime

    dtypes = []
    for c in range(n_cols):
        vals = [r[c] for r in rows[:1000]
                if c < len(r) and r[c] not in (None, "")]
        kind = "text"
        if vals:
            if all(isinstance(v, bool) for v in vals):
                kind = "bool"
            elif all(isinstance(v, int) and not isinstance(v, bool)
                     for v in vals):
                kind = "int"
            elif all(isinstance(v, (int, float)) and not isinstance(v, bool)
                     for v in vals):
                kind = "float"
            elif all(isinstance(v, (datetime.date, datetime.datetime))
                     for v in vals):
                kind = "date"
        dtypes.append(kind)
    return dtypes


def parse_workbook(blob: bytes, max_sheets: int = 50,
                   max_rows: int = 200_000,
                   sample_rows: int = 5) -> list[SheetInfo]:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    except Exception as exc:
        raise RuntimeError(f"invalid xlsx workbook: {type(exc).__name__}") from exc
    names = list(wb.sheetnames or [])
    if len(names) > max_sheets:
        raise RuntimeError(f"too many sheets ({len(names)} > {max_sheets})")
    out: list[SheetInfo] = []
    for i, name in enumerate(names):
        ws = wb[name]
        rows = list(ws.iter_rows(values_only=True))
        header = []
        data: list[list] = []
        for r, row in enumerate(rows):
            cells = list(row or [])
            if all(v in (None, "") for v in cells):
                continue
            if not header:
                header = [(str(v).strip() if v not in (None, "") else "")
                          for v in cells]
                # drop trailing empty header cells
                while header and not header[-1]:
                    header.pop()
                if not header:
                    continue
            else:
                data.append(cells)
        if not header:
            out.append(SheetInfo(name=name or f"Sheet{i + 1}",
                                 table=sanitize_name(name, f"sheet_{i + 1}")))
            continue
        n_cols = len(header)
        if len(data) > max_rows:
            raise RuntimeError(
                f"sheet {name!r} has too many rows "
                f"({len(data)} > {max_rows})")
        seen: dict[str, int] = {}
        cols: list[ExcelColumn] = []
        for c, h in enumerate(header):
            base = sanitize_name(h, f"col_{c + 1}")
            n = seen.get(base, 0)
            seen[base] = n + 1
            col = base if n == 0 else f"{base}_{n + 1}"
            cols.append(ExcelColumn(name=h or f"col_{c + 1}", col=col,
                                    dtype="text"))
        for c, kind in enumerate(_infer(data, n_cols)):
            if c < len(cols):
                cols[c].dtype = kind
        samples = []
        for r in data[:sample_rows]:
            samples.append([("" if (c >= len(r) or r[c] is None) else str(r[c])[:200])
                            for c in range(n_cols)])
        out.append(SheetInfo(name=name, table=sanitize_name(name, f"sheet_{i + 1}"),
                             columns=cols, n_rows=len(data),
                             sample_rows=samples, rows=data))
    try:
        wb.close()
    except Exception:
        pass
    if not out:
        raise RuntimeError("workbook has no sheets")
    return out


def _infer_csv(rows: list[list], n_cols: int) -> list[str]:
    """int -> float -> text for string cells (no date guessing on text)."""

    def kind_of(v: str) -> str:
        v = (v or "").strip()
        if not v:
            return "empty"
        try:
            int(v)
            return "int"
        except ValueError:
            pass
        try:
            float(v)
            return "float"
        except ValueError:
            pass
        return "text"

    dtypes = []
    for c in range(n_cols):
        kinds = {kind_of(r[c]) for r in rows[:1000]
                 if c < len(r) and (r[c] or "").strip()}
        kinds.discard("empty")
        if not kinds:
            dtypes.append("text")
        elif kinds <= {"int"}:
            dtypes.append("int")
        elif kinds <= {"int", "float"}:
            dtypes.append("float")
        else:
            dtypes.append("text")
    return dtypes


def _decode_csv(blob: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return blob.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return blob.decode("utf-8", errors="replace")


def parse_csv(blob: bytes, filename: str = "data.csv",
              max_rows: int = 200_000,
              sample_rows: int = 5) -> list[SheetInfo]:
    """CSV bytes -> single SheetInfo (delimiter sniffed, like .txt upload).

    Same SheetInfo/DuckDB/RAG contract as workbooks, so the worker, the
    DuckDB artifact and excel_query work unchanged.
    """
    import csv as _csv

    text = _decode_csv(blob)
    if not text.strip():
        raise RuntimeError("empty csv file")
    try:
        dialect = _csv.Sniffer().sniff(text[:65536], delimiters=",;|\t")
    except Exception:
        dialect = _csv.excel
    rows = [r for r in _csv.reader(text.splitlines(), dialect)]
    header: list[str] = []
    data: list[list] = []
    for row in rows:
        if all(not (v or "").strip() for v in row):
            continue
        if not header:
            header = [v.strip() for v in row]
            while header and not header[-1]:
                header.pop()
        else:
            data.append([v.strip() for v in row])
    if not header:
        raise RuntimeError("csv has no header row")
    if len(data) > max_rows:
        raise RuntimeError(f"csv has too many rows ({len(data)} > {max_rows})")
    n_cols = len(header)
    seen: dict[str, int] = {}
    cols: list[ExcelColumn] = []
    for c, h in enumerate(header):
        base = sanitize_name(h, f"col_{c + 1}")
        n = seen.get(base, 0)
        seen[base] = n + 1
        cols.append(ExcelColumn(name=h or f"col_{c + 1}",
                                col=base if n == 0 else f"{base}_{n + 1}",
                                dtype="text"))
    for c, kind in enumerate(_infer_csv(data, n_cols)):
        if c < len(cols):
            cols[c].dtype = kind
    base_name = (filename or "data.csv").rsplit("/", 1)[-1]
    base_name = base_name.rsplit(".", 1)[0].strip() or "data"
    samples = [[(r[c] if c < len(r) else "")[:200] for c in range(n_cols)]
               for r in data[:sample_rows]]
    return [SheetInfo(name=base_name,
                      table=sanitize_name(base_name, "csv"),
                      columns=cols, n_rows=len(data),
                      sample_rows=samples, rows=data)]


def _norm_value(v) -> str:
    return " ".join(str(v).strip().lower().split())


def detect_relations(sheets: list[SheetInfo],
                     sample_values: int = 2000,
                     max_relations: int = 20) -> list[dict]:
    """Detect inter-sheet relationships (روابط داده‌ها) from samples.

    Pure + bounded: per-column value pools (capped) over sheet samples.
    - shared-column: same sanitized column name in two tables with ≥2
      shared values (avoids coincidental `id`-style name matches).
    - value-overlap: same dtype, different names, ≥5 shared values AND
      ≥50% coverage of the smaller pool.
    Deterministic order; capped at `max_relations`. No full-row scan
    beyond the cap, no model calls. Single-sheet workbooks yield [].
    """
    pools: dict[tuple[str, str, str, str], set[str]] = {}
    for sh in sheets:
        for ci, col in enumerate(sh.columns or []):
            vals: set[str] = set()
            for r in (sh.rows or [])[:sample_values]:
                if ci < 0 or ci >= len(r):
                    continue
                v = r[ci]
                if v in (None, ""):
                    continue
                vals.add(_norm_value(v))
                if len(vals) >= sample_values:
                    break
            if vals:
                pools[(sh.name, sh.table, col.col, col.dtype)] = vals
    keys = sorted(pools)
    out: list[dict] = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            n1, t1, c1, d1 = keys[i]
            n2, t2, c2, d2 = keys[j]
            if t1 == t2:
                continue
            shared = pools[keys[i]] & pools[keys[j]]
            small = min(len(pools[keys[i]]), len(pools[keys[j]])) or 1
            kind = ""
            if c1 == c2 and len(shared) >= 2:
                kind = "shared-column"
            elif (c1 != c2 and d1 == d2 and len(shared) >= 5
                    and len(shared) / small >= 0.5):
                kind = "value-overlap"
            if kind:
                out.append({"from_sheet": n1, "from_table": t1,
                            "from_col": c1, "to_sheet": n2,
                            "to_table": t2, "to_col": c2,
                            "kind": kind, "shared_values": len(shared)})
                if len(out) >= max(1, max_relations):
                    return out
    return out


def relations_text(relations: list[dict]) -> str:
    """RAG/LLM-readable relationships section (join edges for SQL)."""
    if not relations:
        return ""
    lines = ["Relationships (join on these column pairs):"]
    for r in relations:
        lines.append(
            f"- {r['from_sheet']}.{r['from_col']} <-> "
            f"{r['to_sheet']}.{r['to_col']} "
            f"({r['kind']}, {r.get('shared_values', 0)} shared values)")
    return "\n".join(lines)


def summary_text(sheet: SheetInfo, relations: list[dict] | None = None) -> str:
    """RAG summary chunk for one sheet (structure + samples, never full rows).

    `relations` (this sheet's edges) are appended so RAG + the SQL
    fallback model know which joins exist.
    """
    lines = [f"Sheet: {sheet.name}",
             f"Rows: {sheet.n_rows} | Columns: {len(sheet.columns)}"]
    for c in sheet.columns:
        lines.append(f"- {c.name} ({c.dtype})")
    if sheet.sample_rows:
        heads = [c.name for c in sheet.columns]
        lines.append("Sample rows:")
        lines.append(" | ".join(heads))
        for r in sheet.sample_rows:
            lines.append(" | ".join(v or "-" for v in r))
    elif sheet.n_rows == 0:
        lines.append("(empty sheet: headers only, no data rows)")
    mine = [r for r in (relations or [])
            if r.get("from_table") == sheet.table
            or r.get("to_table") == sheet.table]
    if mine:
        lines.append(relations_text(mine))
    return "\n".join(lines)
