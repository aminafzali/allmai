"""Instagram provider (best-effort, login-wall aware).

Primary: page fetch + OpenGraph tags (og:title/og:description) + visible
text. Embedded login walls yield little text -> the extractor still
returns the URL + whatever metadata exists instead of failing the job.
Direct media URLs are NOT chased (auth-gated); audio transcription only
applies when a caller already holds media bytes (reserved seam).
"""

import re


def _og_text(html: str) -> tuple[str, str]:
    title = desc = ""
    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"',
                  html, re.I)
    if m:
        title = m.group(1).strip()
    m = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"',
                  html, re.I)
    if m:
        desc = m.group(1).strip()
    return title, desc


def extract_instagram(url: str, fetch_fn=None):
    from app.knowledge.parsers.providers import ExtractedURL
    from app.knowledge.parsers.url import fetch_url_text

    fetch = fetch_fn or fetch_url_text
    try:
        text = (fetch(url) or "").strip()
    except Exception:
        text = ""
    title, desc = "", ""
    if text:
        lines = text.splitlines()
        title, desc = (lines[0][:300] if lines else ""), ""
    # og: tags carry the real caption even when visible text is a login wall
    try:
        import httpx

        from app.knowledge.parsers.url import FETCH_MAX_BYTES, FETCH_TIMEOUT

        r = httpx.get(url, timeout=FETCH_TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": "AllMai-KB/0.1"})
        r.raise_for_status()
        og_title, og_desc = _og_text(
            r.content[:FETCH_MAX_BYTES].decode("utf-8", errors="replace"))
        if og_title:
            title = og_title[:300]
        if og_desc and og_desc not in text:
            text = f"{text}\n{og_desc}".strip() if text else og_desc
    except Exception:
        pass
    if not text:
        text = f"{title or 'Instagram post'}\n{url}".strip()
    return ExtractedURL(provider="instagram", url=url,
                        title=title or "Instagram post", kind="text",
                        text=text, meta={"og": bool(title)})
