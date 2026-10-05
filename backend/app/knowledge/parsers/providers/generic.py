"""Generic provider: current fetch_url_text behavior, wrapped."""

from app.knowledge.parsers.providers import ExtractedURL


def extract_generic(url: str, fetch_fn=None) -> ExtractedURL:
    from app.knowledge.parsers.url import fetch_url_text

    fetch = fetch_fn or fetch_url_text
    text = (fetch(url) or "").strip()
    if not text:
        raise RuntimeError("empty page text")
    lines = text.splitlines()
    title = lines[0][:300] if lines else url
    return ExtractedURL(provider="generic", url=url, title=title,
                        kind="text", text=text)
