"""Audio transcription (MVP decision: no local models).

Three routes, selected by model id (panel-editable `audio.transcription`
ai_setting, ENV WHISPER_API_MODEL fallback — the DB row wins):
- `whisper-*` (e.g. `whisper-1`): GapGPT `/audio/transcriptions` endpoint;
  `verbose_json` preserves segment timestamps into PageSegments.
- `gpt-4o-transcribe*` (e.g. `gpt-4o-mini-transcribe`, DEFAULT): AvalAI
  `/audio/transcriptions` (OpenAI-compatible, Bearer key, docs:
  https://docs.avalai.ir/fa/api-reference/audio). These models only speak
  `json`/`text` (no verbose_json timestamps) → a single (0, 0, text)
  segment; wall-clock time still lands on meeting bubbles client-side.
- `gemini-*`: chat completions with audio input (no transcription
  endpoint needed); single segment, no timestamps. No Google key —
  the SAME OpenAI-compatible endpoint + key as everything else.
Provider-agnostic: any endpoint implementing these shapes works by
changing ENV. Nothing here is ever deleted for a route change — the
model id alone selects the route, switchable from /admin/ai-settings.
"""

import io
import time as _time

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


def _avalai_upload_name(filename: str) -> str:
    """ASCII-safe filename that KEEPS the real container (AvalAI sniffs
    format from it; webm/mp4/ogg must not be relabeled .mp3)."""
    name = (filename or "").lower()
    for ext in (".mp3", ".wav", ".m4a", ".webm", ".mp4", ".ogg", ".flac"):
        if name.endswith(ext):
            return f"audio{ext}"
    return "audio.mp3"


def resolve_transcription_model(db=None, explicit: str | None = None) -> str:
    """Effective transcription model id: explicit arg -> panel DB row ->
    ENV WHISPER_API_MODEL -> hardcoded fallback. The panel row is read
    directly (not via get_setting's code-default merge) so ENV stays a
    real fallback when no row exists."""
    if (explicit or "").strip():
        return explicit.strip()
    if db is not None:
        try:
            from app.admin.models import AISetting

            row = db.query(AISetting).filter(
                AISetting.key == "audio.transcription").first()
            m = str(((row.value if row is not None else None) or {}).get("model") or "").strip()
            if m:
                return m
        except Exception:
            pass
    s = get_settings()
    return (s.WHISPER_API_MODEL or "").strip() or "whisper-1"


def is_avalai_transcribe_model(model: str) -> bool:
    m = (model or "").strip().lower()
    return m.startswith("gpt-4o-transcribe") or m.startswith("gpt-4o-mini-transcribe")


def _retry_after_s(resp: httpx.Response, cap: float) -> float:
    """Server's Retry-After hint in seconds (0 when absent/unparseable)."""
    try:
        return max(0.0, min(float(resp.headers.get("retry-after", "") or 0), cap))
    except (TypeError, ValueError):
        return 0.0


def _post_multipart(url: str, api_key: str, files: dict, data: dict,
                    timeout: float, what: str) -> dict | httpx.Response:
    """POST multipart with retries on 429/408/5xx (honors Retry-After).

    Returns the decoded-JSON body when the endpoint speaks JSON, else the
    raw response (e.g. `text` format bodies). Raises
    TranscriptionUnavailable after TRANSCRIBE_MAX_RETRIES retries."""
    s = get_settings()
    max_retries = int(s.TRANSCRIBE_MAX_RETRIES)
    cap = float(s.TRANSCRIBE_RETRY_CAP_S)
    last: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            r = httpx.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                files=files,
                data=data,
                timeout=timeout,
            )
            if r.status_code in (408, 425, 429, 500, 502, 503, 504) and attempt < max_retries:
                wait = _retry_after_s(r, cap) or min(2 ** attempt, cap)
                _time.sleep(wait)
                continue
            r.raise_for_status()
            try:
                return r.json()
            except Exception:
                return r
        except httpx.HTTPStatusError as exc:
            last = exc
            code = exc.response.status_code if exc.response is not None else 0
            if code in (408, 425, 429, 500, 502, 503, 504) and attempt < max_retries:
                wait = 0.0
                try:
                    wait = _retry_after_s(exc.response, cap)
                except Exception:
                    wait = 0.0
                _time.sleep(wait or min(2 ** attempt, cap))
                continue
            body = ""
            try:
                body = (exc.response.text or "")[:500]
            except Exception:
                body = ""
            raise TranscriptionUnavailable(
                f"{what} failed: {exc} | response: {body}") from exc
        except httpx.HTTPError as exc:
            last = exc
            if attempt < max_retries:
                _time.sleep(min(2 ** attempt, cap))
                continue
            raise TranscriptionUnavailable(f"{what} failed: {exc}") from exc
    raise TranscriptionUnavailable(f"{what} failed after retries: {last}")


def _record_audio(provider: str, model: str | None, blob_len: int,
                  latency_s: float, ok: bool, error: Exception | None) -> None:
    """Emit one transcription usage event (P3 ledger). Never raises."""
    try:
        from app.usage.recorder import record_event

        record_event(
            "model", provider=provider, model=model, calls=1,
            input_chars=max(0, int(blob_len or 0)),
            latency_ms=round(latency_s * 1000, 1), ok=ok,
            error=(f"{type(error).__name__}: {error}"[:300] if error else ""),
            meta={"route": "transcription"})
    except Exception:
        pass


def _transcribe_whisper_api(blob: bytes, filename: str,
                            model: str | None = None,
                            language: str | None = None) -> list[tuple[int, int, str]]:
    """Return [(start_ms, end_ms, text)] via the GapGPT whisper endpoint.
    Raises on API failure (after retries)."""
    s = get_settings()
    if not s.OPENAI_COMPAT_API_KEY:
        raise TranscriptionUnavailable("no AI API key configured")
    url = s.OPENAI_COMPAT_BASE_URL.rstrip("/") + "/audio/transcriptions"
    files = {"file": (_upload_name(filename), io.BytesIO(blob), _mime_for(filename))}
    data = {"model": model or s.WHISPER_API_MODEL, "response_format": "verbose_json"}
    if (language or "").strip():
        data["language"] = language.strip()[:16]
    out = _post_multipart(url, s.OPENAI_COMPAT_API_KEY, files, data,
                          timeout=300, what="whisper API")
    body = out if isinstance(out, dict) else {}
    segments: list[tuple[int, int, str]] = []
    for seg in body.get("segments", []):
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        segments.append((int(float(seg.get("start", 0)) * 1000), int(float(seg.get("end", 0)) * 1000), text))
    if not segments:  # plain (non-verbose) response shape
        text = str(body.get("text", "")).strip()
        if text:
            segments.append((0, 0, text))
    return segments


def _transcribe_avalai_api(blob: bytes, filename: str,
                           model: str | None = None,
                           language: str | None = None) -> list[tuple[int, int, str]]:
    """Return [(start_ms, end_ms, text)] via AvalAI (default route).

    Model family `gpt-4o-transcribe*` only speaks `json`/`text` (per
    https://docs.avalai.ir/fa/api-reference/audio) — no segment
    timestamps — so the transcript comes back as ONE (0, 0, text)
    segment. Raises on API failure (after retries)."""
    s = get_settings()
    if not s.AVALAI_API_KEY:
        raise TranscriptionUnavailable("no AvalAI API key configured (AVALAI_API_KEY)")
    model = (model or "gpt-4o-mini-transcribe").strip()
    url = s.AVALAI_BASE_URL.rstrip("/") + "/audio/transcriptions"
    upname = _avalai_upload_name(filename)
    ctype = _mime_for(upname)
    if upname.endswith(".webm"):
        ctype = "audio/webm"
    elif upname.endswith((".mp4", ".m4a")):
        ctype = "audio/mp4"
    elif upname.endswith(".ogg"):
        ctype = "audio/ogg"
    elif upname.endswith(".flac"):
        ctype = "audio/flac"
    files = {"file": (upname, io.BytesIO(blob), ctype)}
    data = {"model": model, "response_format": "json"}
    if (language or "").strip():
        data["language"] = language.strip()[:16]
    out = _post_multipart(url, s.AVALAI_API_KEY, files, data,
                          timeout=300, what="avalai transcription API")
    if isinstance(out, dict):
        segments = []
        for seg in out.get("segments", []) or []:
            text = str((seg or {}).get("text", "")).strip()
            if not text:
                continue
            try:
                start = int(float((seg or {}).get("start", 0)) * 1000)
                end = int(float((seg or {}).get("end", 0)) * 1000)
            except (TypeError, ValueError):
                start, end = 0, 0
            segments.append((start, end, text))
        if segments:
            return segments
        text = str(out.get("text", "") or "").strip()
        return [(0, 0, text)] if text else []
    # `text` format body that isn't JSON: raw transcript
    try:
        raw = str(out.text or "").strip()  # type: ignore[union-attr]
    except Exception:
        raw = ""
    return [(0, 0, raw)] if raw else []


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


def transcribe_whisper_api(blob: bytes, filename: str,
                           model: str | None = None,
                           language: str | None = None) -> list[tuple[int, int, str]]:
    """GapGPT whisper route with usage recording (same contract)."""
    import time as _t

    t0 = _t.perf_counter()
    used = model or get_settings().WHISPER_API_MODEL
    try:
        out = _transcribe_whisper_api(blob, filename, model=model,
                                      language=language)
    except Exception as exc:
        _record_audio("openai_compat", used, len(blob or b""),
                      _t.perf_counter() - t0, ok=False, error=exc)
        raise
    _record_audio("openai_compat", used, len(blob or b""),
                  _t.perf_counter() - t0, ok=True, error=None)
    return out


def transcribe_avalai_api(blob: bytes, filename: str,
                          model: str | None = None,
                          language: str | None = None) -> list[tuple[int, int, str]]:
    """AvalAI transcription route with usage recording (same contract)."""
    import time as _t

    t0 = _t.perf_counter()
    used = (model or "gpt-4o-mini-transcribe").strip()
    try:
        out = _transcribe_avalai_api(blob, filename, model=model,
                                     language=language)
    except Exception as exc:
        _record_audio("avalai", used, len(blob or b""),
                      _t.perf_counter() - t0, ok=False, error=exc)
        raise
    _record_audio("avalai", used, len(blob or b""),
                  _t.perf_counter() - t0, ok=True, error=None)
    return out


def chapterize(segments: list[tuple[int, int, str]],
                 gap_ms: int = 90_000,
                 min_chapter_ms: int = 120_000,
                 max_title_words: int = 8) -> list[dict]:
    """Split a timestamped transcript into chapters (سرفصل), no diarization.

    Pure + deterministic: a new chapter starts where the silence gap
    between two segments reaches `gap_ms`; chapters shorter than
    `min_chapter_ms` merge into the previous one (the first chapter is
    never dropped). The title is the chapter's first words — content
    navigation only. NO speaker labels are produced (diarization is
    explicitly out of scope). Segments without timestamps ((0, 0))
    yield a single chapter. Returns [{start_ms, end_ms, title}].
    """
    segs = [(int(s or 0), int(e or 0), str(t or "").strip())
            for s, e, t in (segments or [])]
    segs = [s for s in segs if s[2]]
    if not segs:
        return []
    timed = [s for s in segs if s[1] > s[0]]
    if not timed:
        words = segs[0][2].split()[:max(1, max_title_words)]
        return [{"start_ms": 0, "end_ms": 0, "title": " ".join(words)}]
    # Raw split at every silence gap >= gap_ms.
    raw: list[dict] = []
    cur = {"start_ms": timed[0][0], "end_ms": timed[0][1],
           "words": list(timed[0][2].split())}
    for (s, e, t) in timed[1:]:
        if s - cur["end_ms"] >= max(1000, gap_ms):
            raw.append(cur)
            cur = {"start_ms": s, "end_ms": e, "words": list(t.split())}
        else:
            cur["end_ms"] = max(cur["end_ms"], e)
            cur["words"].extend(t.split())
    raw.append(cur)
    # Absorb short MIDDLE chapters (interstitial blips) into the previous
    # one. First and last chapters always survive: an opening and a
    # closing section are real even when short.
    merged: list[dict] = []
    for i, ch in enumerate(raw):
        if 0 < i < len(raw) - 1 and \
                (ch["end_ms"] - ch["start_ms"]) < max(0, min_chapter_ms):
            prev = merged[-1]
            prev["end_ms"] = max(prev["end_ms"], ch["end_ms"])
            prev["words"].extend(ch["words"])
        else:
            merged.append({"start_ms": ch["start_ms"], "end_ms": ch["end_ms"],
                           "words": list(ch["words"])})
    return [_close_chapter(c, max_title_words) for c in merged]


def _close_chapter(cur: dict, max_title_words: int) -> dict:
    words = [w for w in (cur.get("words") or []) if w][:max(1, max_title_words)]
    return {"start_ms": int(cur["start_ms"]), "end_ms": int(cur["end_ms"]),
            "title": " ".join(words) or "بخش"}


def transcribe(blob: bytes, filename: str,
               model: str | None = None, db=None,
               language: str | None = None) -> list[tuple[int, int, str]]:
    """Transcribe via API. Route selected by model id (panel setting wins):
    `gemini-*` -> Gemini chat; `gpt-4o-transcribe*` -> AvalAI (default);
    anything else -> GapGPT whisper endpoint. `db` (optional session) lets
    the panel `audio.transcription` row override ENV. `language` (ISO-639-1,
    e.g. fa/en) is forwarded to whisper-compatible endpoints when set;
    unset means auto-detect. Failures mark the source `failed` with cause."""
    m = resolve_transcription_model(db, model)
    if m.lower().startswith("gemini-"):
        return transcribe_via_gemini_chat(blob, filename, m)
    if is_avalai_transcribe_model(m):
        return transcribe_avalai_api(blob, filename, model=m, language=language)
    return transcribe_whisper_api(blob, filename, model=m, language=language)
