"""Google Gemini provider via REST (no extra SDK). Keys from ENV only."""

import httpx

from app.core.config import get_settings

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider:
    provider_name = "gemini"

    def __init__(self) -> None:
        s = get_settings()
        self.api_key = s.GEMINI_API_KEY
        self.model = s.GEMINI_MODEL

    def _post(self, path: str, payload: dict, timeout: float = 120.0) -> dict:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        url = f"{_BASE}/{path}?key={self.api_key}"
        try:
            r = httpx.post(url, json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Gemini request failed: {exc}") from exc

    def generate(self, prompt: str, model: str | None = None, **kw) -> str:
        data = self._post(
            f"{model or self.model}:generateContent",
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": kw.get("temperature", 0.7),
                    "maxOutputTokens": kw.get("max_tokens", 1500),
                },
            },
            timeout=kw.get("timeout", 120.0),
        )
        try:
            return str(
                data["candidates"][0]["content"]["parts"][0]["text"] or ""
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected Gemini response: {data!r}"[:500]) from exc

    def generate_grounded(self, prompt: str, model: str | None = None, **kw) -> dict:
        """Answer with Google Search Grounding (native Gemini REST tool).

        Returns {"text", "queries", "chunks", "supports"} where chunks are
        [{title, uri}] from groundingMetadata.groundingChunks and supports
        map answer segments to chunk indices. Raises RuntimeError when the
        key is missing or Google is unreachable (caller decides fallback).
        """
        data = self._post(
            f"{model or get_settings().WEB_SEARCH_MODEL}:generateContent",
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "tools": [{"google_search": {}}],
                "generationConfig": {
                    "temperature": kw.get("temperature", 0.3),
                    "maxOutputTokens": kw.get("max_tokens", 1500),
                },
            },
            timeout=kw.get("timeout", 120.0),
        )
        try:
            cand = data["candidates"][0]
            text = str(cand["content"]["parts"][0].get("text") or "")
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected Gemini response: {data!r}"[:500]) from exc
        meta = cand.get("groundingMetadata") or {}
        chunks = []
        for ch in meta.get("groundingChunks") or []:
            web = ch.get("web") or {}
            if web.get("uri"):
                chunks.append({"title": web.get("title") or web["uri"],
                               "uri": web["uri"]})
        return {
            "text": text,
            "queries": list(meta.get("webSearchQueries") or []),
            "chunks": chunks,
            "supports": list(meta.get("groundingSupports") or []),
        }

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), 32):
            batch = texts[i : i + 32]
            data = self._post(
                "text-embedding-004:batchEmbedContents",
                {"requests": [{"model": "models/text-embedding-004", "content": {"parts": [{"text": t}]}} for t in batch]},
            )
            out.extend([list(e["values"]) for e in data.get("embeddings", [])])
        if len(out) != len(texts):
            raise RuntimeError("Gemini embedding count mismatch")
        return out

    def generate_structured(self, prompt: str, schema, model: str | None = None, **kw):
        import json as _json

        data = self._post(
            f"{model or self.model}:generateContent",
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": kw.get("temperature", 0.3),
                    "maxOutputTokens": kw.get("max_tokens", 3000),
                    "responseMimeType": "application/json",
                    "responseSchema": schema.model_json_schema(),
                },
            },
            timeout=kw.get("timeout", 180.0),
        )
        content = str(data["candidates"][0]["content"]["parts"][0]["text"] or "")
        return schema.model_validate_json(content)

    async def stream(self, prompt: str, model: str | None = None, **kw):
        """Async generator of deltas via streamGenerateContent (SSE)."""
        import json as _json

        import httpx as _httpx

        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        url = f"{_BASE}/{model or self.model}:streamGenerateContent?alt=sse&key={self.api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": kw.get("temperature", 0.7),
                "maxOutputTokens": kw.get("max_tokens", 1500),
            },
        }
        try:
            async with _httpx.AsyncClient(timeout=kw.get("timeout", 120.0)) as client:
                async with client.stream("POST", url, json=payload) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        try:
                            parts = _json.loads(data)["candidates"][0]["content"]["parts"]
                            for part in parts:
                                if part.get("text"):
                                    yield str(part["text"])
                        except Exception:
                            continue
        except _httpx.HTTPError as exc:
            raise RuntimeError(f"Gemini stream failed: {exc}") from exc
