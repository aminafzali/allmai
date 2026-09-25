"""Agent tools: controlled, workspace-scoped access to Knowledge/Memory.

Tools are plain functions taking an explicit workspace_id — the AgentService
binds them per turn. Nothing here is reachable without a tenant context.
"""

from dataclasses import dataclass


@dataclass
class ToolContext:
    db: object
    workspace_id: object
    user_id: object
    embed_fn: object = None
    kb_ids: object = None
    global_kb_ids: object = None


def knowledge_search(ctx: ToolContext, query: str, kb_id=None, top_k: int = 6) -> list[dict]:
    """Return [{content, chunk_id, source, page_no, start_ms, end_ms, score}]."""
    from app.knowledge.retrieval.hybrid import hybrid_search

    kb_ids = getattr(ctx, "kb_ids", None)
    if kb_id is None and kb_ids:
        # Instance-assigned workspace KBs: fan out per KB, merge by score.
        seen: dict[str, dict] = {}
        for kid in kb_ids:
            hits = hybrid_search(ctx.db, ctx.workspace_id, query, kid, top_k,
                                 embed_fn=ctx.embed_fn)
            for h in hits:
                key = str(h.chunk.id)
                if key not in seen:
                    seg, src = h.segment, h.source
                    seen[key] = {
                        "content": h.chunk.content,
                        "chunk_id": str(h.chunk.id),
                        "source": (src.filename if src else ""),
                        "page_no": seg.page_no if seg else None,
                        "start_ms": seg.start_ms if seg else None,
                        "end_ms": seg.end_ms if seg else None,
                        "score": h.score,
                    }
        return sorted(seen.values(), key=lambda d: d["score"],
                      reverse=True)[:top_k]
    hits = hybrid_search(ctx.db, ctx.workspace_id, query, kb_id, top_k, embed_fn=ctx.embed_fn)
    out = []
    for h in hits:
        seg, src = h.segment, h.source
        out.append({
            "content": h.chunk.content,
            "chunk_id": str(h.chunk.id),
            "source": (src.filename if src else ""),
            "page_no": seg.page_no if seg else None,
            "start_ms": seg.start_ms if seg else None,
            "end_ms": seg.end_ms if seg else None,
            "score": h.score,
        })
    return out


def memory_search(ctx: ToolContext, query: str, top_k: int = 6) -> list[dict]:
    from app.memory.postgres_provider import PostgresMemoryProvider

    return PostgresMemoryProvider(ctx.db).recall(ctx.workspace_id, ctx.user_id, query, top_k)


def memory_facts(ctx: ToolContext) -> list[dict]:
    from app.memory.postgres_provider import PostgresMemoryProvider

    facts = PostgresMemoryProvider(ctx.db).facts_for_user(ctx.workspace_id, ctx.user_id)
    return [{"key": f.key, "value": f.value, "category": f.category} for f in facts]


TOOLS = {"knowledge_search": knowledge_search, "memory_search": memory_search, "memory_facts": memory_facts}


def global_knowledge_search(ctx: ToolContext, query: str, top_k: int = 6) -> list[dict]:
    """Search ONLY assigned global KBs (locked: kb_ids come from the
    definition assignment, never from user input)."""
    from app.knowledge.retrieval.hybrid import hybrid_search_global

    kids = list(getattr(ctx, "global_kb_ids", None) or [])
    if not kids:
        return []
    hits = hybrid_search_global(ctx.db, query, kids, top_k, embed_fn=ctx.embed_fn)
    out = []
    for h in hits:
        seg, src = h.segment, h.source
        out.append({
            "content": h.chunk.content,
            "chunk_id": str(h.chunk.id),
            "source": (src.filename if src else ""),
            "page_no": seg.page_no if seg else None,
            "start_ms": seg.start_ms if seg else None,
            "end_ms": seg.end_ms if seg else None,
            "score": h.score,
        })
    return out
