"""Web search via the ALREADY-PAID GapGPT gateway (Responses API).

POST {OPENAI_COMPAT_BASE_URL}/responses with tools=[{type: web_search}].
GapGPT executes the search on ITS side and returns an OpenAI-compatible
response: assistant message (output_text) + url_citation annotations
(title/uri/content snippet). No new keys, no new vendors, no direct
Google dependency. Billed on the existing GapGPT account (~$0.007/req
observed for gpt-4o-mini; the routing model is configurable).
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)


def gapgpt_grounded_search(query: str, model: str | None = None,
                           timeout: float = 120.0) -> dict:
    """Returns {"text", "chunks": [{title, uri, snippet}], "queries"}.

    Raises RuntimeError when the gateway lacks web_search or fails.
    """
    s = get_settings()
    if not s.OPENAI_COMPAT_API_KEY:
        raise RuntimeError("OPENAI_COMPAT_API_KEY is not configured")
    url = f"{s.OPENAI_COMPAT_BASE_URL.rstrip('/')}/responses"
    try:
        r = httpx.post(
            url,
            headers={"Authorization": f"Bearer {s.OPENAI_COMPAT_API_KEY}"},
            json={"model": model or s.GAPGPT_SEARCH_MODEL,
                  "input": (query or "").strip(),
                  "tools": [{"type": "web_search"}]},
            timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"GapGPT search failed: {exc}") from exc
    text_parts: list[str] = []
    chunks: list[dict] = []
    queries: list[str] = []
    for item in data.get("output") or []:
        itype = str(item.get("type") or "")
        if "web_search" in itype:
            action = item.get("action") or {}
            if isinstance(action, dict):
                if action.get("query"):
                    queries.append(str(action["query"]))
                for src in action.get("sources") or []:
                    if isinstance(src, dict) and src.get("url"):
                        chunks.append({"title": str(src.get("title") or src["url"]),
                                       "uri": str(src["url"]), "snippet": ""})
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") in ("output_text", "text") and c.get("text"):
                    text_parts.append(str(c["text"]))
                for a in c.get("annotations") or []:
                    if (isinstance(a, dict)
                            and a.get("type") == "url_citation" and a.get("url")):
                        chunks.append({
                            "title": str(a.get("title") or a["url"])[:300],
                            "uri": str(a["url"])[:1000],
                            "snippet": str(a.get("content") or "")[:2000]})
    # de-dupe by uri, keep order
    seen: set[str] = set()
    uniq: list[dict] = []
    for ch in chunks:
        key = ch["uri"] or ch["title"]
        if key and key not in seen:
            seen.add(key)
            uniq.append(ch)
    if not text_parts and not uniq:
        raise RuntimeError("GapGPT search returned no usable output")
    return {"text": "\n".join(text_parts).strip(),
            "chunks": uniq, "queries": queries}
