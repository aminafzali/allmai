"""DuckDB persistence (worker) + validated read-only queries (agent tool).

The .duckdb file lives in StorageProvider next to the source blob, so no
new infrastructure is needed and the S3 backend keeps working. Queries
run on a read-only connection with an allowlist validator (SELECT-only,
known tables/columns, capped rows) plus a wall-clock timeout.
"""

import os
import re
import tempfile

DTYPE_SQL = {"int": "BIGINT", "float": "DOUBLE", "date": "TIMESTAMP",
             "bool": "BOOLEAN", "text": "VARCHAR"}

_FORBIDDEN = re.compile(
    r"(;|--|/\*|\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|"
    r"INSTALL|LOAD|COPY|PRAGMA|SHOW|DESCRIBE|EXPLAIN|CALL|VACUUM|"
    r"CHECKPOINT)\b)", re.I)
_IDENT = r"[A-Za-z_][\w$]*"
_QREF = rf"(?:{_IDENT}\.)?{_IDENT}"  # bare col or table-qualified t.col
_SELECT_ITEM = re.compile(
    rf"^(?:(?P<agg>SUM|COUNT|AVG|MIN|MAX)\s*\(\s*(?P<aarg>\*|{_QREF})\s*\)"
    rf"|(?P<col>{_QREF}))"
    rf"(?:\s+AS\s+(?P<alias>{_IDENT}))?$", re.I)
_JOIN_ON = re.compile(rf"^(?P<left>{_QREF})\s*=\s*(?P<right>{_QREF})$", re.I)


def build_duckdb(sheets) -> bytes:
    """Build a DuckDB file from parsed sheets (full rows). Returns bytes."""
    import datetime

    import duckdb

    def coerce(v, dtype):
        if v in (None, ""):
            return None
        try:
            if dtype == "int":
                return int(v) if not isinstance(v, bool) else int(v)
            if dtype == "float":
                return float(v)
            if dtype == "bool":
                return bool(v)
            if dtype == "date":
                if isinstance(v, datetime.datetime):
                    return v
                if isinstance(v, datetime.date):
                    return datetime.datetime(v.year, v.month, v.day)
                return None
            return str(v)
        except (ValueError, TypeError, OverflowError):
            return None

    with tempfile.TemporaryDirectory(prefix="allmai-xbuild-") as tmp:
        path = os.path.join(tmp, "kb.duckdb")
        con = duckdb.connect(path)
        try:
            for sh in sheets:
                cols = ", ".join(f'"{c.col}" {DTYPE_SQL.get(c.dtype, "VARCHAR")}'
                                 for c in sh.columns) or '"_empty" VARCHAR'
                con.execute(f'CREATE TABLE "{sh.table}" ({cols})')
                if sh.n_rows and sh.columns:
                    widths = len(sh.columns)
                    rows = [[coerce(r[ci] if ci < len(r) else None,
                                    sh.columns[ci].dtype)
                             for ci in range(widths)]
                            for r in (sh.rows or [])]
                    # pad short rows (ragged sheets)
                    fixed = [r + [None] * (widths - len(r)) if len(r) < widths
                             else r[:widths] for r in rows]
                    if fixed:
                        con.executemany(
                            f'INSERT INTO "{sh.table}" VALUES '
                            f'({", ".join(["?"] * widths)})', fixed)
            con.commit()
        finally:
            con.close()
        with open(path, "rb") as f:
            return f.read()


def _split_ref(ref: str) -> tuple[str | None, str]:
    """'t.col' -> (t, col); 'col' -> (None, col)."""
    parts = str(ref or "").split(".")
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    return None, str(ref or "")


def _resolve_col(ref: str, t1: str, cols1: set[str],
                 t2: str | None = None,
                 cols2: set[str] | None = None) -> tuple[str, str]:
    """Resolve a bare/qualified column ref to (table, col), quoted later.

    Bare refs must be unambiguous when two tables are in scope; qualified
    refs must name a real table+column. Raises ValueError otherwise.
    """
    t, c = _split_ref(ref)
    if t is not None:
        if t2 is not None and t == t2:
            if c not in (cols2 or set()):
                raise ValueError(f"unknown column: {ref}")
            return t2, c
        if t == t1:
            if c not in cols1:
                raise ValueError(f"unknown column: {ref}")
            return t1, c
        raise ValueError(f"unknown table in ref: {ref}")
    if t2 is not None and c in (cols2 or set()) and c in cols1:
        raise ValueError(f"ambiguous column (qualify it): {c}")
    if c in cols1:
        return t1, c
    if t2 is not None and c in (cols2 or set()):
        return t2, c
    raise ValueError(f"unknown column: {c}")


def _join_edge_ok(t1: str, c1: str, t2: str, c2: str,
                  relations: list[dict] | None) -> bool:
    """A JOIN is allowed only on a detected relation edge (either direction)."""
    for r in relations or []:
        a = (str(r.get("from_table") or ""), str(r.get("from_col") or ""))
        b = (str(r.get("to_table") or ""), str(r.get("to_col") or ""))
        if (a == (t1, c1) and b == (t2, c2)) or \
                (a == (t2, c2) and b == (t1, c1)):
            return True
    return False


def validate_sql(sql: str, tables: dict[str, set[str]],
                 relations: list[dict] | None = None) -> dict:
    """Allowlist-validate an analytic SELECT. Returns parsed parts.

    tables: {table_name: {column_names}}. relations: detected join edges
    [{from_table, from_col, to_table, to_col}] — a JOIN is accepted ONLY
    on one of these edges (2-table INNER JOIN, single ON equality).
    Raises ValueError on anything outside SELECT <agg|col>[, ...]
    FROM t [[INNER] JOIN t2 ON c1 = c2] [GROUP BY col] [ORDER BY col
    [DESC]] [LIMIT n]. LIMIT is clamped to the query cap by the caller.
    """
    from app.core.config import get_settings

    q = " ".join((sql or "").split())
    if not q or len(q) > 2000:
        raise ValueError("empty or oversized query")
    if _FORBIDDEN.search(q):
        raise ValueError("only read-only SELECT queries are allowed")
    m = re.match(
        rf"^SELECT\s+(?P<select>.+?)\s+FROM\s+(?P<table>{_IDENT})"
        rf"(?:\s+(?:INNER\s+)?JOIN\s+(?P<join>{_IDENT})\s+ON\s+"
        rf"(?P<on>{_QREF}\s*=\s*{_QREF}))?"
        rf"(?:\s+GROUP\s+BY\s+(?P<group>{_QREF}))?"
        rf"(?:\s+ORDER\s+BY\s+(?P<order>{_QREF})(?:\s+(?P<dir>ASC|DESC))?)?"
        rf"(?:\s+LIMIT\s+(?P<limit>\d+))?\s*$", q, re.I)
    if not m:
        raise ValueError("unsupported query shape (SELECT … FROM … "
                         "[JOIN … ON …] [GROUP BY …] [ORDER BY …] [LIMIT …])")
    table = m.group("table")
    if table not in tables:
        raise ValueError(f"unknown table: {table}")
    cols1 = tables[table]
    t2, cols2 = None, set()
    on_clause = ""
    if m.group("join"):
        t2 = m.group("join")
        if t2 not in tables or t2 == table:
            raise ValueError(f"unknown join table: {t2}")
        cols2 = tables[t2]
        om = _JOIN_ON.match((m.group("on") or "").strip())
        if not om:
            raise ValueError(f"unsupported ON clause: {m.group('on')!r}")
        lt, lc = _resolve_col(om.group("left"), table, cols1, t2, cols2)
        rt, rc = _resolve_col(om.group("right"), table, cols1, t2, cols2)
        if {lt, rt} != {table, t2} or lt == rt:
            raise ValueError("ON must relate the two tables")
        if not _join_edge_ok(lt, lc, rt, rc, relations):
            raise ValueError(
                f"no detected relation for JOIN {lt}.{lc} = {rt}.{rc}")
        on_clause = f" JOIN \"{t2}\" ON \"{lt}\".\"{lc}\" = \"{rt}\".\"{rc}\""
    out_cols: list[str] = []
    for item in m.group("select").split(","):
        im = _SELECT_ITEM.match(item.strip())
        if not im:
            raise ValueError(f"unsupported select item: {item.strip()!r}")
        if im.group("agg"):
            agg, arg = im.group("agg").upper(), im.group("aarg")
            if arg == "*":
                if agg != "COUNT":
                    raise ValueError("`*` is only allowed in COUNT(*)")
                out_cols.append(f"{agg}(*)")
                continue
            rt, rc = _resolve_col(arg, table, cols1, t2, cols2)
            out_cols.append(f"{agg}(\"{rt}\".\"{rc}\")")
        else:
            rt, rc = _resolve_col(im.group("col"), table, cols1, t2, cols2)
            out_cols.append(f"\"{rt}\".\"{rc}\"")
    limit = int(m.group("limit") or 0) or int(get_settings().EXCEL_QUERY_MAX_ROWS)
    limit = max(1, min(limit, int(get_settings().EXCEL_QUERY_MAX_ROWS)))
    if t2 is None:
        # Single-table output stays byte-identical to the legacy shape
        # (raw select text, quoted group/order).
        group, order = m.group("group"), m.group("order")
        if group:
            _resolve_col(group, table, cols1)
        if order:
            im_o = _SELECT_ITEM.match(order.strip())
            ok_alias = False
            if im_o:
                try:
                    if im_o.group("agg"):
                        a = im_o.group("aarg")
                        if a == "*":
                            ok_alias = (im_o.group("agg").upper() == "COUNT"
                                        and "COUNT(*)" in out_cols)
                        else:
                            rt, rc = _resolve_col(a, table, cols1)
                            ok_alias = (f"{im_o.group('agg').upper()}"
                                        f"(\"{rt}\".\"{rc}\")") in out_cols
                    else:
                        rt, rc = _resolve_col(im_o.group("col"), table, cols1)
                        ok_alias = f"\"{rt}\".\"{rc}\"" in out_cols
                except ValueError:
                    ok_alias = False
            if not ok_alias:
                _resolve_col(order, table, cols1)
        safe = (f"SELECT {m.group('select')} FROM \"{table}\""
                + (f" GROUP BY \"{group}\"" if group else "")
                + (f" ORDER BY \"{order}\"" + (f" {m.group('dir').upper()}"
                                               if m.group("dir") else "")
                   if order else "")
                + f" LIMIT {limit}")
        return {"table": table, "sql": safe, "limit": limit, "join": None}

    def _q(ref: str | None) -> str | None:
        if not ref:
            return None
        rt, rc = _resolve_col(ref, table, cols1, t2, cols2)
        return f"\"{rt}\".\"{rc}\""

    group = _q(m.group("group"))
    order_raw, order = m.group("order"), None
    if order_raw:
        # ORDER BY may target a SELECT alias (e.g. an aggregate).
        if order_raw in out_cols:
            order = order_raw
        else:
            order = _q(order_raw)
    select_out = ", ".join(out_cols)
    safe = (f"SELECT {select_out} FROM \"{table}\"{on_clause}"
            + (f" GROUP BY {group}" if group else "")
            + (f" ORDER BY {order}" + (f" {m.group('dir').upper()}"
                                       if m.group("dir") else "") if order else "")
            + f" LIMIT {limit}")
    return {"table": table, "sql": safe, "limit": limit, "join": t2}


def query_duckdb_file(path: str, sql: str, timeout_s: int = 15) -> tuple[list, list]:
    """Run validated SQL on a local .duckdb file (read-only)."""
    from concurrent.futures import ThreadPoolExecutor

    def _run():
        import duckdb

        con = duckdb.connect(path, read_only=True)
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in (cur.description or [])]
            return cols, cur.fetchall()
        finally:
            con.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_run)
        try:
            return fut.result(timeout=timeout_s)
        except Exception as exc:
            raise RuntimeError(f"query failed: {type(exc).__name__}") from exc


def run_query(storage, duckdb_key: str, sql: str,
              timeout_s: int = 15) -> tuple[list, list]:
    """Fetch the .duckdb blob via StorageProvider and query it read-only."""
    blob = storage.get(duckdb_key)  # FileNotFoundError surfaces loudly
    with tempfile.TemporaryDirectory(prefix="allmai-xq-") as tmp:
        path = os.path.join(tmp, "q.duckdb")
        with open(path, "wb") as f:
            f.write(blob)
        return query_duckdb_file(path, sql, timeout_s)
