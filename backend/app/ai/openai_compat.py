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
        text, _ = self.generate_with_usage(prompt, model=model, **kw)
        return text

    @staticmethod
    def _usage_of(data: dict) -> dict:
        """Token usage from a chat-completions payload (zeros when absent).

        Central helper so every model call site (chat, vision, extraction)
        reports the same shape for the internal usage ledger (P3): the
        gateway may omit `usage` on some paths, which must read as
        zero-not-unknown downstream, never as a crash.
        """
        usage = data.get("usage") if isinstance(data, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        try:
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
        except (TypeError, ValueError):
            prompt_tokens = 0
        try:
            completion_tokens = int(usage.get("completion_tokens") or 0)
        except (TypeError, ValueError):
            completion_tokens = 0
        return {"prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens}

    def _record_model(self, model: str | None, usage: dict | None,
                      latency_s: float, ok: bool = True, error: str = "",
                      input_chars: int = 0, meta: dict | None = None) -> None:
        """Emit one model usage event (P3 ledger). Never raises: usage
        tracking must not break product calls, and unit tests stay
        hermetic (the recorder drops events under pytest)."""
        try:
            from app.usage.recorder import record_event

            usage = usage or {}
            record_event(
                "model", provider=self.provider_name, model=model,
                prompt_tokens=(usage.get("prompt_tokens") or 0),
                completion_tokens=(usage.get("completion_tokens") or 0),
                calls=1, input_chars=input_chars,
                latency_ms=round(latency_s * 1000, 1),
                ok=ok, error=error, meta=meta)
        except Exception:
            pass

    def generate_with_usage(self, prompt: str, model: str | None = None,
                            **kw) -> tuple[str, dict]:
        """Same as generate, plus the token usage dict (for usage/cost
        tracking of extraction and other model calls)."""
        import time as _time

        t0, used_model = _time.perf_counter(), model or self.chat_model
        try:
            data = self._post(
                self.chat_url,
                {
                    "model": used_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": kw.get("temperature", 0.7),
                    "max_tokens": kw.get("max_tokens", 1500),
                },
                timeout=kw.get("timeout", 120.0),
            )
            try:
                text = str(data["choices"][0]["message"]["content"] or "")
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeError(f"unexpected chat response: {data!r}"[:500]) from exc
        except Exception as exc:
            self._record_model(used_model, None, _time.perf_counter() - t0,
                               ok=False,
                               error=f"{type(exc).__name__}: {exc}"[:300],
                               input_chars=len(prompt or ""))
            raise
        usage = self._usage_of(data)
        self._record_model(used_model, usage, _time.perf_counter() - t0,
                           input_chars=len(prompt or ""))
        return text, usage

    def describe_image(self, image_bytes: bytes, prompt: str,
                       model: str | None = None, **kw) -> str:
        """Vision call over the SAME integration (gapgpt/OpenAI-compatible).

        Shared by OCR (page -> text, Phase 2) and Vision (figure ->
        description, Phase 3): same endpoint, key, retry policy — only the
        prompt differs. No parallel vision architecture.
        """
        text, _ = self.describe_image_with_usage(
            image_bytes, prompt, model=model, **kw)
        return text

    def describe_image_with_usage(self, image_bytes: bytes, prompt: str,
                                  model: str | None = None,
                                  **kw) -> tuple[str, dict]:
        """Same as describe_image, plus the token usage dict (vision/OCR
        cost tracking)."""
        import base64 as _b64
        import time as _time

        t0, used_model = _time.perf_counter(), model or self.chat_model
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
        try:
            data = self._post(
                self.chat_url,
                {
                    "model": used_model,
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
                text = str(data["choices"][0]["message"]["content"] or "")
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeError(f"unexpected vision response: {data!r}"[:500]) from exc
        except Exception as exc:
            self._record_model(used_model, None, _time.perf_counter() - t0,
                               ok=False,
                               error=f"{type(exc).__name__}: {exc}"[:300],
                               input_chars=len(blob))
            raise
        usage = self._usage_of(data)
        self._record_model(used_model, usage, _time.perf_counter() - t0,
                           input_chars=len(blob))
        return text, usage

    def transcribe_audio(self, audio_bytes: bytes, audio_format: str = "wav",
                           model: str | None = None, **kw) -> str:
        """Speech-to-text over chat completions (GapGPT-routed Gemini).

        Uses OpenAI `input_audio` content parts, so any compatible gateway
        model with audio understanding (e.g. gemini-2.5-flash-lite) works —
        no Google key, no transcription endpoint. Single plain-text reply,
        no timestamps (callers wrap it as one segment).
        """
        import base64 as _b64
        import time as _time

        t0, used_model = _time.perf_counter(), model or self.chat_model
        fmt = (audio_format or "wav").lower().strip()
        if fmt not in ("wav", "mp3"):
            fmt = "mp3"  # closest envelope; the model sniffs the codec
        try:
            data = self._post(
                self.chat_url,
                {
                    "model": used_model,
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
                text = str(data["choices"][0]["message"]["content"] or "")
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeError(f"unexpected audio response: {data!r}"[:500]) from exc
        except Exception as exc:
            self._record_model(used_model, None, _time.perf_counter() - t0,
                               ok=False,
                               error=f"{type(exc).__name__}: {exc}"[:300],
                               input_chars=len(audio_bytes or b""))
            raise
        usage = self._usage_of(data)
        self._record_model(used_model, usage, _time.perf_counter() - t0,
                           input_chars=len(audio_bytes or b""))
        return text

    def generate_with_tools(self, messages: list, tools: list,
                              model: str | None = None, **kw) -> dict:
        """One chat round with OpenAI-style function tools (GapGPT-routed).

        Returns {"text": str, "calls": [{id, name, arguments}]}. Malformed
        tool calls are dropped, never raised — callers decide the loop.
        """
        import json as _json
        import time as _time

        t0, used_model = _time.perf_counter(), model or self.chat_model
        try:
            data = self._post(
                self.chat_url,
                {
                    "model": used_model,
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": kw.get("tool_choice", "auto"),
                    "temperature": kw.get("temperature", 0.3),
                    "max_tokens": kw.get("max_tokens", 1500),
                },
                timeout=kw.get("timeout", 180.0),
            )
            try:
                msg = data["choices"][0].get("message") or {}
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeError(f"unexpected tools response: {data!r}"[:500]) from exc
        except Exception as exc:
            self._record_model(used_model, None, _time.perf_counter() - t0,
                               ok=False,
                               error=f"{type(exc).__name__}: {exc}"[:300],
                               input_chars=len(str(messages or "")))
            raise
        usage = self._usage_of(data)
        self._record_model(used_model, usage, _time.perf_counter() - t0,
                           input_chars=len(str(messages or "")),
                           meta={"tool_calls": len(msg.get("tool_calls") or [])})
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = (tc or {}).get("function") or {}
            try:
                args = _json.loads(fn.get("arguments") or "{}")
            except Exception:
                continue
            if fn.get("name") and isinstance(args, dict):
                calls.append({"id": tc.get("id"), "name": fn["name"],
                              "arguments": args})
        content = msg.get("content")
        return {"text": content if isinstance(content, str) else "", "calls": calls}

    def generate_structured(self, prompt: str, schema, model: str | None = None, **kw):
        """JSON-mode generation validated into a Pydantic schema."""
        import json as _json
        import re as _re
        import time as _time

        t0, used_model = _time.perf_counter(), model or self.chat_model
        schema_json = schema.model_json_schema()
        try:
            data = self._post(
                self.chat_url,
                {
                    "model": used_model,
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
        except Exception as exc:
            self._record_model(used_model, None, _time.perf_counter() - t0,
                               ok=False,
                               error=f"{type(exc).__name__}: {exc}"[:300],
                               input_chars=len(prompt or ""))
            raise
        usage = self._usage_of(data)
        self._record_model(used_model, usage, _time.perf_counter() - t0,
                           input_chars=len(prompt or ""))
        try:
            return schema.model_validate_json(content)
        except Exception:
            m = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, _re.S)
            candidate = m.group(1) if m else content[content.find("{") : content.rfind("}") + 1]
            return schema.model_validate_json(candidate)

    def embed(self, texts: list[str]) -> list[list[float]]:
        import time as _time

        t0 = _time.perf_counter()
        if not texts:
            return []
        blanks = [i for i, t in enumerate(texts) if not (t or "").strip()]
        if blanks:
            # Fail fast with the exact cause: strict embedding gateways
            # 400 the ENTIRE batch on one empty input, which used to
            # surface as a cryptic provider error far from its source.
            raise RuntimeError(
                f"embed refusing batch with {len(blanks)} blank inputs "
                f"(indices {blanks[:8]}); chunking must never emit them")
        out: list[list[float]] = []
        prompt_toks = 0
        try:
            for i in range(0, len(texts), 32):
                batch = texts[i : i + 32]
                data = self._post(
                    self.embeddings_url,
                    {"model": self.embed_model, "input": batch},
                    timeout=120.0,
                )
                prompt_toks += int(((data.get("usage") or {}).get("prompt_tokens")
                                    if isinstance(data, dict) else 0) or 0)
                ordered = sorted(data["data"], key=lambda d: d["index"])
                out.extend([list(d["embedding"]) for d in ordered])
        except Exception as exc:
            self._record_model(self.embed_model, None, _time.perf_counter() - t0,
                               ok=False,
                               error=f"{type(exc).__name__}: {exc}"[:300],
                               input_chars=sum(len(t or "") for t in texts))
            raise
        self._record_model(self.embed_model,
                           {"prompt_tokens": prompt_toks,
                            "completion_tokens": 0,
                            "total_tokens": prompt_toks},
                           _time.perf_counter() - t0,
                           input_chars=sum(len(t or "") for t in texts))
        return out

    async def stream(self, prompt: str, model: str | None = None, **kw):
        """Async generator of content deltas (SSE). Used by future /chat/stream."""
        import json as _json
        import time as _time

        import httpx as _httpx

        if not self.api_key:
            raise RuntimeError("AI API key is not configured (OPENAI_COMPAT_API_KEY)")
        used_model = model or self.chat_model
        payload = {
            "model": used_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": kw.get("temperature", 0.7),
            "max_tokens": kw.get("max_tokens", 1500),
            "stream": True,
        }
        t0, out_chars = _time.perf_counter(), 0
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
                            out_chars += len(str(delta))
                            yield str(delta)
        except _httpx.HTTPError as exc:
            # Streams carry no token usage (only chars); cost stays unknown
            # until stream_options/include_usage is adopted — chars still
            # let future pricing estimate per user/workspace.
            self._record_model(used_model, None, _time.perf_counter() - t0,
                               ok=False, error=f"AI stream failed: {exc}"[:300],
                               input_chars=len(prompt or ""),
                               meta={"output_chars": out_chars})
            raise RuntimeError(f"AI stream failed: {exc}") from exc
        self._record_model(used_model, None, _time.perf_counter() - t0,
                           input_chars=len(prompt or ""),
                           meta={"output_chars": out_chars})
