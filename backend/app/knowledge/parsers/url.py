"""Full URL fetching at ingest time (Phase B done).

Fetches the page live (15s timeout, 2MB cap, HTML -> text via bs4),
falling back to the stored title/url descriptor on any failure so the
job never fails just because a site is down. The fetcher is injectable
for tests.
"""

import httpx

FETCH_TIMEOUT = 15.0
FETCH_MAX_BYTES = 2 * 1024 * 1024


def fetch_url_text(url: str) -> str:
    """Return page title + visible text. Raises on failure."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"refusing to fetch non-http url: {url!r}")
    try:
        r = httpx.get(url, timeout=FETCH_TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": "AllMai-KB/0.1"})
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"fetch failed: {exc}") from exc
    raw = r.content[:FETCH_MAX_BYTES]
    ctype = r.headers.get("content-type", "")
    if "html" not in ctype:
        try:
            return raw.decode("utf-8", errors="replace")[:20000]
        except Exception as exc:
            raise RuntimeError(f"cannot decode {ctype}") from exc
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    title = (soup.title.string or "").strip() if soup.title else ""
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    body = "\n".join(lines)[:20000]
    if not body:
        raise RuntimeError("empty page text")
    return f"{title}\n{body}".strip() if title else body
