"""Audio transcription (MVP decision: no local models).

Two GapGPT-routed routes, selected by model id (WHISPER_API_MODEL):
- `whisper-*` (default `whisper-1`): `/audio/transcriptions` endpoint;
  `verbose_json` preserves segment timestamps into PageSegments.
- `gemini-*`: chat completions with audio input (no transcription
  endpoint needed); single segment, no timestamps. No Google key —
  the SAME OpenAI-compatible endpoint + key as everything else.
Provider-agnostic: any endpoint implementing these shapes works by
changing ENV.
"""

import io

import httpx

from app.core.config import get_settings

_AUDIO_MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}


def _mime_for(filename: str) -> str:
    name = (filename or "").lower()
    for ext, mime in _AUDIO_MIME.items():
        if name.endswith(ext):
            return mime
    return "audio/mpeg"


class TranscriptionUnavailable(RuntimeError):
    pass


def _upload_name(filename: str) -> str:
    """ASCII-safe multipart filename (GapGPT-specific pitfall).

    The provider detects the audio format from the upload filename;
    non-ASCII names (e.g. Persian) break detection with
    `400 Unrecognized file format`. The original name stays in our DB.
    """
    name = (filename or "").lower()
    for ext in (".mp3", ".wav", ".m4a"):
        if name.endswith(ext):
            return f"audio{ext}"
    return "audio.mp3"


def transcribe_whisper_api(blob: bytes, filename: str,
                           model: str | None = None) -> list[tuple[int, int, str]]:
    """Return [(start_ms, end_ms, text)]. Raises on API failure."""
    s = get_settings()
    if not s.OPENAI_COMPAT_API_KEY:
        raise TranscriptionUnavailable("no AI API key configured")
    url = s.OPENAI_COMPAT_BASE_URL.rstrip("/") + "/audio/transcriptions"
    files = {"file": (_upload_name(filename), io.BytesIO(blob), _mime_for(filename))}
    data = {"model": model or s.WHISPER_API_MODEL, "response_format": "verbose_json"}
    try:
        r = httpx.post(
            url,
            headers={"Authorization": f"Bearer {s.OPENAI_COMPAT_API_KEY}"},
            files=files,
            data=data,
            timeout=300,
        )
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = ""
        try:
            body = (exc.response.text or "")[:500]
        except Exception:
            body = ""
        raise TranscriptionUnavailable(
            f"whisper API failed: {exc} | response: {body}") from exc
    except httpx.HTTPError as exc:
        raise TranscriptionUnavailable(f"whisper API failed: {exc}") from exc
    out: list[tuple[int, int, str]] = []
    for seg in r.json().get("segments", []):
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        out.append((int(float(seg.get("start", 0)) * 1000), int(float(seg.get("end", 0)) * 1000), text))
    if not out:  # plain (non-verbose) response shape
        text = str(r.json().get("text", "")).strip()
        if text:
            out.append((0, 0, text))
    return out


def transcribe_via_gemini_chat(blob: bytes, filename: str,
                               model: str) -> list[tuple[int, int, str]]:
    """Gemini-via-GapGPT transcription (chat + audio input, live-verified).

    Returns a single [(0, 0, text)] segment — no timestamps on this route.
    Empty reply -> [] (caller treats it as no content, never as an error).
    """
    from app.ai.openai_compat import OpenAICompatProvider

    name = (filename or "").lower()
    fmt = "wav" if name.endswith(".wav") else "mp3"
    try:
        text = (OpenAICompatProvider().transcribe_audio(blob, fmt, model=model)
                or "").strip()
    except Exception as exc:
        raise TranscriptionUnavailable(f"gemini audio failed: {exc}") from exc
    return [(0, 0, text)] if text else []


def transcribe(blob: bytes, filename: str,
               model: str | None = None) -> list[tuple[int, int, str]]:
    """Transcribe via API. `gemini-*` models route to Gemini chat (no
    transcription endpoint); anything else uses the whisper endpoint.
    Failures mark the source `failed` with the cause."""
    s = get_settings()
    m = (model or s.WHISPER_API_MODEL or "whisper-1").strip()
    if m.lower().startswith("gemini-"):
        return transcribe_via_gemini_chat(blob, filename, m)
    return transcribe_whisper_api(blob, filename, model=m)
