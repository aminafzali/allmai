"""Agent tools: controlled, workspace-scoped access to Knowledge/Memory.

Tools are plain functions taking an explicit workspace_id — the AgentService
binds them per turn. Nothing here is reachable without a tenant context.
"""

from dataclasses import dataclass


@dataclass
class ToolContext:
    db: object
    workspace_id: object
    user_id: object
    embed_fn: object = None
    kb_ids: object = None
    global_kb_ids: object = None
    generate_fn: object = None
    storage: object = None


def _hits_to_dicts(db, hits) -> list[dict]:
    """ScoredChunks -> prompt/citation dicts with expanded passages,
    document titles and source ids. Never raises (falls back to raw)."""
    try:
        from app.knowledge.retrieval.hybrid import bulk_doc_titles
        from app.knowledge.retrieval.passages import expand_hit_texts

        titles = bulk_doc_titles(db, [h.chunk.id for h in hits])
        expanded = expand_hit_texts(db, hits)
    except Exception:
        titles, expanded = {}, {}
    out = []
    for h in hits:
        seg, src = h.segment, h.source
        cid = str(h.chunk.id)
        out.append({
            "content": expanded.get(cid) or h.chunk.content,
            "chunk_id": cid,
            "source": (src.filename if src else ""),
            "source_id": str(src.id) if src else None,
            "doc_title": titles.get(cid, ""),
            "page_no": seg.page_no if seg else None,
            "start_ms": seg.start_ms if seg else None,
            "end_ms": seg.end_ms if seg else None,
            "score": h.score,
        })
    return out


def knowledge_search(ctx: ToolContext, query: str, kb_id=None, top_k: int = 6) -> list[dict]:
    """Return [{content, chunk_id, source, page_no, start_ms, end_ms, score}]."""
    from app.knowledge.retrieval.hybrid import hybrid_search

    kb_ids = getattr(ctx, "kb_ids", None)
    if kb_id is None and kb_ids:
        # Instance-assigned workspace KBs: fan out per KB, merge by score.
        seen: dict[str, dict] = {}
        order: dict[str, float] = {}
        for kid in kb_ids:
            hits = hybrid_search(ctx.db, ctx.workspace_id, query, kid, top_k,
                                 embed_fn=ctx.embed_fn)
            for d in _hits_to_dicts(ctx.db, hits):
                key = d["chunk_id"]
                if key not in seen or d["score"] > order.get(key, -1):
                    seen[key] = d
                    order[key] = d["score"]
        return sorted(seen.values(), key=lambda d: d["score"],
                      reverse=True)[:top_k]
    hits = hybrid_search(ctx.db, ctx.workspace_id, query, kb_id, top_k, embed_fn=ctx.embed_fn)
    return _hits_to_dicts(ctx.db, hits)


def memory_search(ctx: ToolContext, query: str, top_k: int = 6) -> list[dict]:
    from app.memory.postgres_provider import PostgresMemoryProvider

    return PostgresMemoryProvider(ctx.db).recall(ctx.workspace_id, ctx.user_id, query, top_k)


def memory_facts(ctx: ToolContext) -> list[dict]:
    from app.memory.postgres_provider import PostgresMemoryProvider

    facts = PostgresMemoryProvider(ctx.db).facts_for_user(ctx.workspace_id, ctx.user_id)
    return [{"key": f.key, "value": f.value, "category": f.category} for f in facts]


TOOLS = {"knowledge_search": knowledge_search, "memory_search": memory_search, "memory_facts": memory_facts,
          "global_knowledge_search": None,  # bound below (defined after imports)
          "web_search": None, "maps_search": None,  # bound below
          "excel_query": None}  # bound below (defined after imports)


# ---------- excel_query (structured Excel questions over DuckDB) ----------

_ANALYTIC_HINTS = ("sum", "total", "count", "avg", "average", "max", "min",
                   "group", "aggregate", "مجموع", "جمع", "تعداد", "چند",
                   "میانگین", "بیشترین", "کمترین", "حداکثر", "حداقل",
                   "گروه", "تفکیک")

_AGG_WORDS = (
    ("SUM", ("sum", "total", "مجموع", "جمع")),
    ("COUNT", ("count", "تعداد", "چند")),
    ("AVG", ("avg", "average", "میانگین")),
    ("MAX", ("max", "بیشترین", "حداکثر")),
    ("MIN", ("min", "کمترین", "حداقل")),
)


def _norm(s: str) -> str:
    return "".join((s or "").lower().split())


def _excel_sources(ctx: ToolContext, kb_id=None) -> list:
    """Workspace-scoped excel sources (ready only). RLS is the 2nd layer."""
    from app.common.base import coerce_uuid
    from app.knowledge.models import Source

    q = (ctx.db.query(Source)
         .filter(Source.workspace_id == coerce_uuid(ctx.workspace_id),
                 Source.type.in_(("excel", "csv")), Source.status == "ready"))
    kids = [kb_id] if kb_id else list(getattr(ctx, "kb_ids", None) or [])
    if len(kids) == 1:
        q = q.filter(Source.kb_id == coerce_uuid(kids[0]))
    elif len(kids) > 1:
        q = q.filter(Source.kb_id.in_([coerce_uuid(k) for k in kids]))
    return q.all()


def _pick_table(question: str, tables: list[dict]) -> dict | None:
    nq = _norm(question)
    best, best_score = None, 0
    for t in tables:
        for cand in (t.get("sheet") or "", t.get("table") or ""):
            nc = _norm(str(cand))
            if nc and (nc in nq or nq in nc):
                score = len(nc)
                if score > best_score:
                    best, best_score = t, score
    return best or (tables[0] if tables else None)


def _pick_column(question: str, columns: list[dict],
                 numeric_only: bool = False) -> dict | None:
    nq = _norm(question)
    for c in columns:
        if numeric_only and c.get("dtype") not in ("int", "float"):
            continue
        for cand in (c.get("name") or "", c.get("col") or ""):
            nc = _norm(str(cand))
            if len(nc) >= 2 and nc in nq:
                return c
    if numeric_only:
        for c in columns:
            if c.get("dtype") in ("int", "float"):
                return c
    return None


def _deterministic_sql(question: str, table: dict) -> str | None:
    """SUM/COUNT/AVG/MAX/MIN [+ GROUP BY] from keywords + column mentions.

    The GROUP BY column is picked FIRST (a text column mentioned alongside
    a grouping hint), so multi-mention questions like "count Units by
    Region" aggregate Units instead of Region.
    """
    nq = _norm(question)
    func = next((f for f, words in _AGG_WORDS
                 if any(_norm(w) in nq for w in words)), None)
    if func is None:
        return None
    cols = table.get("columns") or []
    grouped = any(_norm(w) in nq for w in
                  ("group", "by", "گروه", "تفکیک", "به تفکیک"))
    group = None
    if grouped:
        for c in cols:
            if c.get("dtype") not in ("text", "bool", "date"):
                continue
            nc = _norm(str(c.get("name") or ""))
            if len(nc) >= 2 and nc in nq:
                group = c
                break
    rest = [c for c in cols if c is not group]
    if func == "COUNT":
        # COUNT(mentioned column) when the question names one (COUNT works
        # on any dtype); otherwise COUNT(*) over the table.
        mentioned = _pick_column(question, rest)
        if mentioned is None:
            return f'SELECT COUNT(*) FROM {table["table"]}'
        target = mentioned
    else:
        target = _pick_column(question, rest, numeric_only=True)
        if target is None:
            return None
    # NOTE: identifiers stay unquoted here; validate_sql() quotes them
    # after allowlist checks (quoted input would fail the shape regex).
    sql = f'SELECT {func}({target["col"]}) FROM {table["table"]}'
    if group:
        sql = (f'SELECT {group["col"]}, {func}({target["col"]}) '
               f'FROM {table["table"]} GROUP BY {group["col"]}')
    return sql


def _mentioned_tables(question: str, tables: list[dict]) -> list[dict]:
    """Tables whose sheet/table/column names appear in the question."""
    nq = _norm(question)
    out = []
    for t in tables:
        cands = [t.get("sheet") or "", t.get("table") or ""]
        cands += [c.get("name") or "" for c in (t.get("columns") or [])]
        if any(len(_norm(c)) >= 2 and _norm(c) in nq for c in cands):
            out.append(t)
    return out


def _join_edge(t1: dict, t2: dict, relations: list[dict]) -> dict | None:
    """First detected edge between two tables (either direction)."""
    a, b = t1.get("table"), t2.get("table")
    for r in relations or []:
        ends = {(str(r.get("from_table") or ""), str(r.get("from_col") or "")),
                (str(r.get("to_table") or ""), str(r.get("to_col") or ""))}
        cols_a = {c.get("col") for c in (t1.get("columns") or [])}
        cols_b = {c.get("col") for c in (t2.get("columns") or [])}
        left = next(((ta, ca) for ta, ca in ends if ta == a and ca in cols_a),
                    None)
        right = next(((tb, cb) for tb, cb in ends if tb == b and cb in cols_b),
                     None)
        if left and right:
            return {"t1": a, "c1": left[1], "t2": b, "c2": right[1]}
    return None


def _mentioned_cols(question: str, table: dict,
                    numeric_only: bool = False) -> list[dict]:
    nq = _norm(question)
    out = []
    for c in table.get("columns") or []:
        if numeric_only and c.get("dtype") not in ("int", "float"):
            continue
        for cand in (c.get("name") or "", c.get("col") or ""):
            if len(_norm(cand)) >= 2 and _norm(cand) in nq:
                out.append(c)
                break
    return out


def _deterministic_join(question: str, tables: list[dict],
                        relations: list[dict]) -> str | None:
    """Two-table INNER JOIN from mentions + a detected relation edge.

    Covers: aggregate over one table grouped/limited by the other, and
    plain multi-table column selects. Single-table questions never reach
    here (the single-table builder runs first and wins ties).
    """
    mentioned = _mentioned_tables(question, tables)
    if len(mentioned) < 2 or not relations:
        return None
    nq = _norm(question)
    func = next((f for f, words in _AGG_WORDS
                 if any(_norm(w) in nq for w in words)), None)
    grouped = any(_norm(w) in nq for w in
                  ("group", "by", "گروه", "تفکیک", "به تفکیک"))
    for i in range(len(mentioned)):
        for j in range(i + 1, len(mentioned)):
            t1, t2 = mentioned[i], mentioned[j]
            edge = _join_edge(t1, t2, relations)
            if not edge:
                continue
            if func == "COUNT":
                cols1 = _mentioned_cols(question, t1)
                cols2 = _mentioned_cols(question, t2)
                if cols1 and cols2 and grouped:
                    return (f'SELECT {t1["table"]}.{cols1[0]["col"]}, '
                            f'COUNT(*) FROM {t1["table"]} '
                            f'JOIN {t2["table"]} ON {edge["t1"]}.{edge["c1"]} = '
                            f'{edge["t2"]}.{edge["c2"]} '
                            f'GROUP BY {t1["table"]}.{cols1[0]["col"]}')
                target = (cols1 or cols2 or [None])[0]
                if target:
                    owner = t1["table"] if target in cols1 else t2["table"]
                    return (f'SELECT COUNT({owner}.{target["col"]}) '
                            f'FROM {t1["table"]} JOIN {t2["table"]} ON '
                            f'{edge["t1"]}.{edge["c1"]} = {edge["t2"]}.{edge["c2"]}')
            elif func:
                for ta, tb in ((t1, t2), (t2, t1)):
                    nums = _mentioned_cols(question, ta, numeric_only=True)
                    others = _mentioned_cols(question, tb)
                    if nums and (others or not grouped):
                        sel = f'{func}({ta["table"]}.{nums[0]["col"]})'
                        if grouped and others:
                            return (f'SELECT {tb["table"]}.{others[0]["col"]}, '
                                    f'{sel} FROM {t1["table"]} '
                                    f'JOIN {t2["table"]} ON {edge["t1"]}.{edge["c1"]} = '
                                    f'{edge["t2"]}.{edge["c2"]} '
                                    f'GROUP BY {tb["table"]}.{others[0]["col"]}')
                        if not grouped:
                            return (f'SELECT {sel} FROM {t1["table"]} '
                                    f'JOIN {t2["table"]} ON {edge["t1"]}.{edge["c1"]} = '
                                    f'{edge["t2"]}.{edge["c2"]}')
            else:
                cols1 = _mentioned_cols(question, t1)
                cols2 = _mentioned_cols(question, t2)
                if cols1 and cols2:
                    sel = ", ".join(
                        [f'{t1["table"]}.{c["col"]}' for c in cols1] +
                        [f'{t2["table"]}.{c["col"]}' for c in cols2])
                    return (f'SELECT {sel} FROM {t1["table"]} '
                            f'JOIN {t2["table"]} ON {edge["t1"]}.{edge["c1"]} = '
                            f'{edge["t2"]}.{edge["c2"]}')
    return None


def _llm_sql(generate_fn, question: str, schema_txt: str) -> str | None:
    """LLM fallback for complex questions: model returns ONLY the SQL."""
    import re as _re

    prompt = (
        "Write ONE read-only analytic SQL query (SELECT … FROM … "
        "[[INNER] JOIN … ON …] [GROUP BY …] [ORDER BY …] [LIMIT …]) "
        "using EXACTLY these tables/columns:\n"
        f"{schema_txt}\n\nQuestion: {question}\n"
        "Rules: single SELECT, COUNT(*) only for counts, JOIN only on a "
        "listed relationship pair with table-qualified columns, no other "
        "statements. Return ONLY the SQL inside ```sql … ```.")
    try:
        text = generate_fn(prompt, temperature=0.0, max_tokens=500) or ""
    except Exception:
        return None
    m = _re.search(r"```sql\s*(.+?)\s*```", text, _re.S | _re.I)
    return m.group(1).strip() if m else None


def _schema_txt(tables: list[dict], relations: list[dict] | None = None) -> str:
    lines = []
    for t in tables:
        cols = ", ".join(f"{c.get('col')} ({c.get('dtype')})"
                         for c in (t.get("columns") or []))
        lines.append(f"table \"{t.get('table')}\" "
                     f"(sheet: {t.get('sheet')}, rows: {t.get('rows')}): {cols}")
    if relations:
        lines.append("Relationships (JOIN only on these pairs, "
                     "qualify columns as table.column):")
        for r in relations:
            lines.append(f"- {r.get('from_table')}.{r.get('from_col')} = "
                         f"{r.get('to_table')}.{r.get('to_col')} "
                         f"({r.get('kind')})")
    return "\n".join(lines)


def excel_query(ctx: ToolContext, question: str, kb_id=None,
                top_sources: int = 3) -> list[dict]:
    """Answer analytic Excel questions from DuckDB (never by re-parsing).

    Returns [{content, chunk_id, source, source_id, sheet, sql, columns,
    rows, row_count, score}] — the same shape as knowledge_search plus
    sheet/sql, so prompts and citations work unchanged. Informational
    questions (no analytic pattern) yield [] so RAG handles them.
    """
    from app.core.config import get_settings
    from app.knowledge.excel.store import run_query, validate_sql

    out: list[dict] = []
    try:
        sources = _excel_sources(ctx, kb_id)[:max(1, top_sources)]
    except Exception:
        return out
    storage = getattr(ctx, "storage", None)
    if storage is None:
        from app.storage.s3 import get_storage

        storage = get_storage()
    for src in sources:
        meta = (src.parse_meta or {}).get("excel") or {}
        tables = meta.get("tables") or []
        duck_key = meta.get("duckdb_key") or ""
        if not tables or not duck_key:
            continue
        relations = list(meta.get("relations") or [])
        table = _pick_table(question or "", tables)
        if table is None:
            continue
        allow = {t.get("table"): {c.get("col") for c in (t.get("columns") or [])}
                 for t in tables}
        # Cross-table questions (columns from 2+ tables over a
        # detected edge) try the JOIN builder FIRST; everything else
        # keeps the legacy single-table path byte-for-byte.
        sql = None
        if len(_mentioned_tables(question or "", tables)) >= 2:
            sql = _deterministic_join(question or "", tables, relations)
        if sql is None:
            sql = _deterministic_sql(question or "", table)
        if sql is None:
            gen = getattr(ctx, "generate_fn", None)
            if not callable(gen):
                continue  # informational: let RAG answer
            sql = _llm_sql(gen, question or "", _schema_txt(tables, relations))
            if sql is None:
                continue
        try:
            parsed = validate_sql(sql, allow, relations)
        except ValueError:
            continue
        try:
            cols, rows = run_query(
                storage, duck_key, parsed["sql"],
                timeout_s=int(get_settings().EXCEL_QUERY_TIMEOUT_S))
        except Exception:
            continue
        txt = [f"Excel result — {src.filename} / sheet {table.get('sheet')} "
               f"(SQL: {parsed['sql']})",
               " | ".join(str(c) for c in cols)]
        for r in rows[: int(get_settings().EXCEL_QUERY_MAX_ROWS)]:
            txt.append(" | ".join(("" if v is None else str(v)) for v in r))
        out.append({
            "content": "\n".join(txt),
            "chunk_id": f"excel:{src.id}:{table.get('table')}",
            "source": src.filename or "",
            "source_id": str(src.id),
            "page_no": None, "start_ms": None, "end_ms": None,
            "score": 1.0, "sheet": table.get("sheet"),
            "sql": parsed["sql"], "columns": [str(c) for c in cols],
            "rows": [[("" if v is None else str(v)) for v in r] for r in rows],
            "row_count": len(rows),
        })
    return out


TOOLS["excel_query"] = excel_query


def global_knowledge_search(ctx: ToolContext, query: str, top_k: int = 6) -> list[dict]:
    """Search ONLY assigned global KBs (locked: kb_ids come from the
    definition assignment, never from user input)."""
    from app.knowledge.retrieval.hybrid import hybrid_search_global

    kids = list(getattr(ctx, "global_kb_ids", None) or [])
    if not kids:
        return []
    hits = hybrid_search_global(ctx.db, query, kids, top_k, embed_fn=ctx.embed_fn)
    return _hits_to_dicts(ctx.db, hits)


TOOLS["global_knowledge_search"] = global_knowledge_search


# ---------- web_search (Gemini + Google Search Grounding) ----------

def web_search(ctx: ToolContext, query: str, top_k: int = 5) -> list[dict]:
    """General internet search via Gemini Google Search Grounding.

    Returns [{content, chunk_id, source, url, score}] — the first item is
    the grounded answer text, followed by one item per grounding source so
    citations point at real pages. [] when the key is missing or Google
    is unreachable (same fail-open pattern as excel_query).
    """
    import logging as _logging

    log = _logging.getLogger(__name__)
    try:
        from app.ai.gemini import GeminiProvider

        res = GeminiProvider().generate_grounded(query or "")
    except Exception as exc:
        log.warning("web_search unavailable: %s", exc)
        return []
    out = []
    if (res.get("text") or "").strip():
        out.append({
            "content": res["text"].strip(),
            "chunk_id": "web:answer",
            "source": "Google Search",
            "url": "",
            "page_no": None, "start_ms": None, "end_ms": None,
            "score": 1.0,
        })
    for i, ch in enumerate(list(res.get("chunks") or [])[:max(1, top_k)]):
        out.append({
            "content": f"{ch.get('title') or ch.get('uri')}\n{ch.get('uri') or ''}".strip(),
            "chunk_id": f"web:{i}",
            "source": ch.get("title") or ch.get("uri") or "",
            "url": ch.get("uri") or "",
            "page_no": None, "start_ms": None, "end_ms": None,
            "score": round(0.95 - i * 0.05, 3),
        })
    return out


TOOLS["web_search"] = web_search


# ---------- maps_search (Google Maps Places Text Search) ----------

def maps_search(ctx: ToolContext, query: str, top_k: int = 6) -> list[dict]:
    """Places/businesses search via Google Maps Platform (Places API New).

    Returns [{content, chunk_id, source, url, score}] — one item per place
    with name/address/rating/phone/maps link. [] when the key is missing
    or Google is unreachable.
    """
    import logging as _logging

    log = _logging.getLogger(__name__)
    try:
        from app.ai.maps import search_places

        places = search_places(query or "", max_results=top_k)
    except Exception as exc:
        log.warning("maps_search unavailable: %s", exc)
        return []
    out = []
    for i, p in enumerate(places):
        lines = [p.get("name") or ""]
        if p.get("address"):
            lines.append(p["address"])
        if p.get("rating") is not None:
            lines.append(f"امتیاز {p['rating']}"
                         + (f" ({p.get('ratings_total')} نظر)"
                            if p.get("ratings_total") else ""))
        if p.get("phone"):
            lines.append(f"تلفن: {p['phone']}")
        if p.get("maps_uri"):
            lines.append(p["maps_uri"])
        out.append({
            "content": "\n".join(l for l in lines if l),
            "chunk_id": f"maps:{i}",
            "source": p.get("name") or "",
            "url": p.get("maps_uri") or "",
            "page_no": None, "start_ms": None, "end_ms": None,
            "score": round(0.95 - i * 0.05, 3),
        })
    return out


TOOLS["maps_search"] = maps_search
