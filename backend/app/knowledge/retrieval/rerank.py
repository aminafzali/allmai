"""Reranker seam: pluggable Provider/Adapter behind `get_rerank_fn`.

Methods (ai_settings `retrieval.rerank.method`):
- none:      reranking disabled (fully valid baseline).
- heuristic: legacy term-coverage rescorer (kept for tests; NOT selectable
             as a default — preserved code, retired contract).
- api:       AvalAIReranker (REST, no OpenAI SDK): model qwen3-rerank.
- local:     interface only (fail-closed until the P4 benchmark picks one).

Unknown methods disable reranking loudly (log) — never a silent
fallback. API failures fail OPEN (fused order + warning) so chat never
500s on a rerank outage.
"""

import logging

logger = logging.getLogger(__name__)

_RERANK_DEFAULTS = {
    "enabled": True,
    "method": "api",
    "provider": "avalai",
    "model": "qwen3-rerank",
    "timeout_s": 12,
    "max_chars_per_doc": 3000,
}


def _hit_text(h) -> tuple[str, str]:
    """(content, title-plus-filename) for a hit in either shape."""
    if hasattr(h, "chunk"):
        content = h.chunk.content or ""
        src = getattr(h, "source", None)
        title = (getattr(src, "filename", None) or "")
    else:
        content = str(h.get("content", ""))
        title = str(h.get("source", ""))
    return content, title


def heuristic_rerank(query: str, hits: list, top_k: int = 8) -> list:
    from app.knowledge.normalize import fa_tokens

    q = fa_tokens(query)
    if not q:
        return hits[:top_k]
    qset = set(q)
    rescored = []
    for h in hits:
        content, title = _hit_text(h)
        toks = fa_tokens(content)
        if not toks:
            rescored.append((0.0, h))
            continue
        overlap = len(qset & set(toks)) / max(len(qset), 1)
        # early-position bonus: matches in the first 30 tokens matter most
        early = len(qset & set(toks[:30])) / max(len(qset), 1)
        norm_content = " ".join(toks)
        phrase = 1.0 if " ".join(q) in norm_content else 0.0
        # title/filename overlap: the user naming the article wins
        ttoks = set(fa_tokens(title))
        tcover = len(qset & ttoks) / max(len(qset), 1) if ttoks else 0.0
        rescored.append((overlap + 0.5 * early + 0.5 * phrase + tcover
                         + 0.01 * h.score, h))
    rescored.sort(key=lambda t: t[0], reverse=True)
    return [h for _, h in rescored[:top_k]]


def get_rerank_fn(db=None):
    """None when disabled/unknown; otherwise the configured method.

    Contract: the returned callable takes (query, hits, top_k=None) and
    returns reordered hits. Unknown methods log loudly and disable —
    silent heuristic fallback was retired with the P1 hardening.
    """
    from app.ai.settings import list_settings

    try:
        cfg = (list_settings(db).get("retrieval.rerank") or {})
    except Exception:
        cfg = {}
    merged = dict(_RERANK_DEFAULTS)
    merged.update({k: v for k, v in cfg.items() if v is not None})
    if not merged.get("enabled", True):
        return None
    method = merged.get("method", "none")
    if method == "none":
        return None
    if method == "heuristic":
        return heuristic_rerank  # legacy only; not a selectable default
    if method == "api" and (merged.get("provider") or "avalai") == "avalai":
        return AvalAIReranker(
            model=merged.get("model") or "qwen3-rerank",
            timeout_s=float(merged.get("timeout_s", 12) or 12),
            max_chars_per_doc=int(merged.get("max_chars_per_doc", 3000) or 3000),
        )
    if method == "local":
        return LocalReranker()
    logger.warning("unknown rerank method %r: reranking disabled", method)
    return None


class RerankerProvider:
    """Adapter interface: (query, hits, top_k) -> reordered hits."""

    name = "base"

    def __call__(self, query: str, hits: list, top_k: int | None = None) -> list:
        raise NotImplementedError


class LocalReranker(RerankerProvider):
    """Local-model slot: interface only until the P4 benchmark picks one.

    Fail-closed on purpose: selecting it executes nothing silently.
    """

    name = "local"

    def __call__(self, query: str, hits: list, top_k: int | None = None) -> list:
        raise NotImplementedError(
            "local reranker model not selected yet (P4 benchmark pending)")


def _set_rerank_score(h, value: float) -> None:
    try:
        h.rerank_score = float(value)
        return
    except Exception:
        pass
    try:
        if isinstance(h, dict):
            h["rerank_score"] = float(value)
    except Exception:
        pass


class AvalAIReranker(RerankerProvider):
    """AvalAI rerank over HTTP/REST (httpx, never the OpenAI SDK).

    Sends fused candidates as plain STRINGS (this deployment rejects the
    documented {id, text} object form with 400) with top_n=final_k, maps
    results back by `index` into the sent order, and returns the ORIGINAL
    hit objects reordered — chunk_id/source/kb/workspace/page survive by
    construction. Accepts both `data` and `results` response keys. Any
    failure (no key, timeout, 4xx/5xx, bad payload) fails OPEN to fused
    order with a warning.
    """

    name = "avalai"
    endpoint_path = "/rerank"

    def __init__(self, model: str = "qwen3-rerank",
                 base_url: str | None = None, api_key: str | None = None,
                 timeout_s: float = 12, max_chars_per_doc: int = 3000):
        from app.core.config import get_settings

        s = get_settings()
        self.model = model
        self.base_url = (base_url or s.AVALAI_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else s.AVALAI_API_KEY
        self.timeout_s = timeout_s
        self.max_chars = max(500, int(max_chars_per_doc or 3000))

    def __call__(self, query: str, hits: list, top_k: int | None = None) -> list:
        import httpx

        if not hits:
            return hits
        if not (self.api_key or "").strip():
            logger.warning("avalai rerank skipped: no AVALAI_API_KEY (fail-open)")
            return hits
        texts = []
        for h in hits:
            content, _ = _hit_text(h)
            texts.append((content or "")[:self.max_chars])
        top_n = top_k or len(texts)
        try:
            resp = httpx.post(
                self.base_url + self.endpoint_path,
                json={"model": self.model, "query": query,
                      "documents": texts, "top_n": top_n,
                      "return_documents": True},
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            payload = resp.json() or {}
            results = payload.get("results", payload.get("data")) or []
        except Exception as exc:
            logger.warning("avalai rerank fail-open (%s: %s); fused order kept",
                           type(exc).__name__, str(exc)[:160])
            return hits
        ranked: list = []
        seen: set[int] = set()
        for r in results if isinstance(results, list) else []:
            if not isinstance(r, dict):
                continue
            idx = r.get("index")
            h = hits[idx] if isinstance(idx, int) and 0 <= idx < len(hits) else None
            if h is None or id(h) in seen:
                continue
            seen.add(id(h))
            try:
                _set_rerank_score(h, float(r.get("relevance_score") or 0.0))
            except (TypeError, ValueError):
                pass
            ranked.append(h)
        for h in hits:  # never lose a candidate the API omitted
            if id(h) not in seen:
                ranked.append(h)
        return ranked[:top_n] if top_n else ranked
