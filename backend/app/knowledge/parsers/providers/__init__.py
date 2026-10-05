"""URL provider layer: one generic Source, swappable per-site extractors.

`detect(url)` picks a provider from the domain; `extract(...)` returns an
`ExtractedURL` (text OR timestamped transcript). Each provider lives in
its own module so external-service changes touch one file only.
`provider_overrides` is the offline test seam (maps name -> fake fn).
"""

from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class ExtractedURL:
    provider: str  # youtube | instagram | aparat | generic
    url: str
    title: str = ""
    kind: str = "text"  # "text" | "transcript"
    text: str = ""
    segments: list = field(default_factory=list)  # [(start_ms,end_ms,text)]
    meta: dict = field(default_factory=dict)


def detect(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    if host.endswith(("youtube.com", "youtu.be", "music.youtube.com")):
        return "youtube"
    if host.endswith(("instagram.com", "instagr.am")):
        return "instagram"
    if host.endswith(("aparat.com",)):
        return "aparat"
    return "generic"


def extract(url: str, fetch_fn=None, transcribe_fn=None,
            provider_overrides: dict | None = None) -> ExtractedURL:
    """Run the detected provider. Never raises opaque errors: provider
    failures degrade to the generic text path, and total failure raises a
    RuntimeError the worker records on the row (never a crash)."""
    provider = detect(url)
    if provider_overrides and provider in provider_overrides:
        return provider_overrides[provider](url)
    if provider == "youtube":
        from app.knowledge.parsers.providers.youtube import extract_youtube

        return extract_youtube(url, transcribe_fn=transcribe_fn)
    if provider == "instagram":
        from app.knowledge.parsers.providers.instagram import extract_instagram

        return extract_instagram(url, fetch_fn=fetch_fn)
    if provider == "aparat":
        from app.knowledge.parsers.providers.aparat import extract_aparat

        return extract_aparat(url, fetch_fn=fetch_fn)
    from app.knowledge.parsers.providers.generic import extract_generic

    return extract_generic(url, fetch_fn=fetch_fn)
