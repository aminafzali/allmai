"""Hybrid retrieval: vector + metadata filter + FTS -> RRF -> rerank seam.

PostgreSQL path uses pgvector cosine distance and `to_tsvector('simple')`
(the `simple` config tokenizes Persian without English stemming).
Non-Postgres path (unit tests) uses pure-Python cosine + LIKE scoring so
the fusion logic itself is covered everywhere. Reranking is an optional
callable hook (None in MVP).
"""

import hashlib
import logging
import math
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.common.base import coerce_uuid
from app.core.workspace import require_workspace_id
from app.knowledge.models import Chunk, Document, PageSegment, Source
from app.knowledge.normalize import fa_tokens, normalize_fa

RRF_K = 60
CANDIDATES_MULTIPLIER = 3

logger = logging.getLogger(__name__)

_HYBRID_DEFAULTS = {
    "rrf_k": RRF_K,
    "candidates_multiplier": CANDIDATES_MULTIPLIER,
    "retrieval_top_k": 24,
    "default_final_k": 8,
    "min_score": 0.0,
    "expand_chars": 2000,
    "query_expansion_enabled": True,
    "ready_only": True,
}


def _hybrid_cfg(db=None) -> dict:
    """Effective retrieval.hybrid settings (ai_settings row wins over code
    defaults). Never raises: misconfiguration degrades to defaults loudly
    at the validation step, never to an unscoped search."""
    try:
        from app.ai.settings import list_settings

        cfg = (list_settings(db).get("retrieval.hybrid") or {})
    except Exception:
        cfg = {}
    out = dict(_HYBRID_DEFAULTS)
    out.update({k: v for k, v in cfg.items() if v is not None})
    return out


@dataclass
class ScoredChunk:
    chunk: Chunk
    segment: PageSegment | None
    source: Source | None
    score: float
    vector_rank: int | None = None
    fts_rank: int | None = None
    # Set by API rerankers (AvalAI relevance_score); fused RRF score in
    # `score` is left untouched so ranking provenance stays explicit.
    rerank_score: float | None = None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _is_pg(db: Session) -> bool:
    return db.bind is not None and db.bind.dialect.name == "postgresql"


def _candidates(db: Session, workspace_id, kb_id, source_types=None,
                ready_only=True):
    q = (
        db.query(Chunk, PageSegment, Source)
        .join(PageSegment, PageSegment.id == Chunk.segment_id)
        .join(Document, Document.id == PageSegment.document_id)
        .join(Source, Source.id == Document.source_id)
        .filter(Chunk.workspace_id == coerce_uuid(workspace_id))
    )
    if kb_id is not None:
        q = q.filter(Chunk.kb_id == coerce_uuid(kb_id))
    # Pre-retrieval SQL filters (never post-RRF): only finished sources,
    # optionally narrowed to source types. Half-ingested documents must
    # not answer questions.
    if ready_only:
        q = q.filter(Source.status == "ready")
    if source_types:
        q = q.filter(Source.type.in_(list(source_types)))
    return q


def _global_candidates(db, kb_ids, source_types=None, ready_only=True):
    """Candidates across global rows only (workspace_id IS NULL).

    Callers MUST pass only KBs assigned to the current definition
    (see definitions_service.assigned_global_kb_ids); NULL alone grants
    nothing at the RLS layer either (migration 0006 policies).
    """
    kids = [coerce_uuid(k) for k in (kb_ids or [])]
    q = (
        db.query(Chunk, PageSegment, Source)
        .join(PageSegment, PageSegment.id == Chunk.segment_id)
        .join(Document, Document.id == PageSegment.document_id)
        .join(Source, Source.id == Document.source_id)
        .filter(Chunk.workspace_id.is_(None))
    )
    if kids:
        q = q.filter(Chunk.kb_id.in_(kids))
    if ready_only:
        q = q.filter(Source.status == "ready")
    if source_types:
        q = q.filter(Source.type.in_(list(source_types)))
    return q


def _vector_candidates(db, workspace_id, kb_id, query_vec, limit,
                       source_types=None, ready_only=True):
    rows = _candidates(db, workspace_id, kb_id, source_types, ready_only).all()
    if _is_pg(db):
        # NOTE: Chunk.embedding is a portable TypeDecorator, so pgvector's
        # .cosine_distance() helper is unavailable — use the raw <=> operator,
        # which the underlying VECTOR column binds correctly.
        rows = (
            _candidates(db, workspace_id, kb_id, source_types, ready_only)
            .order_by(Chunk.embedding.op("<=>")(query_vec))
            .limit(limit)
            .all()
        )
        return rows
    scored = sorted(
        rows, key=lambda r: _cosine(query_vec, list(r[0].embedding or [])), reverse=True
    )
    return scored[:limit]


def _fts_candidates(db, workspace_id, kb_id, query: str, limit,
                    source_types=None, ready_only=True):
    query = normalize_fa(query)
    if _is_pg(db):
        tsq = func.plainto_tsquery("simple", query)
        rows = (
            _candidates(db, workspace_id, kb_id, source_types, ready_only)
            .filter(func.to_tsvector("simple", Chunk.content).op("@@")(tsq))
            .order_by(func.ts_rank(func.to_tsvector("simple", Chunk.content), tsq).desc())
            .limit(limit)
            .all()
        )
        return rows
    words = fa_tokens(query)
    scored = []
    for row in _candidates(db, workspace_id, kb_id, source_types, ready_only).all():
        content = normalize_fa(row[0].content or "")
        hits = sum(1 for w in words if w in content)
        if hits:
            scored.append((hits, row))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [r for _, r in scored[:limit]]


def bulk_doc_titles(db: Session, chunk_ids: list) -> dict[str, str]:
    """chunk_id (str) -> document title (bulk queries; missing -> "").

    UUIDs stay UUID objects for the bind processors — only dict keys are
    stringified.
    """
    cids = [coerce_uuid(c) for c in (chunk_ids or [])]
    if not cids:
        return {}
    rows = (db.query(Chunk.id, Chunk.segment_id)
            .filter(Chunk.id.in_(cids)).all())
    seg_ids = list({s for _, s in rows if s is not None})
    seg2doc: dict = dict(
        db.query(PageSegment.id, PageSegment.document_id)
        .filter(PageSegment.id.in_(seg_ids)).all()) if seg_ids else {}
    doc_ids = list({d for d in seg2doc.values() if d is not None})
    titles: dict = dict(
        db.query(Document.id, Document.title)
        .filter(Document.id.in_(doc_ids)).all()) if doc_ids else {}
    out: dict[str, str] = {}
    for c, s in rows:
        d = seg2doc.get(s)
        out[str(c)] = (titles.get(d) or "") if d is not None else ""
    return out


def _apply_title_boost(query: str, hits: list, db: Session) -> list:
    """Post-fusion boost for chunks whose DOCUMENT title or source
    filename covers the query (normalized tokens). The article the user
    names jumps to the top; pure content matches keep their order."""
    qtoks = set(fa_tokens(query))
    if not qtoks or not hits:
        return hits
    titles = bulk_doc_titles(db, [h.chunk.id for h in hits])
    rescored = []
    for h in hits:
        title = titles.get(str(h.chunk.id), "")
        text = f"{title} {(h.source.filename if h.source else '')}"
        ttoks = set(fa_tokens(text))
        cover = len(qtoks & ttoks) / len(qtoks) if ttoks else 0.0
        if cover > 0:
            # RRF scores are small (~0.03); multiplicative boost + a flat
            # bonus for naming the article outright. Bounded by construction.
            h.score = h.score * (1.0 + cover) + (0.25 if cover >= 0.99 else 0.0)
        rescored.append(h)
    rescored.sort(key=lambda h: h.score, reverse=True)
    return rescored


def _title_candidates(base_q, query: str, limit):
    """Third candidate stream: chunks whose DOCUMENT title or source
    filename contains query tokens (normalized). This is what lets
    'the شناخت article' recall its chunks even when embeddings
    point elsewhere — RRF then fuses it with vector/FTS."""
    from sqlalchemy import or_ as _or

    toks = [t for t in fa_tokens(query) if len(t) > 2][:6]
    if not toks:
        return []

    def _esc(t: str) -> str:
        return t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    conds = []
    for t in toks:
        like = f"%{_esc(t)}%"
        conds.append(Document.title.ilike(like))
        conds.append(Source.filename.ilike(like))
    rows = base_q.filter(_or(*conds)).all()
    if not rows:
        return []
    scored = []
    # rank by token coverage over joined doc title + filename
    with_titles = _with_doc_titles(base_q, rows)
    for row, title in with_titles:
        hay = set(fa_tokens(f"{title} {row[2].filename if row[2] else ''}"))
        cover = len([t for t in toks if t in hay])
        if cover:
            scored.append((cover, row))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [r for _, r in scored[:limit]]


def _with_doc_titles(base_q, rows):
    """Attach document titles to candidate rows: [(row, title)]."""
    db = base_q.session
    seg_ids = list({r[1].id for r in rows if r[1] is not None})
    seg2doc = dict(
        db.query(PageSegment.id, PageSegment.document_id)
        .filter(PageSegment.id.in_(seg_ids)).all()) if seg_ids else {}
    doc_ids = list({d for d in seg2doc.values() if d is not None})
    titles = dict(
        db.query(Document.id, Document.title)
        .filter(Document.id.in_(doc_ids)).all()) if doc_ids else {}
    out = []
    for r in rows:
        d = seg2doc.get(r[1].id) if r[1] is not None else None
        out.append((r, titles.get(d, "") if d else ""))
    return out


def _rrf(rank_lists: list[list], top_k: int, rrf_k: int = RRF_K) -> list[tuple]:
    scores: dict[str, float] = {}
    by_id: dict[str, tuple] = {}
    for ranked in rank_lists:
        for rank, row in enumerate(ranked):
            cid = str(row[0].id)
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)
            by_id[cid] = row
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return [(by_id[cid], score) for cid, score in ordered]


def _apply_threshold(hits: list, min_score: float) -> list:
    """Drop weak fused hits. min_score=0.0 is infrastructure-only: counts
    are logged for later tuning, nothing is removed."""
    if not hits or min_score <= 0:
        return hits
    kept = [h for h in hits if (h.score or 0.0) >= min_score]
    dropped = len(hits) - len(kept)
    if dropped:
        logger.info("retrieval threshold: dropped %d/%d hits below %.4f",
                    dropped, len(hits), min_score)
    return kept


def _content_hash(text: str) -> str:
    return hashlib.sha1(normalize_fa(text or "").encode("utf-8")).hexdigest()


def _dedupe_content(hits: list) -> list:
    """Drop near-duplicate chunks (same normalized text, e.g. re-ingested
    copies). Keeps the best-scored copy; order preserved."""
    seen: set[str] = set()
    out = []
    dupes = 0
    for h in hits:
        content = (h.chunk.content or "")
        if not content.strip():
            out.append(h)
            continue
        digest = _content_hash(content)
        if digest in seen:
            dupes += 1
            continue
        seen.add(digest)
        out.append(h)
    if dupes:
        logger.info("retrieval dedup: dropped %d duplicate chunks", dupes)
    return out


def _resolve_limits(top_k: int | None, cfg: dict) -> tuple[int, int]:
    """(final_k, stream limit). Single source of truth: multiplier —
    limit = final_k * candidates_multiplier. The declared retrieval_top_k
    (for the default final_k) is validated, never silently absorbed."""
    mult = max(1, int(cfg.get("candidates_multiplier", CANDIDATES_MULTIPLIER)))
    base_fk = max(1, int(cfg.get("default_final_k", 8)))
    fk = max(1, min(top_k if top_k is not None else base_fk, 50))
    limit = fk * mult
    declared = int(cfg.get("retrieval_top_k", base_fk * mult))
    if declared != base_fk * mult:
        logger.warning(
            "retrieval.hybrid inconsistent: retrieval_top_k=%s != "
            "default_final_k(%s) * multiplier(%s); using computed limit",
            declared, base_fk, mult)
    return fk, limit


def hybrid_search(
    db: Session,
    workspace_id: UUID | str,
    query: str,
    kb_id: UUID | str | None = None,
    top_k: int | None = 8,
    query_vector: list[float] | None = None,
    embed_fn=None,
    rerank_fn="auto",
    source_types: list | None = None,
) -> list[ScoredChunk]:
    """Workspace-scoped hybrid search. query_vector/embed_fn are test seams.
    rerank_fn: "auto" (default, from ai_settings), callable, or None to skip.
    top_k is the FINAL count (final_k); each stream pulls
    final_k * candidates_multiplier candidates pre-fusion.
    """
    require_workspace_id(workspace_id)  # loud error, never an unscoped search
    from app.usage import context as _uctx

    _uctx.bind_usage_context(workspace_id=workspace_id)
    import time as _time

    _u_t0 = _time.perf_counter()
    cfg = _hybrid_cfg(db)
    fk, limit = _resolve_limits(top_k, cfg)
    rrf_k = max(1, int(cfg.get("rrf_k", RRF_K)))
    ready_only = bool(cfg.get("ready_only", True))
    if query_vector is None:
        if embed_fn is None:
            from app.ai.factory import get_embedding_provider

            embed_fn = get_embedding_provider(db).embed
        query_vector = embed_fn([normalize_fa(query)])[0]
    vec_rows = _vector_candidates(db, workspace_id, kb_id, query_vector, limit,
                                  source_types, ready_only)
    fts_rows = _fts_candidates(db, workspace_id, kb_id, query, limit,
                               source_types, ready_only)
    title_rows = _title_candidates(
        _candidates(db, workspace_id, kb_id, source_types, ready_only), query, limit)

    vec_rank = {str(r[0].id): i for i, r in enumerate(vec_rows)}
    fts_rank = {str(r[0].id): i for i, r in enumerate(fts_rows)}
    title_rank = {str(r[0].id): i for i, r in enumerate(title_rows)}
    fused = _rrf([vec_rows, fts_rows, title_rows], fk, rrf_k)

    out = [
        ScoredChunk(chunk=c, segment=s, source=src, score=score,
                    vector_rank=vec_rank.get(str(c.id)), fts_rank=fts_rank.get(str(c.id)))
        for (c, s, src), score in fused
    ]
    out = _apply_title_boost(query, out, db)
    out = _apply_threshold(out, float(cfg.get("min_score", 0.0) or 0.0))
    out = _dedupe_content(out)
    if rerank_fn == "auto":
        from app.knowledge.retrieval.rerank import get_rerank_fn

        rerank_fn = get_rerank_fn(db)
    if rerank_fn is not None:
        out = _timed_rerank(rerank_fn, query, out, fk)
    # NOTE: errors raised ABOVE this line (embedding/DB outage) surface
    # as ok=False MODEL events from the provider hook — the retrieval row
    # itself only records completed searches (keeps this hot path flat).
    from app.usage import recorder as _urec

    _urec.record_event(
        "retrieval", workspace_id=workspace_id,
        latency_ms=round((_time.perf_counter() - _u_t0) * 1000, 1),
        ok=True, meta={"top_k": top_k, "final_hits": len(out),
                       "query_chars": len(query or "")})
    return out


def _timed_rerank(rerank_fn, query: str, hits: list, fk: int) -> list:
    """Run one rerank arm with usage recording (P3).

    Fail-open contract preserved: API/heuristic arms never raise (they
    degrade internally); a raising arm (local stub) records ok=False and
    re-raises exactly as before.
    """
    import time as _time

    from app.usage import recorder as _urec

    name = getattr(rerank_fn, "name", None) or getattr(
        rerank_fn, "__name__", "rerank")
    t0, err = _time.perf_counter(), ""
    try:
        return list(rerank_fn(query, hits, fk)[:fk])
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"[:300]
        raise
    finally:
        _urec.record_event(
            "rerank",
            provider=("avalai" if name == "avalai" else str(name))[:32],
            model=getattr(rerank_fn, "model", None),
            input_chars=sum(len(getattr(getattr(h, "chunk", None),
                                        "content", None) or "") for h in hits),
            latency_ms=round((_time.perf_counter() - t0) * 1000, 1),
            ok=not err, error=err,
            meta={"candidates": len(hits), "final_k": fk})


def hybrid_search_global(
    db: Session,
    query: str,
    kb_ids: list | None,
    top_k: int | None = 8,
    query_vector: list[float] | None = None,
    embed_fn=None,
    rerank_fn="auto",
    source_types: list | None = None,
) -> list[ScoredChunk]:
    """Global-KB hybrid search (workspace_id IS NULL rows only).

    Locked contract: callers pass ONLY kb_ids assigned to the current
    definition. Empty/None kb_ids -> []. RLS independently denies
    unassigned rows on live PG (migration 0006).
    """
    kids = [coerce_uuid(k) for k in (kb_ids or [])]
    if not kids:
        from app.usage import recorder as _urec2

        _urec2.record_event("retrieval", ok=True,
                            meta={"empty": True, "query_chars": len(query or "")})
        return []
    import time as _time

    _u_t0 = _time.perf_counter()
    cfg = _hybrid_cfg(db)
    fk, limit = _resolve_limits(top_k, cfg)
    rrf_k = max(1, int(cfg.get("rrf_k", RRF_K)))
    ready_only = bool(cfg.get("ready_only", True))
    if query_vector is None:
        if embed_fn is None:
            from app.ai.factory import get_embedding_provider

            embed_fn = get_embedding_provider(db).embed
        query_vector = embed_fn([normalize_fa(query)])[0]
    if _is_pg(db):
        vec_rows = (
            _global_candidates(db, kids, source_types, ready_only)
            .order_by(Chunk.embedding.op("<=>")(query_vector))
            .limit(limit)
            .all()
        )
        tsq = func.plainto_tsquery("simple", normalize_fa(query))
        fts_rows = (
            _global_candidates(db, kids, source_types, ready_only)
            .filter(func.to_tsvector("simple", Chunk.content).op("@@")(tsq))
            .order_by(func.ts_rank(func.to_tsvector("simple", Chunk.content), tsq).desc())
            .limit(limit)
            .all()
        )
    else:
        rows = _global_candidates(db, kids, source_types, ready_only).all()
        scored = sorted(
            rows, key=lambda r: _cosine(query_vector, list(r[0].embedding or [])),
            reverse=True,
        )
        vec_rows = scored[:limit]
        words = fa_tokens(query)
        fts_scored = []
        for row in rows:
            content = normalize_fa(row[0].content or "")
            hits = sum(1 for w in words if w in content)
            if hits:
                fts_scored.append((hits, row))
        fts_scored.sort(key=lambda t: t[0], reverse=True)
        fts_rows = [r for _, r in fts_scored[:limit]]
    title_rows = _title_candidates(_global_candidates(db, kids, source_types, ready_only), query, limit)
    vec_rank = {str(r[0].id): i for i, r in enumerate(vec_rows)}
    fts_rank = {str(r[0].id): i for i, r in enumerate(fts_rows)}
    fused = _rrf([vec_rows, fts_rows, title_rows], fk, rrf_k)
    out = [
        ScoredChunk(chunk=c, segment=s, source=src, score=score,
                    vector_rank=vec_rank.get(str(c.id)), fts_rank=fts_rank.get(str(c.id)))
        for (c, s, src), score in fused
    ]
    out = _apply_title_boost(query, out, db)
    out = _apply_threshold(out, float(cfg.get("min_score", 0.0) or 0.0))
    out = _dedupe_content(out)
    if rerank_fn == "auto":
        from app.knowledge.retrieval.rerank import get_rerank_fn

        rerank_fn = get_rerank_fn(db)
    if rerank_fn is not None:
        out = _timed_rerank(rerank_fn, query, out, fk)
    from app.usage import recorder as _urec3

    _urec3.record_event(
        "retrieval",
        latency_ms=round((_time.perf_counter() - _u_t0) * 1000, 1),
        ok=True, meta={"top_k": top_k, "final_hits": len(out),
                       "query_chars": len(query or ""), "global": True})
    return out


def get_embed_fn(db=None):
    """Dependency: embedding callable from the configured provider
    (ai_settings `embedding.default` provider wins over ENV)."""
    from app.ai.factory import get_embedding_provider

    return get_embedding_provider(db).embed


def get_generate_fn(db=None):
    """Dependency: chat callable from the configured provider."""
    from app.ai.factory import get_chat_provider

    return get_chat_provider(db).generate


def get_stream_fn(db=None):
    """Dependency: streaming callable (async generator of deltas)."""
    from app.ai.factory import get_chat_provider

    return get_chat_provider(db).stream


def get_generate_tools_fn(db=None):
    """Dependency: one tool-calling chat round, or None when the
    configured provider offers no tool support (plain answer path)."""
    from app.ai.factory import get_chat_provider

    return getattr(get_chat_provider(db), "generate_with_tools", None)
