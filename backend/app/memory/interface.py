"""MemoryProvider interface. MVP uses Postgres only.

Zep (or any future provider) must implement this protocol in its own
adapter module. Nothing else in the codebase may import a memory vendor SDK.
"""

from typing import Protocol
from uuid import UUID


class MemoryProvider(Protocol):
    def remember(
        self, workspace_id: UUID | str, user_id: UUID | str, key: str, value: str,
        category: str = "fact",
    ) -> None:
        ...

    def recall(
        self, workspace_id: UUID | str, user_id: UUID | str, query: str, top_k: int = 8,
    ) -> list[dict]:
        ...

    def summarize(
        self, workspace_id: UUID | str, user_id: UUID | str, scope: str, summary: str,
    ) -> None:
        ...
