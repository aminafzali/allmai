"""Hybrid retrieval contract (Phase 6+).

Pipeline: vector search (pgvector cosine) + metadata filtering
+ full-text search -> RRF fusion -> optional reranker (future).
Every retrieval call requires ``workspace_id``; cross-workspace
reads must be impossible at both app and DB (RLS) layers.
"""

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID


@dataclass
class RetrievedChunk:
    chunk_id: UUID
    content: str
    score: float
    source_id: UUID | None = None
    page_no: int | None = None
    start_ms: int | None = None


@dataclass
class RetrievalResult:
    query: str
    chunks: list[RetrievedChunk] = field(default_factory=list)


class RetrievalPort(Protocol):
    def search(
        self, workspace_id: UUID | str, query: str, kb_id: UUID | str | None, top_k: int
    ) -> RetrievalResult:
        ...
