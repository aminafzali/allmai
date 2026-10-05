"""Upload validation: extension allow-list, magic-byte sniffing, size cap.

No libmagic dependency — signatures are checked manually so validation
works identically in every environment.
"""

from fastapi import HTTPException

from app.knowledge.models import AUDIO_EXTENSIONS, SOURCE_TYPES

MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# extension -> (allowed source types, magic-byte prefixes)
# NOTE: ".mp4"/".mov" share the ftyp box layout; ".webm" is EBML (b"\x1a\x45\xdf\xa3").
# ".xlsx" is a zip like docx/pptx (PK\x03\x04) — the type/extension pair
# disambiguates, same as the existing docx/pptx handling.
_SIGNATURES: dict[str, tuple[tuple[str, ...], list[bytes]]] = {
    ".pdf": (("pdf",), [b"%PDF"]),
    ".docx": (("docx",), [b"PK\x03\x04"]),
    ".pptx": (("pptx",), [b"PK\x03\x04"]),
    ".xlsx": (("excel",), [b"PK\x03\x04"]),
    ".txt": (("txt",), []),
    ".csv": (("csv",), []),  # plain text: sniffed at parse time, like .txt
    ".md": (("txt",), []),
    ".markdown": (("txt",), []),
    ".png": (("image",), [b"\x89PNG\r\n\x1a\n"]),
    ".jpg": (("image",), [b"\xff\xd8\xff"]),
    ".jpeg": (("image",), [b"\xff\xd8\xff"]),
    ".webp": (("image",), [b"RIFF"]),
    ".gif": (("image",), [b"GIF87a", b"GIF89a"]),
    ".mp3": (("audio",), [b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"]),
    ".wav": (("audio",), [b"RIFF"]),
    ".m4a": (("audio",), [b"\x00\x00\x00", b"ftyp"]),
    ".mp4": (("video",), [b"\x00\x00\x00"]),  # ftyp box checked at offset 4
    ".mov": (("video",), [b"\x00\x00\x00"]),  # (same special-case as .m4a below)
    ".webm": (("video",), [b"\x1a\x45\xdf\xa3"]),
}

_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _ext(filename: str) -> str:
    name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    dot = name.rfind(".")
    return name[dot:].lower() if dot != -1 else ""


def safe_filename(filename: str) -> str:
    name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()
    cleaned = "".join(c for c in name if c.isalnum() or c in ("-", "_", ".", " "))
    return (cleaned.strip() or "file")[:180]


def validate_upload(source_type: str, filename: str, blob: bytes) -> str:
    """Return the MIME type or raise 422/413. Never trusts client MIME."""
    if source_type not in SOURCE_TYPES:
        raise HTTPException(422, f"unsupported source type: {source_type!r}")
    # Video has its own tunable cap (VIDEO_MAX_BYTES); everything else
    # shares the global cap. ftyp-box videos (mp4/mov) check "ftyp" at
    # offset 4 like the existing m4a handling.
    if source_type == "video":
        from app.core.config import get_settings

        cap = int(get_settings().VIDEO_MAX_BYTES)
        if len(blob) > cap:
            raise HTTPException(413, f"video too large (max {cap // (1024 * 1024)} MB)")
    elif len(blob) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large (max 50 MB)")
    if not blob:
        raise HTTPException(422, "empty file")
    ext = _ext(filename)
    if ext not in _SIGNATURES:
        raise HTTPException(422, f"file extension not allowed: {ext!r}")
    allowed_types, magics = _SIGNATURES[ext]
    if source_type not in allowed_types:
        raise HTTPException(
            422, f"extension {ext!r} does not match source type {source_type!r}"
        )
    if source_type == "audio" and ext not in AUDIO_EXTENSIONS:
        raise HTTPException(422, f"audio MVP supports {', '.join(AUDIO_EXTENSIONS)}")
    for magic in magics:
        if magic == b"\x00\x00\x00":  # m4a box-size prefix: check ftyp at offset 4
            if len(blob) > 8 and blob[4:8] == b"ftyp":
                break
        elif blob.startswith(magic):
            break
    else:
        if magics:
            raise HTTPException(422, "file content does not match its extension")
    return _MIME[ext]


def storage_key(workspace_id, kb_id, source_id, filename: str) -> str:
    return f"workspaces/{workspace_id}/kb/{kb_id}/sources/{source_id}/{safe_filename(filename)}"
