"""OpenAI-compatible chat + embedding provider.

Works with api.openai.com AND GapGPT by only changing
``OPENAI_COMPAT_BASE_URL`` / ``OPENAI_COMPAT_API_KEY`` in ENV.
HTTP calls land in Phase 6+; Phase 0 exposes config + endpoint building.
"""

import httpx

from app.core.config import get_settings

CHAT_COMPLETIONS_PATH = "/chat/completions"
EMBEDDINGS_PATH = "/embeddings"


class OpenAICompatProvider:
    provider_name = "openai_compat"

    def __init__(self) -> None:
        s = get_settings()
        self.base_url = s.OPENAI_COMPAT_BASE_URL.rstrip("/")
        self.api_key = s.OPENAI_COMPAT_API_KEY
        self.chat_model = s.CHAT_MODEL
        self.embed_model = s.EMBEDDING_MODEL
        self.embed_dim = s.EMBEDDING_DIM

    @property
    def chat_url(self) -> str:
        return f"{self.base_url}{CHAT_COMPLETIONS_PATH}"

    @property
    def embeddings_url(self) -> str:
        return f"{self.base_url}{EMBEDDINGS_PATH}"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _post(self, url: str, payload: dict, timeout: float) -> dict:
        import time as _time

        if not self.api_key:
            raise RuntimeError("AI API key is not configured (OPENAI_COMPAT_API_KEY)")
        last: Exception | None = None
        for attempt in (1, 2, 3):
            try:
                r = httpx.post(url, headers=self._headers(), json=payload, timeout=timeout)
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as exc:
                last = exc
                # retry rate limits + transient server errors only
                if exc.response.status_code not in (408, 425, 429, 500, 502, 503, 504):
                    break
            except httpx.HTTPError as exc:
                last = exc
            _time.sleep(min(2 ** (attempt - 1), 4))
        raise RuntimeError(f"AI provider request failed: {last}") from last

    def generate(self, prompt: str, model: str | None = None, **kw) -> str:
        data = self._post(
            self.chat_url,
            {
                "model": model or self.chat_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": kw.get("temperature", 0.7),
                "max_tokens": kw.get("max_tokens", 1500),
            },
            timeout=kw.get("timeout", 120.0),
        )
        return str(data["choices"][0]["message"]["content"] or "")

    def describe_image(self, image_bytes: bytes, prompt: str,
                       model: str | None = None, **kw) -> str:
        """Vision call over the SAME integration (gapgpt/OpenAI-compatible).

        Shared by OCR (page -> text, Phase 2) and Vision (figure ->
        description, Phase 3): same endpoint, key, retry policy — only the
        prompt differs. No parallel vision architecture.
        """
        import base64 as _b64

        blob = image_bytes or b""
        if blob.startswith(b"\x89PNG"):
            mime = "image/png"
        elif blob.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif blob.startswith(b"RIFF"):
            mime = "image/webp"
        else:
            mime = "image/png"
        b64 = _b64.b64encode(blob).decode()
        data = self._post(
            self.chat_url,
            {
                "model": model or self.chat_model,
                "max_tokens": kw.get("max_tokens", 1000),
                "temperature": kw.get("temperature", 0.2),
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                }],
            },
            timeout=kw.get("timeout", 120.0),
        )
        try:
            return str(data["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected vision response: {data!r}"[:500]) from exc

    def transcribe_audio(self, audio_bytes: bytes, audio_format: str = "wav",
                           model: str | None = None, **kw) -> str:
        """Speech-to-text over chat completions (GapGPT-routed Gemini).

        Uses OpenAI `input_audio` content parts, so any compatible gateway
        model with audio understanding (e.g. gemini-2.5-flash-lite) works —
        no Google key, no transcription endpoint. Single plain-text reply,
        no timestamps (callers wrap it as one segment).
        """
        import base64 as _b64

        fmt = (audio_format or "wav").lower().strip()
        if fmt not in ("wav", "mp3"):
            fmt = "mp3"  # closest envelope; the model sniffs the codec
        data = self._post(
            self.chat_url,
            {
                "model": model or self.chat_model,
                "temperature": kw.get("temperature", 0.0),
                "max_tokens": kw.get("max_tokens", 2000),
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": kw.get(
                            "prompt",
                            "Transcribe this audio exactly. "
                            "Return only the transcription, no commentary.")},
                        {"type": "input_audio",
                         "input_audio": {
                             "data": _b64.b64encode(audio_bytes or b"").decode(),
                             "format": fmt}},
                    ],
                }],
            },
            timeout=kw.get("timeout", 300.0),
        )
        try:
            return str(data["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected audio response: {data!r}"[:500]) from exc

    def generate_structured(self, prompt: str, schema, model: str | None = None, **kw):
        """JSON-mode generation validated into a Pydantic schema."""
        import json as _json
        import re as _re

        schema_json = schema.model_json_schema()
        data = self._post(
            self.chat_url,
            {
                "model": model or self.chat_model,
                "messages": [
                    {"role": "system",
                     "content": "Respond with a single JSON object matching this schema: "
                                + _json.dumps(schema_json, ensure_ascii=False)},
                    {"role": "user", "content": prompt},
                ],
                "temperature": kw.get("temperature", 0.3),
                "max_tokens": kw.get("max_tokens", 3000),
                "response_format": {"type": "json_object"},
            },
            timeout=kw.get("timeout", 180.0),
        )
        content = str(data["choices"][0]["message"]["content"] or "")
        try:
            return schema.model_validate_json(content)
        except Exception:
            m = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, _re.S)
            candidate = m.group(1) if m else content[content.find("{") : content.rfind("}") + 1]
            return schema.model_validate_json(candidate)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), 32):
            batch = texts[i : i + 32]
            data = self._post(
                self.embeddings_url,
                {"model": self.embed_model, "input": batch},
                timeout=120.0,
            )
            ordered = sorted(data["data"], key=lambda d: d["index"])
            out.extend([list(d["embedding"]) for d in ordered])
        return out

    async def stream(self, prompt: str, model: str | None = None, **kw):
        """Async generator of content deltas (SSE). Used by future /chat/stream."""
        import json as _json

        import httpx as _httpx

        if not self.api_key:
            raise RuntimeError("AI API key is not configured (OPENAI_COMPAT_API_KEY)")
        payload = {
            "model": model or self.chat_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": kw.get("temperature", 0.7),
            "max_tokens": kw.get("max_tokens", 1500),
            "stream": True,
        }
        try:
            async with _httpx.AsyncClient(timeout=kw.get("timeout", 120.0)) as client:
                async with client.stream(
                    "POST", self.chat_url, headers=self._headers(), json=payload
                ) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            delta = _json.loads(data)["choices"][0]["delta"].get("content")
                        except Exception:
                            continue
                        if delta:
                            yield str(delta)
        except _httpx.HTTPError as exc:
            raise RuntimeError(f"AI stream failed: {exc}") from exc
