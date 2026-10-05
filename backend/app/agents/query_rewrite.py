"""Query rewriting for thin retrieval (fail-open, cost-noted).

When the first retrieval round comes back thin (< 2 merged chunks), one
CHEAP model round rewrites a vague/short user message into up to 3
retrieval-friendly queries (keywords, filename guesses, English
synonyms). Each costs an embedding call (cheap) — never a full chat
turn. Any failure returns [] and the pipeline continues unchanged.

COST NOTE (honest): one extra chat-completions call per thin turn
(~200-400 tokens on the cheap default model) + up to 3 embedding calls.
"""

from __future__ import annotations

import json as _json
import logging
import re as _re

log = logging.getLogger(__name__)

REWRITE_MAX_QUERIES = 3


def rewrite_queries(message: str, history: list | None = None,
                    model: str | None = None) -> list[str]:
    """Vague message -> retrieval queries. [] on any failure."""
    from app.ai.openai_compat import OpenAICompatProvider

    clean = (message or "").strip()
    if not clean:
        return []
    hist = ""
    try:
        turns = [m for m in (history or [])
                 if isinstance(m, dict) and m.get("content")]
        if turns:
            last = turns[-1]
            hist = f"\nPrevious turn ({last.get('role')}): {str(last.get('content'))[:300]}"
    except Exception:
        hist = ""
    prompt = (
        "Rewrite the user's question into up to 3 short search queries for "
        "a Persian document database (filenames + extracted text). Use "
        "keywords, likely filenames, and key nouns; keep Persian words as-is. "
        "Return ONLY a JSON list of strings, e.g. [\"...\", \"...\"]."
        f"\nUser: {clean[:500]}{hist}")
    try:
        provider = OpenAICompatProvider()
        text = provider.generate(prompt, model=model, temperature=0.0,
                                 max_tokens=300) or ""
    except Exception as exc:
        log.warning("query rewrite failed: %s", exc)
        return []
    try:
        m = _re.search(r"\[.*\]", text, _re.S)
        items = _json.loads(m.group(0)) if m else []
    except Exception:
        items = []
    out = []
    for it in items:
        s = str(it or "").strip()[:200]
        if s and s not in out:
            out.append(s)
    return out[:REWRITE_MAX_QUERIES]
