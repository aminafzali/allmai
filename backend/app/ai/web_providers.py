"""Web-search providers behind one interface (swappable, never hard-wired).

Locked rules:
- Tools depend ONLY on ``WebProvider`` / ``ProviderChain`` here, never on
  a concrete backend (DDG today, Google/Gemini/GapGPT tomorrow).
- No search may originate from this server unless the admin explicitly
  selects a ``side="server"`` provider in config. Default chain is
  client-side only (the user's browser executes; see web/lib/browserTools.ts).
- Every provider fails open ([] / raise -> chain tries next); the chain
  records which provider actually served (``served_by``) for audit/citation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class WebResult:
    title: str = ""
    uri: str = ""
    snippet: str = ""


class WebProvider:
    """One swappable web-search backend."""

    name: str = "base"
    side: str = "server"  # "server" (runs here) | "client" (runs in browser)

    def search(self, query: str, max_results: int = 5,
               timeout: float = 30.0) -> list[WebResult]:
        raise NotImplementedError

    def client_spec(self) -> dict:
        """Descriptor the browser executor uses (client-side providers)."""
        raise NotImplementedError(f"{self.name} has no browser executor")


def validate_web_results(items: object, max_items: int = 10) -> list[dict]:
    """Clean UNTRUSTED browser-supplied results into safe dicts.

    Used by the /chat/resume endpoint. Caps count/items, strips length,
    keeps http(s) URLs only. Never raises on bad input.
    """
    out: list[dict] = []
    if not isinstance(items, list):
        return out
    for it in items[:max(1, max_items)]:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "")[:300].strip()
        uri = str(it.get("uri") or it.get("url") or "")[:1000].strip()
        snippet = str(it.get("snippet") or it.get("content") or "")[:2000].strip()
        if not (title or uri or snippet):
            continue
        if uri and not uri.lower().startswith(("http://", "https://")):
            uri = ""
        out.append({"title": title or uri, "uri": uri, "snippet": snippet})
    return out


class DDGServerProvider(WebProvider):
    """DuckDuckGo html endpoint, executed HERE (opt-in server side).

    Dormant by default: the default chain is client-side only, so this
    runs only if the admin explicitly selects ``web_search_provider=ddg``
    with ``web_search_side=server``. HTML scraping is brittle by nature;
    any failure returns [] (chain tries next).
    """

    name = "ddg"
    side = "server"

    def search(self, query: str, max_results: int = 5,
               timeout: float = 30.0) -> list[WebResult]:
        import re as _re
        from html import unescape as _un

        import httpx

        try:
            r = httpx.post(
                "https://html.duckduckgo.com/html/",
                data={"q": (query or "").strip()},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                timeout=timeout, follow_redirects=True,
            )
            r.raise_for_status()
            html = r.text
        except Exception as exc:
            log.warning("ddg search failed: %s", exc)
            return []
        out: list[WebResult] = []
        for m in _re.finditer(
                r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
                r'.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
                html, _re.S):
            if len(out) >= max(1, max_results):
                break
            href, title, snippet = m.group(1), m.group(2), m.group(3)
            if href.startswith("//"):
                href = "https:" + href
            strip = lambda s: _re.sub(r"<.*?>", "", _un(s)).strip()
            if href.lower().startswith(("http://", "https://")):
                out.append(WebResult(title=strip(title)[:300] or href,
                                     uri=href[:1000], snippet=strip(snippet)[:2000]))
        return out

    def client_spec(self) -> dict:
        return {
            "name": "ddg",
            "side": "client",
            "attempts": [
                {"endpoint": "direct",
                 "url": "https://html.duckduckgo.com/html/",
                 "method": "POST"},
                {"endpoint": "proxy",
                 "url": "https://api.allorigins.win/raw?url="
                        "https%3A%2F%2Fhtml.duckduckgo.com%2Fhtml%2F",
                 "method": "POST",
                 "note": "public CORS proxy fallback (third-party, rate-limited)"},
            ],
            "parse": "result__a/result__snippet anchors",
        }


class GeminiGroundingProvider(WebProvider):
    """Gemini + Google Search Grounding, executed HERE. Dormant unless
    GEMINI_API_KEY is set (direct Google key, not GapGPT)."""

    name = "gemini-grounding"
    side = "server"

    def search(self, query: str, max_results: int = 5,
               timeout: float = 120.0) -> list[WebResult]:
        from app.ai.gemini import GeminiProvider

        try:
            res = GeminiProvider().generate_grounded(query or "")
        except Exception as exc:
            log.warning("gemini grounding unavailable: %s", exc)
            return []
        out: list[WebResult] = []
        if (res.get("text") or "").strip():
            out.append(WebResult(title="Google Search (grounded answer)", uri="",
                                 snippet=res["text"].strip()[:2000]))
        for ch in list(res.get("chunks") or [])[:max(1, max_results)]:
            out.append(WebResult(title=(ch.get("title") or ch.get("uri") or "")[:300],
                                 uri=ch.get("uri") or "",
                                 snippet=(ch.get("title") or "")[:500]))
        return out


# GapGPT slot: if a future probe shows the gateway exposes web search
# (/v1/responses + web_search tool or a search model), add a
# GapGPTWebProvider here — tools/runtime stay untouched.


class GapGPTWebProvider(WebProvider):
    """Web search through the ALREADY-PAID GapGPT gateway (Responses API
    + web_search tool). Proven live: 200 + url_citation annotations.
    Same trust domain as every chat call (existing key, existing egress).
    DEFAULT provider — browser DDG is unreachable from Iranian networks
    (both direct and public proxies fail), so client-side web search is
    currently a dead path."""

    name = "gapgpt"
    side = "server"

    def search(self, query: str, max_results: int = 5,
               timeout: float = 120.0) -> list[WebResult]:
        from app.ai.gapgpt_search import gapgpt_grounded_search

        try:
            res = gapgpt_grounded_search(query or "")
        except Exception as exc:
            log.warning("gapgpt search unavailable: %s", exc)
            return []
        out: list[WebResult] = []
        if (res.get("text") or "").strip():
            out.append(WebResult(title="پاسخ زمینه‌دار GapGPT", uri="",
                                 snippet=res["text"].strip()[:2000]))
        for ch in list(res.get("chunks") or [])[:max(1, max_results)]:
            out.append(WebResult(title=(ch.get("title") or ch.get("uri") or "")[:300],
                                 uri=ch.get("uri") or "",
                                 snippet=(ch.get("snippet") or "")[:2000]))
        return out


PROVIDERS: dict[str, WebProvider] = {
    "gapgpt": GapGPTWebProvider(),
    "ddg": DDGServerProvider(),
    "gemini-grounding": GeminiGroundingProvider(),
}


@dataclass
class ProviderChain:
    """Ordered [primary, *fallbacks]: first non-empty result wins.

    ``served_by`` records the provider name ("" when all failed) so the
    API can cite/audit it. Per-provider timeout; never raises.
    """

    providers: list[WebProvider] = field(default_factory=list)

    def run(self, query: str, max_results: int = 5,
            timeout_each: float = 30.0) -> tuple[list[WebResult], str]:
        for p in self.providers:
            if getattr(p, "side", "server") != "server":
                continue  # client-side links run in the browser, not here
            t0 = time.perf_counter()
            try:
                out = p.search(query, max_results=max_results,
                               timeout=timeout_each) or []
            except Exception as exc:
                log.warning("web provider %s failed: %s", p.name, exc)
                out = []
            if out:
                log.info("web_search served by %s (%.1fs)", p.name,
                         time.perf_counter() - t0)
                return out, p.name
        return [], ""


def get_web_chain() -> ProviderChain:
    """Build the SERVER-side chain from config.

    WEB_SEARCH_PROVIDER + comma WEB_SEARCH_FALLBACKS name registry keys.
    Unknown names are ignored (fail-open). Default: ddg server impl is
    registered but the default SIDE is client (see config), so a default
    install never searches from this server.
    """
    from app.core.config import get_settings

    s = get_settings()
    names = [s.WEB_SEARCH_PROVIDER] + [
        n.strip() for n in (s.WEB_SEARCH_FALLBACKS or "").split(",") if n.strip()]
    providers = [PROVIDERS[n] for n in dict.fromkeys(names) if n in PROVIDERS]
    if getattr(s, "WEB_SEARCH_SIDE", "client") != "server":
        # Default install: searches run in the user's browser, never here.
        return ProviderChain([])
    return ProviderChain([p for p in providers if p.side == "server"])
