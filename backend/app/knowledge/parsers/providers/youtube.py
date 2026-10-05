"""YouTube provider: captions first (free, timestamped), audio fallback.

1. `extract_info(download=False)` -> title + subtitles/automatic_captions.
   The first usable track (fa, then en, then any) is downloaded and parsed
   (VTT/SRT/TTML tolerant) into [(start_ms, end_ms, text)].
2. No captions -> bestaudio ONLY via yt-dlp (FFmpegExtractAudio to mp3,
   capped at VIDEO_MAX_BYTES; the full video is never stored) into a temp
   dir -> transcribe_fn (the worker/test seam).
"""

import os
import re
import tempfile

_VTT_TS = re.compile(r"(?:(\d+):)?(\d+):(\d+)[.,](\d+)\s*-->\s*"
                     r"(?:(\d+):)?(\d+):(\d+)[.,](\d+)")
_TAG_RE = re.compile(r"<[^>]+>")


def _to_ms(h, m, s, frac) -> int:
    h = int(h) if h else 0
    return ((h * 3600 + int(m) * 60 + int(s)) * 1000
            + int((frac + "000")[:3]))


def _clock_to_ms(clock: str) -> int:
    parts = clock.replace(",", ".").split(":")
    sec = float(parts[-1])
    total = sec
    mul = 1
    for p in reversed(parts[:-1]):
        mul *= 60
        total += int(p) * mul
    return int(total * 1000)


def parse_subtitles(raw: str) -> list[tuple[int, int, str]]:
    """Parse VTT/SRT/TTML-ish cue text into segments (tolerant)."""
    # TTML/XML shape: <p begin="00:00:01.000" end="00:00:04.000">text</p>
    segs: list[tuple[int, int, str]] = []
    for m in re.finditer(
            r'<p\s+begin="([\d:.]+)"\s+end="([\d:.]+)"[^>]*>(.*?)</p>',
            raw or "", re.S | re.I):
        try:
            s_ms = _clock_to_ms(m.group(1))
            e_ms = _clock_to_ms(m.group(2))
            text = _TAG_RE.sub("", m.group(3)).strip()
            if text:
                segs.append((s_ms, e_ms, text))
        except (ValueError, IndexError):
            continue
    if segs:
        return segs
    cur_s = cur_e = None
    buf: list[str] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        m = _VTT_TS.search(line)
        if m:
            if cur_s is not None and buf:
                text = _TAG_RE.sub("", " ".join(buf)).strip()
                if text:
                    segs.append((cur_s, cur_e or cur_s, text))
            cur_s = _to_ms(m.group(1), m.group(2), m.group(3), m.group(4))
            cur_e = _to_ms(m.group(5), m.group(6), m.group(7), m.group(8))
            buf = []
        elif line and line != "WEBVTT" and not line.isdigit() and cur_s is not None:
            buf.append(line)
        elif not line and cur_s is not None and buf:
            text = _TAG_RE.sub("", " ".join(buf)).strip()
            if text:
                segs.append((cur_s, cur_e or cur_s, text))
            cur_s, cur_e, buf = None, None, []
    if cur_s is not None and buf:
        text = _TAG_RE.sub("", " ".join(buf)).strip()
        if text:
            segs.append((cur_s, cur_e or cur_s, text))
    return segs


def _caption_track(info: dict) -> dict | None:
    for key in ("subtitles", "automatic_captions"):
        tracks = info.get(key) or {}
        for lang in ("fa", "en"):
            cands = [t for t in (tracks.get(lang) or []) if t.get("url")]
            if cands:
                # prefer vtt/srt over ttml for simpler parsing
                cands.sort(key=lambda t: 0 if (t.get("ext") in ("vtt", "srt")) else 1)
                return cands[0]
        for lang, cands in tracks.items():
            cands = [t for t in (cands or []) if t.get("url")]
            if cands:
                return cands[0]
    return None


def _download_text(url: str) -> str:
    import httpx

    from app.knowledge.parsers.url import FETCH_MAX_BYTES, FETCH_TIMEOUT

    r = httpx.get(url, timeout=FETCH_TIMEOUT, follow_redirects=True,
                  headers={"User-Agent": "AllMai-KB/0.1"})
    r.raise_for_status()
    return r.content[:FETCH_MAX_BYTES].decode("utf-8", errors="replace")


def _proxy() -> str | None:
    """User VPN/proxy support: HTTPS_PROXY / HTTP_PROXY / ALL_PROXY env."""
    import os

    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                "https_proxy", "http_proxy", "all_proxy"):
        if os.environ.get(key, "").strip():
            return os.environ[key].strip()
    return None


def extract_youtube(url: str, transcribe_fn=None):
    """Captions-first YouTube extraction (offline-testable seams)."""
    from app.knowledge.parsers.providers import ExtractedURL

    import yt_dlp

    base_opts: dict = {"quiet": True, "no_warnings": True}
    proxy = _proxy()
    if proxy:
        base_opts["proxy"] = proxy
    info: dict = {}
    try:
        with yt_dlp.YoutubeDL({**base_opts, "skip_download": True,
                               "socket_timeout": 30, "retries": 2}) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as exc:
        raise RuntimeError(
            f"youtube unreachable ({type(exc).__name__}): "
            f"check network/VPN access to youtube.com"
            + (" (no proxy configured)" if not proxy else " (proxy used)")) from exc
    title = str(info.get("title") or url)[:300]
    track = _caption_track(info)
    if track:
        try:
            segs = parse_subtitles(_download_text(track["url"]))
        except Exception:
            segs = []
        if segs:
            return ExtractedURL(provider="youtube", url=url, title=title,
                                kind="transcript", segments=segs,
                                meta={"captions": True,
                                      "lang": str(track.get("ext") or "")})
    # Fallback: audio only (never the full video), then transcription API.
    if transcribe_fn is None:
        from app.knowledge.parsers.audio import transcribe as transcribe_fn
    from app.core.config import get_settings

    s = get_settings()
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        ffmpeg_loc = os.path.dirname(get_ffmpeg_exe())
    except Exception:
        ffmpeg_loc = None
    with tempfile.TemporaryDirectory(prefix="allmai-yt-") as tmp:
        opts = {**base_opts, "socket_timeout": 60,
                "retries": 2, "format": "bestaudio/best",
                "max_filesize": int(s.VIDEO_MAX_BYTES),
                "outtmpl": os.path.join(tmp, "au.%(ext)s"),
                "postprocessors": [{"key": "FFmpegExtractAudio",
                                    "preferredcodec": "mp3",
                                    "preferredquality": "64"}]}
        if ffmpeg_loc:
            opts["ffmpeg_location"] = ffmpeg_loc
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception as exc:
            raise RuntimeError(f"youtube audio failed: {type(exc).__name__}") from exc
        mp3 = os.path.join(tmp, "au.mp3")
        if not os.path.exists(mp3) or os.path.getsize(mp3) == 0:
            raise RuntimeError("youtube audio produced no output")
        with open(mp3, "rb") as f:
            blob = f.read()
    segs = [(a, b, t) for a, b, t in transcribe_fn(blob, "audio.mp3")
            if (t or "").strip()]
    if not segs:
        raise RuntimeError("youtube transcript empty")
    return ExtractedURL(provider="youtube", url=url, title=title,
                        kind="transcript", segments=segs,
                        meta={"captions": False})
