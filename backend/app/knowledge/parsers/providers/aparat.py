"""Aparat provider: videohash API + page fallback, opportunistic audio.

Primary: `etc/api/video/videohash/{hash}` (no auth) gives title, author,
duration, description AND per-quality mp4 links. The SMALLEST quality is
downloaded (capped) and transcribed — the full video is never stored.
Any network step degrades to text-only (title + description + URL), never
a job failure. The failure reason is recorded in meta for transparency.
"""

import re

_HASH_RE = re.compile(r"/v/([A-Za-z0-9]+)")


def videohash(url: str) -> str:
    m = _HASH_RE.search(url or "")
    return m.group(1) if m else ""


def _video_info(vhash: str) -> dict:
    import httpx

    from app.knowledge.parsers.url import FETCH_TIMEOUT

    r = httpx.get(f"https://www.aparat.com/etc/api/video/videohash/{vhash}",
                  timeout=FETCH_TIMEOUT,
                  headers={"User-Agent": "AllMai-KB/0.1"})
    r.raise_for_status()
    return r.json().get("video") or {}


def _smallest_mp4(info: dict) -> str:
    cands: list[tuple[int, str]] = []

    def size_of(text: str) -> int:
        m = re.search(r"([\d.]+)\s*مگابایت", text or "")
        try:
            return int(float(m.group(1)) * 1024 * 1024) if m else 10 ** 12
        except (ValueError, AttributeError):
            return 10 ** 12

    for item in (info.get("file_link_all") or []):
        for u in (item.get("urls") or []):
            if ".mp4" in u:
                cands.append((size_of(item.get("text", "")), u))
    if isinstance(info.get("file_link"), str) and ".mp4" in info["file_link"]:
        cands.append((10 ** 12, info["file_link"]))
    cands.sort(key=lambda t: t[0])
    return cands[0][1] if cands else ""


def extract_aparat(url: str, fetch_fn=None, transcribe_fn=None):
    from app.knowledge.parsers.providers import ExtractedURL
    from app.knowledge.parsers.url import fetch_url_text

    fetch = fetch_fn or fetch_url_text
    vhash = videohash(url)
    title, author, desc, media = "", "", "", ""
    err = ""
    if vhash:
        try:
            info = _video_info(vhash)
            title = str(info.get("title") or "")[:300]
            author = str(info.get("sender_name") or info.get("username") or "")
            desc = str(info.get("description") or "").strip()
            media = _smallest_mp4(info)
        except Exception as exc:
            err = f"aparat api: {type(exc).__name__}"
    if not title:
        try:
            text = (fetch(url) or "").strip()
            if text and not title:
                title = text.splitlines()[0][:300]
        except Exception as exc:
            err = (err + f" | page: {type(exc).__name__}").strip(" |")
    # Opportunistic audio: smallest mp4 -> transcription (capped).
    if media:
        if transcribe_fn is None:
            from app.knowledge.parsers.audio import transcribe as transcribe_fn
        try:
            import httpx

            from app.core.config import get_settings
            from app.knowledge.parsers.url import FETCH_TIMEOUT

            with httpx.stream("GET", media, timeout=FETCH_TIMEOUT,
                              follow_redirects=True,
                              headers={"User-Agent": "AllMai-KB/0.1"}) as resp:
                resp.raise_for_status()
                buf = bytearray()
                cap = int(get_settings().VIDEO_MAX_BYTES)
                for chunk in resp.iter_bytes(65536):
                    buf += chunk
                    if len(buf) > cap:
                        break
            if buf:
                from app.knowledge.parsers.video import transcribe_video

                segs = transcribe_video(bytes(buf), "video.mp4",
                                        transcribe_fn=transcribe_fn)
                segs = [(a, b, t) for a, b, t in segs if (t or "").strip()]
                if segs:
                    return ExtractedURL(
                        provider="aparat", url=url,
                        title=title or "Aparat video", kind="transcript",
                        segments=segs,
                        meta={"author": author, "duration_s": None})
        except Exception as exc:
            err = (err + f" | media: {type(exc).__name__}").strip(" |")
    body = "\n".join(p for p in
                     [title, f"by {author}" if author else "",
                      desc, url] if p).strip()
    return ExtractedURL(provider="aparat", url=url,
                        title=title or "Aparat video", kind="text",
                        text=body or url,
                        meta={"author": author,
                              "error": err or "no transcript available"})
