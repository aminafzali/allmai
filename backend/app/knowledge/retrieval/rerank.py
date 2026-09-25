"""Reranker seam: a cheap heuristic today, a cross-encoder tomorrow.

`heuristic_rerank` re-scores fused hits by exact-phrase/term coverage
(weighting titles/first sentences), which fixes the classic dense-retrieval
miss where the right chunk embeds far from a short query. Signature matches
`hybrid_search(..., rerank_fn=...)`. A future cross-encoder implements the
same `(query, hits) -> hits` contract behind `retrieval.rerank.method`
(an ai_settings key; unknown methods fall back to heuristic).
"""


def _tokens(text: str) -> list[str]:
    return [t for t in text.lower().split() if len(t) > 1]


def heuristic_rerank(query: str, hits: list, top_k: int = 8) -> list:
    q = _tokens(query)
    if not q:
        return hits[:top_k]
    qset = set(q)
    rescored = []
    for h in hits:
        content = (h.chunk.content or "") if hasattr(h, "chunk") else str(h.get("content", ""))
        toks = _tokens(content)
        if not toks:
            rescored.append((0.0, h))
            continue
        overlap = len(qset & set(toks)) / max(len(qset), 1)
        # early-position bonus: matches in the first 30 tokens matter most
        early = len(qset & set(toks[:30])) / max(len(qset), 1)
        phrase = 1.0 if query.lower().strip() in content.lower() else 0.0
        rescored.append((overlap + 0.5 * early + 0.5 * phrase + 0.01 * h.score, h))
    rescored.sort(key=lambda t: t[0], reverse=True)
    return [h for _, h in rescored[:top_k]]


def get_rerank_fn(db=None):
    """None when disabled in ai_settings; otherwise the configured method."""
    from app.ai.settings import list_settings

    try:
        cfg = (list_settings(db).get("retrieval.rerank") or {})
    except Exception:
        cfg = {}
    if not cfg.get("enabled", True):
        return None
    method = cfg.get("method", "heuristic")
    if method == "heuristic":
        return heuristic_rerank
    return heuristic_rerank  # unknown methods fall back safely
