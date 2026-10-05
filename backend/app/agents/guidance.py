"""Academic/career guidance (moshaver): RIASEC personality quiz + O_NET
job matching over DuckDB tables.

- Quiz bank + scoring are static (no LLM needed); results persist as
  memory profile facts (``guidance.riasec*``) so chat, prompts and the
  jobs tab share them.
- Job matching is DETERMINISTIC SQL over ingested CSV/Excel sources that
  carry a RIASEC column (e.g. the O_NET reference table): candidates are
  ranked in Python by overlap with the user's 3-letter code. No LLM
  hallucination in the matching itself; the chat agent only explains.
"""

from __future__ import annotations

import json as _json

from sqlalchemy.orm import Session

from app.common.base import coerce_uuid

RIASEC_TYPES: dict[str, dict] = {
    "R": {"fa": "واقع‌گرا", "en": "Realistic",
          "desc": "عملی و فنی؛ کار با دست، ابزار و ماشین را دوست دارد."},
    "I": {"fa": "پژوهشگر", "en": "Investigative",
          "desc": "تحلیلی و علمی؛ عاشق حل مسئله و کشف ناشناخته‌ها."},
    "A": {"fa": "هنری", "en": "Artistic",
          "desc": "خلاق و بیانی؛ نوآوری و خلق زیبایی برایش مهم است."},
    "S": {"fa": "اجتماعی", "en": "Social",
          "desc": "یاری‌رسان؛ کمک، آموزش و کار گروهی را دوست دارد."},
    "E": {"fa": "کارآفرین", "en": "Enterprising",
          "desc": "رهبر و متقاعدکننده؛ راه‌اندازی و مدیریت را دوست دارد."},
    "C": {"fa": "سازمانی", "en": "Conventional",
          "desc": "منظم و دقیق؛ نظم، داده و جزئیات برایش مهم است."},
}

QUIZ: list[dict] = [
    {"id": "q01", "type": "R", "text": "دوست دارم با دست‌هایم چیزی بسازم یا تعمیر کنم."},
    {"id": "q02", "type": "R", "text": "کار با ماشین‌آلات و ابزار برایم لذت‌بخش است."},
    {"id": "q03", "type": "R", "text": "فعالیت بدنی و عملی را به نشستن پشت میز ترجیح می‌دهم."},
    {"id": "q04", "type": "I", "text": "دوست دارم مسائل پیچیده را تحلیل و حل کنم."},
    {"id": "q05", "type": "I", "text": "از آزمایش کردن و کشف چیزهای جدید لذت می‌برم."},
    {"id": "q06", "type": "I", "text": "دوست دارم درباره موضوعات علمی عمیق بخوانم."},
    {"id": "q07", "type": "A", "text": "دوست دارم چیزهای زیبا خلق کنم (نقاشی، موسیقی، نوشته)."},
    {"id": "q08", "type": "A", "text": "ایده‌های غیرمعمول و نو به ذهنم می‌رسد."},
    {"id": "q09", "type": "A", "text": "دوست دارم خودم را به شکل خلاقانه بیان کنم."},
    {"id": "q10", "type": "S", "text": "دوست دارم به دیگران کمک کنم و مشکلشان را حل کنم."},
    {"id": "q11", "type": "S", "text": "از آموزش دادن به دیگران لذت می‌برم."},
    {"id": "q12", "type": "S", "text": "کار کردن در جمع و تیم را دوست دارم."},
    {"id": "q13", "type": "E", "text": "دوست دارم رهبری یک گروه را بر عهده بگیرم."},
    {"id": "q14", "type": "E", "text": "می‌توانم دیگران را برای ایده‌هایم قانع کنم."},
    {"id": "q15", "type": "E", "text": "دوست دارم کسب‌وکار خودم را راه بیندازم."},
    {"id": "q16", "type": "C", "text": "نظم، برنامه‌ریزی و لیست داشتن را دوست دارم."},
    {"id": "q17", "type": "C", "text": "کار با اعداد، جدول و داده‌ها برایم راحت است."},
    {"id": "q18", "type": "C", "text": "دقت در جزئیات و درست انجام شدن کارها برایم مهم است."},
]

SCALE = [
    {"v": 1, "label": "کاملاً مخالفم"},
    {"v": 2, "label": "مخالفم"},
    {"v": 3, "label": "نظری ندارم"},
    {"v": 4, "label": "موافقم"},
    {"v": 5, "label": "کاملاً موافقم"},
]

_Q_BY_ID = {q["id"]: q for q in QUIZ}

PROFILE_PREFIX = "guidance."


def score_quiz(answers: dict) -> dict:
    """Sum Likert 1..5 per type -> {code, scores, types}. Raises ValueError
    when fewer than 12 questions are answered."""
    scores = {t: 0 for t in RIASEC_TYPES}
    counted = 0
    for qid, val in (answers or {}).items():
        q = _Q_BY_ID.get(str(qid))
        try:
            v = int(val)
        except (TypeError, ValueError):
            continue
        if q is None or v < 1 or v > 5:
            continue
        scores[q["type"]] += v
        counted += 1
    if counted < 12:
        raise ValueError(f"answer at least 12 questions (got {counted})")
    ranked = sorted(scores, key=lambda t: (-scores[t], t))
    code = "".join(ranked[:3])
    return {
        "code": code,
        "scores": scores,
        "types": [{"code": t, **RIASEC_TYPES[t], "score": scores[t]}
                  for t in ranked],
    }


def save_guidance_profile(db: Session, ws, user, result: dict) -> dict:
    from app.auth.service import audit
    from app.memory.postgres_provider import PostgresMemoryProvider

    mem = PostgresMemoryProvider(db)
    mem.remember(ws.id, user.id, PROFILE_PREFIX + "riasec",
                 result["code"], category="profile")
    mem.remember(ws.id, user.id, PROFILE_PREFIX + "riasec_scores",
                 _json.dumps(result["scores"], ensure_ascii=False),
                 category="profile")
    audit(db, "agents.guidance_quiz", actor_user_id=user.id,
          workspace_id=ws.id, entity="user", entity_id=user.id,
          meta={"code": result["code"]})
    return {"code": result["code"], "scores": result["scores"]}


def read_guidance_profile(db: Session, ws, user) -> dict:
    from app.memory.postgres_provider import PostgresMemoryProvider

    facts = {f.key: f.value for f in
             PostgresMemoryProvider(db).facts_for_user(ws.id, user.id)}
    code = facts.get(PROFILE_PREFIX + "riasec")
    if not code:
        return {"code": None, "scores": {}, "types": []}
    try:
        scores = _json.loads(facts.get(PROFILE_PREFIX + "riasec_scores") or "{}")
    except Exception:
        scores = {}
    ranked = sorted(RIASEC_TYPES, key=lambda t: (-int(scores.get(t, 0)), t))
    return {
        "code": code,
        "scores": {t: int(scores.get(t, 0)) for t in RIASEC_TYPES},
        "types": [{"code": t, **RIASEC_TYPES[t],
                   "score": int(scores.get(t, 0))} for t in ranked],
    }


# ---------- O_NET job matching (deterministic DuckDB) ----------

_COL_HINTS = (
    ("title", ("عنوان شغل",)),
    ("group", ("گروه شغلی",)),
    ("education", ("تحصیلات",)),
    ("readiness", ("آمادگی",)),
    ("interests", ("علایق شغلی اصلی", "علایق شغلی")),
    ("description", ("شرح شغل",)),
    ("riasec", ("RIASEC", "رایزک", "تیپ علاقه")),
)


def _pick_col(columns: list[dict], hints: tuple[str, ...]) -> dict | None:
    for c in columns:
        name = str(c.get("name") or "")
        if any(h in name for h in hints):
            return c
    return None


def _code3(value: str) -> str:
    import re as _re

    m = _re.match(r"\s*([A-Z]{3})", str(value or "").upper())
    return m.group(1) if m else ""


def _candidate_tables(db: Session, ws) -> list[tuple]:
    """(source, tables, duck_key) for csv/excel sources whose tables carry
    a RIASEC column — workspace KBs + moshaver-assigned global KBs."""
    from app.agents.definitions_service import assigned_global_kb_ids
    from app.agents.definitions_service import get_definition_by_key
    from app.knowledge.models import KnowledgeBase, Source

    global_kb_ids: set[str] = set()
    try:
        definition = get_definition_by_key(db, "moshaver")
        if definition is not None:
            global_kb_ids = {str(k) for k in
                             assigned_global_kb_ids(db, definition.id)}
    except Exception:
        global_kb_ids = set()
    ws_kb_ids = {str(k.id) for k in
                 db.query(KnowledgeBase)
                 .filter(KnowledgeBase.workspace_id == ws.id).all()}
    rows = (
        db.query(Source)
        .filter(Source.status == "ready",
                Source.type.in_(("csv", "excel")))
        .all()
    )
    out = []
    for src in rows:
        kb = str(src.kb_id)
        allowed = kb in ws_kb_ids or (
            src.workspace_id is None and kb in global_kb_ids)
        if not allowed:
            continue
        meta = (src.parse_meta or {}).get("excel") or {}
        tables = meta.get("tables") or []
        duck_key = meta.get("duckdb_key") or ""
        if tables and duck_key and any(
                _pick_col(t.get("columns") or [], ("RIASEC", "رایزک", "تیپ علاقه"))
                for t in tables):
            out.append((src, tables, duck_key))
    return out


def match_jobs(db: Session, ws, user, code: str, limit: int = 20,
               storage=None) -> dict:
    """Rank O_NET jobs by overlap with the user's 3-letter RIASEC code."""
    from fastapi import HTTPException

    from app.core.config import get_settings
    from app.knowledge.excel.store import run_query

    code = "".join(c for c in (code or "").upper() if c in RIASEC_TYPES)[:3]
    if len(code) < 1:
        raise HTTPException(422, "a RIASEC code (e.g. SIA) is required")
    if storage is None:
        from app.storage.s3 import get_storage

        storage = get_storage()

    def _q(ident: str) -> str:  # quote an allowlisted identifier (meta-sourced)
        return '"' + str(ident).replace('"', '""') + '"'

    cands = _candidate_tables(db, ws)
    if not cands:
        raise HTTPException(404, "no RIASEC job table found in this workspace "
                                 "(or its assigned global KBs)")
    jobs: list[dict] = []
    seen: set[str] = set()
    import re as _re

    for src, tables, duck_key in cands:
        for table in tables:
            if not _re.fullmatch(r"[A-Za-z_][\w$]*", str(table.get("table") or "")):
                continue
            cols = table.get("columns") or []
            if _pick_col(cols, ("RIASEC", "رایزک", "تیپ علاقه")) is None:
                continue
            mapping = {key: _pick_col(cols, hints) for key, hints in _COL_HINTS}
            if mapping["title"] is None or mapping["riasec"] is None:
                continue
            # Injection-safe by construction: table/columns come from the
            # allowlisted ingest metadata (exact membership, quoted); the
            # LIKE letters come from the RIASEC alphabet only.
            cols_allow = {c.get("col") for c in cols}
            want = [m["col"] for m in mapping.values() if m is not None]
            if any(c not in cols_allow for c in want):
                continue
            select = ", ".join(_q(c) for c in dict.fromkeys(want))
            conds = " OR ".join(
                f"{_q(mapping['riasec']['col'])} ILIKE '%{ch}%'" for ch in code)
            sql = f"SELECT {select} FROM {_q(table['table'])} WHERE {conds}"
            try:
                _, rows = run_query(storage, duck_key, sql,
                                    timeout_s=int(get_settings().EXCEL_QUERY_TIMEOUT_S))
            except Exception:
                continue
            idx = {key: (want.index(m["col"]) if m is not None else None)
                   for key, m in mapping.items()}
            for r in rows:
                def _g(k):
                    i = idx[k]
                    return "" if i is None else str(r[i] or "")

                c3 = _code3(_g("riasec"))
                if not c3:
                    continue
                matched = [c for c in code if c in c3]
                if not matched:
                    continue
                title = _g("title").strip()
                key = f"{title}|{_g('group')}"
                if not title or key in seen:
                    continue
                seen.add(key)
                jobs.append({
                    "title": title[:200], "group": _g("group")[:200],
                    "education": _g("education")[:200],
                    "readiness": _g("readiness")[:200],
                    "interests": _g("interests")[:500],
                    "description": _g("description")[:1000],
                    "riasec": c3, "matched": len(matched),
                    "source": src.filename or "",
                })
    jobs.sort(key=lambda j: (-j["matched"], j["title"]))
    return {"code": code, "jobs": jobs[:max(1, min(50, limit or 20))],
            "total": len(jobs)}
