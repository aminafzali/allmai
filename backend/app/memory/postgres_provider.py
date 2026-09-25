"""Postgres-backed MemoryProvider (real implementation).

MVP scoring is token-overlap over key+value (portable, no PG dependency);
a vector/FTS upgrade can replace `_score` later without changing callers.
Every method requires workspace context and is scoped to (workspace, user).
"""

from sqlalchemy.orm import Session

from app.common.base import coerce_uuid
from app.core.workspace import require_workspace_id
from app.memory.models import ConversationSummary, MemoryFact

_CATEGORY_BOOST = {"goal": 2.0, "preference": 1.5, "plan": 1.5}


def _tokens(text: str) -> set[str]:
    return {t for t in text.lower().split() if len(t) > 1}


class PostgresMemoryProvider:
    """Stores user memory in our own PostgreSQL (memory_facts, summaries)."""

    provider_name = "postgres"

    def __init__(self, db: Session):
        self.db = db

    def remember(self, workspace_id, user_id, key, value, category="fact") -> MemoryFact:
        require_workspace_id(workspace_id)
        key = (key or "").strip()[:200]
        if not key:
            raise ValueError("key is required")
        row = (
            self.db.query(MemoryFact)
            .filter(
                MemoryFact.workspace_id == coerce_uuid(workspace_id),
                MemoryFact.user_id == coerce_uuid(user_id),
                MemoryFact.key == key,
            )
            .first()
        )
        if row is None:
            row = MemoryFact(workspace_id=coerce_uuid(workspace_id),
                             user_id=coerce_uuid(user_id), key=key)
            self.db.add(row)
        row.value = (value or "")[:5000]
        row.category = (category or "fact")[:100]
        self.db.commit()
        self.db.refresh(row)
        return row

    def facts_for_user(self, workspace_id, user_id) -> list[MemoryFact]:
        require_workspace_id(workspace_id)
        return (
            self.db.query(MemoryFact)
            .filter(
                MemoryFact.workspace_id == coerce_uuid(workspace_id),
                MemoryFact.user_id == coerce_uuid(user_id),
            )
            .order_by(MemoryFact.updated_at.desc())
            .all()
        )

    def _score(self, query_tokens: set[str], fact: MemoryFact) -> float:
        hay = _tokens(f"{fact.key} {fact.value}")
        overlap = len(query_tokens & hay)
        if not overlap:
            return 0.0
        return overlap * _CATEGORY_BOOST.get(fact.category, 1.0)

    def recall(self, workspace_id, user_id, query, top_k=8) -> list[dict]:
        require_workspace_id(workspace_id)
        qt = _tokens(query or "")
        if not qt:
            return []
        scored = []
        for fact in self.facts_for_user(workspace_id, user_id):
            score = self._score(qt, fact)
            if score > 0:
                scored.append((score, fact))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [
            {"key": f.key, "value": f.value, "category": f.category, "score": s}
            for s, f in scored[: max(1, min(top_k, 50))]
        ]

    def summarize(self, workspace_id, user_id, scope, summary) -> ConversationSummary:
        require_workspace_id(workspace_id)
        scope = (scope or "general")[:200]
        row = (
            self.db.query(ConversationSummary)
            .filter(
                ConversationSummary.workspace_id == coerce_uuid(workspace_id),
                ConversationSummary.user_id == coerce_uuid(user_id),
                ConversationSummary.scope == scope,
            )
            .first()
        )
        if row is None:
            row = ConversationSummary(workspace_id=coerce_uuid(workspace_id),
                                      user_id=coerce_uuid(user_id), scope=scope)
            self.db.add(row)
        row.summary = (summary or "")[:10000]
        self.db.commit()
        self.db.refresh(row)
        return row

    def get_summary(self, workspace_id, user_id, scope="general") -> str:
        require_workspace_id(workspace_id)
        row = (
            self.db.query(ConversationSummary)
            .filter(
                ConversationSummary.workspace_id == coerce_uuid(workspace_id),
                ConversationSummary.user_id == coerce_uuid(user_id),
                ConversationSummary.scope == scope,
            )
            .first()
        )
        return row.summary if row else ""


def get_memory_provider(db) -> PostgresMemoryProvider:
    return PostgresMemoryProvider(db)
