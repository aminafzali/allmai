"""Video → audio-track transcription (no frame analysis).

Pipeline: probe duration (guard) → extract mono 16kHz mp3 via the
imageio-ffmpeg static binary (no OS install) → split pieces larger than
WHISPER_MAX_BYTES (timestamps shifted) → transcribe each piece →
[(start_ms, end_ms, text)].

`transcribe_fn` is the test seam (production default: audio.transcribe,
i.e. the whisper/gemini API routes). Guards (VIDEO_MAX_BYTES at upload,
VIDEO_MAX_DURATION_S here) are tunable via Settings; oversized videos
are rejected loudly instead of stalling the worker.
"""

import os
import re
import subprocess
import tempfile

_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):([\d.]+)")


class VideoTooLong(RuntimeError):
    pass


def _ffmpeg() -> str:
    from imageio_ffmpeg import get_ffmpeg_exe

    return get_ffmpeg_exe()


def probe_duration_s(blob: bytes) -> float:
    """Return the container duration in seconds (0.0 when unparseable)."""
    with tempfile.TemporaryDirectory(prefix="allmai-vprobe-") as tmp:
        src = os.path.join(tmp, "in.bin")
        with open(src, "wb") as f:
            f.write(blob)
        try:
            p = subprocess.run(
                [_ffmpeg(), "-hide_banner", "-i", src],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return 0.0
    err = (p.stderr or "") + (p.stdout or "")
    m = _DUR_RE.search(err)
    if not m:
        return 0.0
    h, mi, sec = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + sec


def _extract_mp3(blob: bytes, tmp: str, start: float = 0.0,
                 length: float | None = None) -> str:
    """Extract (a window of) the audio track to mono 16kHz mp3. Returns path."""
    from app.core.config import get_settings

    src = os.path.join(tmp, "in.bin")
    if not os.path.exists(src):
        with open(src, "wb") as f:
            f.write(blob)
    out = os.path.join(tmp, f"au_{start:.1f}_{length or -1}.mp3")
    cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"]
    if start > 0:
        cmd += ["-ss", f"{start:.2f}"]
    cmd += ["-i", src, "-vn", "-ac", "1", "-ar", "16000",
            "-b:a", get_settings().VIDEO_AUDIO_BITRATE]
    if length is not None:
        cmd += ["-t", f"{length:.2f}"]
    cmd.append(out)
    try:
        subprocess.run(cmd, capture_output=True, timeout=600, check=True)
    except (OSError, subprocess.SubprocessError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"audio extraction failed: {type(exc).__name__}") from exc
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        raise RuntimeError("audio extraction produced no output")
    return out


def transcribe_video(blob: bytes, filename: str,
                     transcribe_fn=None) -> list[tuple[int, int, str]]:
    """Full video→segments transcription with duration/size guards."""
    from app.core.config import get_settings

    s = get_settings()
    if len(blob) > int(s.VIDEO_MAX_BYTES):
        raise VideoTooLong(
            f"video too large ({len(blob) // (1024 * 1024)} MB > "
            f"{int(s.VIDEO_MAX_BYTES) // (1024 * 1024)} MB)")
    duration = probe_duration_s(blob)
    if duration > 0 and duration > float(s.VIDEO_MAX_DURATION_S):
        raise VideoTooLong(
            f"video too long ({duration:.0f}s > "
            f"{float(s.VIDEO_MAX_DURATION_S):.0f}s)")
    if transcribe_fn is None:
        from app.knowledge.parsers.audio import transcribe as transcribe_fn

    with tempfile.TemporaryDirectory(prefix="allmai-video-") as tmp:
        full = _extract_mp3(blob, tmp)
        size = os.path.getsize(full)
        cap = int(s.WHISPER_MAX_BYTES)
        total = duration if duration > 0 else 0.0
        # Split oversized audio by time so every piece fits the per-request
        # cap; piece timestamps are shifted back to video time afterwards.
        pieces: list[tuple[float, float | None]] = [(0.0, None)]
        if size > cap and total > 0:
            import math

            n = max(2, math.ceil(size / cap))
            step = total / n
            pieces = [(round(i * step, 2), round(step, 2)) for i in range(n)]
        out: list[tuple[int, int, str]] = []
        for start, length in pieces:
            path = full if (start == 0.0 and length is None) else \
                _extract_mp3(blob, tmp, start, length)
            with open(path, "rb") as f:
                chunk = f.read()
            for s_ms, e_ms, text in transcribe_fn(chunk, "audio.mp3"):
                text = (text or "").strip()
                if text:
                    out.append((int(s_ms + start * 1000),
                                int(e_ms + start * 1000), text))
    out.sort(key=lambda t: (t[0], t[1]))
    return out
