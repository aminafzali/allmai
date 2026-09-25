"""AIProvider abstraction: generate / generate_structured / stream / embed.

Keys and model names come from ENV only and stay server-side.
``openai_compat`` covers both OpenAI and GapGPT (https://gapgpt.app),
which exposes an OpenAI-compatible API with a different base URL.
"""

from collections.abc import AsyncIterator
from typing import Any, Protocol

from pydantic import BaseModel


class AIProvider(Protocol):
    def generate(self, prompt: str, model: str | None = None, **kw: Any) -> str:
        ...

    def generate_structured(
        self, prompt: str, schema: type[BaseModel], model: str | None = None, **kw: Any
    ) -> BaseModel:
        ...

    def stream(
        self, prompt: str, model: str | None = None, **kw: Any
    ) -> AsyncIterator[str]:
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...
