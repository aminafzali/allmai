"""Hybrid retrieval: vector + metadata filter + FTS -> RRF -> rerank seam.

PostgreSQL path uses pgvector cosine distance and `to_tsvector('simple')`
(the `simple` config tokenizes Persian without English stemming).
Non-Postgres path (unit tests) uses pure-Python cosine + LIKE scoring so
the fusion logic itself is covered everywhere. Reranking is an optional
callable hook (None in MVP).
"""

import math
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.common.base import coerce_uuid
from app.core.workspace import require_workspace_id
from app.knowledge.models import Chunk, Document, PageSegment, Source

RRF_K = 60
CANDIDATES_MULTIPLIER = 3


@dataclass
class ScoredChunk:
    chunk: Chunk
    segment: PageSegment | None
    source: Source | None
    score: float
    vector_rank: int | None = None
    fts_rank: int | None = None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _is_pg(db: Session) -> bool:
    return db.bind is not None and db.bind.dialect.name == "postgresql"


def _candidates(db: Session, workspace_id, kb_id):
    q = (
        db.query(Chunk, PageSegment, Source)
        .join(PageSegment, PageSegment.id == Chunk.segment_id)
        .join(Document, Document.id == PageSegment.document_id)
        .join(Source, Source.id == Document.source_id)
        .filter(Chunk.workspace_id == coerce_uuid(workspace_id))
    )
    if kb_id is not None:
        q = q.filter(Chunk.kb_id == coerce_uuid(kb_id))
    return q


def _global_candidates(db, kb_ids):
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
    return q


def _vector_candidates(db, workspace_id, kb_id, query_vec, limit):
    rows = _candidates(db, workspace_id, kb_id).all()
    if _is_pg(db):
        # NOTE: Chunk.embedding is a portable TypeDecorator, so pgvector's
        # .cosine_distance() helper is unavailable — use the raw <=> operator,
        # which the underlying VECTOR column binds correctly.
        rows = (
            _candidates(db, workspace_id, kb_id)
            .order_by(Chunk.embedding.op("<=>")(query_vec))
            .limit(limit)
            .all()
        )
        return rows
    scored = sorted(
        rows, key=lambda r: _cosine(query_vec, list(r[0].embedding or [])), reverse=True
    )
    return scored[:limit]


def _fts_candidates(db, workspace_id, kb_id, query: str, limit):
    if _is_pg(db):
        tsq = func.plainto_tsquery("simple", query)
        rows = (
            _candidates(db, workspace_id, kb_id)
            .filter(func.to_tsvector("simple", Chunk.content).op("@@")(tsq))
            .order_by(func.ts_rank(func.to_tsvector("simple", Chunk.content), tsq).desc())
            .limit(limit)
            .all()
        )
        return rows
    words = [w for w in query.split() if len(w) > 1]
    scored = []
    for row in _candidates(db, workspace_id, kb_id).all():
        content = row[0].content or ""
        hits = sum(1 for w in words if w in content)
        if hits:
            scored.append((hits, row))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [r for _, r in scored[:limit]]


def _rrf(rank_lists: list[list], top_k: int) -> list[tuple]:
    scores: dict[str, float] = {}
    by_id: dict[str, tuple] = {}
    for ranked in rank_lists:
        for rank, row in enumerate(ranked):
            cid = str(row[0].id)
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            by_id[cid] = row
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return [(by_id[cid], score) for cid, score in ordered]


def hybrid_search(
    db: Session,
    workspace_id: UUID | str,
    query: str,
    kb_id: UUID | str | None = None,
    top_k: int = 8,
    query_vector: list[float] | None = None,
    embed_fn=None,
    rerank_fn="auto",
) -> list[ScoredChunk]:
    """Workspace-scoped hybrid search. query_vector/embed_fn are test seams.
    rerank_fn: "auto" (default, from ai_settings), callable, or None to skip.
    """
    require_workspace_id(workspace_id)  # loud error, never an unscoped search
    top_k = max(1, min(top_k, 50))
    if query_vector is None:
        if embed_fn is None:
            from app.ai.factory import get_embedding_provider

            embed_fn = get_embedding_provider(db).embed
        query_vector = embed_fn([query])[0]
    limit = top_k * CANDIDATES_MULTIPLIER
    vec_rows = _vector_candidates(db, workspace_id, kb_id, query_vector, limit)
    fts_rows = _fts_candidates(db, workspace_id, kb_id, query, limit)

    vec_rank = {str(r[0].id): i for i, r in enumerate(vec_rows)}
    fts_rank = {str(r[0].id): i for i, r in enumerate(fts_rows)}
    fused = _rrf([vec_rows, fts_rows], top_k)

    out = [
        ScoredChunk(chunk=c, segment=s, source=src, score=score,
                    vector_rank=vec_rank.get(str(c.id)), fts_rank=fts_rank.get(str(c.id)))
        for (c, s, src), score in fused
    ]
    if rerank_fn == "auto":
        from app.knowledge.retrieval.rerank import get_rerank_fn

        rerank_fn = get_rerank_fn(db)
    if rerank_fn is not None:
        out = rerank_fn(query, out)[:top_k]
    return out


def hybrid_search_global(
    db: Session,
    query: str,
    kb_ids: list | None,
    top_k: int = 8,
    query_vector: list[float] | None = None,
    embed_fn=None,
    rerank_fn=None,
) -> list[ScoredChunk]:
    """Global-KB hybrid search (workspace_id IS NULL rows only).

    Locked contract: callers pass ONLY kb_ids assigned to the current
    definition. Empty/None kb_ids -> []. RLS independently denies
    unassigned rows on live PG (migration 0006).
    """
    kids = [coerce_uuid(k) for k in (kb_ids or [])]
    if not kids:
        return []
    top_k = max(1, min(top_k, 50))
    if query_vector is None:
        if embed_fn is None:
            from app.ai.factory import get_embedding_provider

            embed_fn = get_embedding_provider(db).embed
        query_vector = embed_fn([query])[0]
    limit = top_k * CANDIDATES_MULTIPLIER
    if _is_pg(db):
        vec_rows = (
            _global_candidates(db, kids)
            .order_by(Chunk.embedding.op("<=>")(query_vector))
            .limit(limit)
            .all()
        )
        tsq = func.plainto_tsquery("simple", query)
        fts_rows = (
            _global_candidates(db, kids)
            .filter(func.to_tsvector("simple", Chunk.content).op("@@")(tsq))
            .order_by(func.ts_rank(func.to_tsvector("simple", Chunk.content), tsq).desc())
            .limit(limit)
            .all()
        )
    else:
        rows = _global_candidates(db, kids).all()
        scored = sorted(
            rows, key=lambda r: _cosine(query_vector, list(r[0].embedding or [])),
            reverse=True,
        )
        vec_rows = scored[:limit]
        words = [w for w in query.split() if len(w) > 1]
        fts_scored = []
        for row in rows:
            content = row[0].content or ""
            hits = sum(1 for w in words if w in content)
            if hits:
                fts_scored.append((hits, row))
        fts_scored.sort(key=lambda t: t[0], reverse=True)
        fts_rows = [r for _, r in fts_scored[:limit]]
    vec_rank = {str(r[0].id): i for i, r in enumerate(vec_rows)}
    fts_rank = {str(r[0].id): i for i, r in enumerate(fts_rows)}
    fused = _rrf([vec_rows, fts_rows], top_k)
    out = [
        ScoredChunk(chunk=c, segment=s, source=src, score=score,
                    vector_rank=vec_rank.get(str(c.id)), fts_rank=fts_rank.get(str(c.id)))
        for (c, s, src), score in fused
    ]
    if rerank_fn is not None:
        out = rerank_fn(query, out)[:top_k]
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
