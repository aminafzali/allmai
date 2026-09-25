"""LangGraph runtime — internal to the Agent Engine.

Linear pipeline (each node is also directly unit-testable):
load_memory -> retrieve_knowledge -> build_prompt -> llm_call -> final.

Phase 1: retrieval merges workspace chunks + assigned-global chunks;
the prompt composes effective instructions + instance runtime state +
conversation state + memory + both contexts. The graph shape (4 nodes)
is unchanged.

No router/client may import this module; they use AgentService.
"""

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.agents.safety import MAX_HISTORY_TURNS, SAFETY_PREAMBLE
from app.agents.tools import (
    ToolContext,
    global_knowledge_search,
    knowledge_search,
    memory_facts,
    memory_search,
)


class AgentState(TypedDict, total=False):
    workspace_id: str
    user_id: str
    agent_key: str
    agent_id: str
    definition_id: str | None
    spec: dict
    instructions: str
    agent_state: dict
    conv_state: dict
    instance_kb_ids: list
    message: str
    kb_id: str | None
    history: list
    facts: list
    memory_hits: list
    summary: str
    chunks: list
    workspace_chunks: list
    global_chunks: list
    prompt: str
    answer: str
    citations: list


@dataclass
class RuntimeDeps:
    db: Any
    embed_fn: Any = None
    generate_fn: Any = None
    model: str | None = None
    temperature: float = 0.7
    max_tokens: int = 1500


def _tools_for(spec: dict) -> list[str]:
    return list(spec.get("tools", []))


def _assigned_global_ids(deps: RuntimeDeps, state: dict) -> list:
    did = state.get("definition_id")
    if not did:
        return []
    try:
        from app.agents.definitions_service import assigned_global_kb_ids

        return assigned_global_kb_ids(deps.db, did)
    except Exception:
        return []


def node_load_memory(state: dict, deps: RuntimeDeps) -> dict:
    ctx = ToolContext(db=deps.db, workspace_id=state["workspace_id"], user_id=state["user_id"])
    tools = _tools_for(state.get("spec", {}))
    facts = memory_facts(ctx) if "memory_search" in tools or "memory_facts" in tools else []
    mem_hits = memory_search(ctx, state["message"]) if "memory_search" in tools else []
    # Locked scoping: per-instance summary, legacy fallback only for
    # definition-less (pre-Phase-1) instances.
    summary = ""
    try:
        agent_id, agent_key = state.get("agent_id"), state.get("agent_key", "")
        if agent_id:
            from app.memory.postgres_provider import PostgresMemoryProvider

            mem = PostgresMemoryProvider(deps.db)
            summary = mem.get_summary(
                state["workspace_id"], state["user_id"],
                scope=f"agent_instance:{agent_id}")
            if not summary and not state.get("definition_id"):
                summary = mem.get_summary(
                    state["workspace_id"], state["user_id"],
                    scope=f"agent:{agent_key}")
        else:
            from app.memory.postgres_provider import PostgresMemoryProvider

            summary = PostgresMemoryProvider(deps.db).get_summary(
                state["workspace_id"], state["user_id"],
                scope=f"agent:{state['agent_key']}")
    except Exception:
        summary = ""
    return {"facts": facts, "memory_hits": mem_hits, "summary": summary}


def node_retrieve_knowledge(state: dict, deps: RuntimeDeps) -> dict:
    tools = _tools_for(state.get("spec", {}))
    if "knowledge_search" not in tools:
        return {"chunks": [], "workspace_chunks": [], "global_chunks": []}
    ctx = ToolContext(db=deps.db, workspace_id=state["workspace_id"],
                      user_id=state["user_id"], embed_fn=deps.embed_fn,
                      kb_ids=state.get("instance_kb_ids"))
    ws_chunks = knowledge_search(ctx, state["message"], kb_id=state.get("kb_id"), top_k=4)
    glob_chunks: list = []
    if state.get("definition_id"):
        ctx.global_kb_ids = _assigned_global_ids(deps, state)
        glob_chunks = global_knowledge_search(ctx, state["message"], top_k=4)
    # Merge preserving workspace-first ordering, de-duplicated.
    seen: dict[str, dict] = {}
    for c in list(ws_chunks) + list(glob_chunks):
        seen.setdefault(c["chunk_id"], c)
    merged = sorted(seen.values(), key=lambda d: d.get("score", 0), reverse=True)[:6]
    return {"workspace_chunks": ws_chunks, "global_chunks": glob_chunks,
            "chunks": merged}


def node_build_prompt(state: dict, deps: RuntimeDeps) -> dict:
    lines = [
        state.get("instructions") or state.get("spec", {}).get("goal", "You are a helpful assistant."),
        SAFETY_PREAMBLE,
    ]
    agent_state = state.get("agent_state") or {}
    if agent_state:
        kv = "\n".join(f"- {k}: {str(v)[:300]}" for k, v in list(agent_state.items())[:12])
        lines.append(f"Agent runtime state (this instance):\n{kv}")
    conv_state = state.get("conv_state") or {}
    turns = conv_state.get("turns")
    if turns is not None:
        lines.append(f"Conversation progress: turn {turns}.")
    if state.get("summary"):
        lines.append(f"Conversation summary so far: {state['summary']}")
    if state.get("facts"):
        facts_txt = "\n".join(f"- {f['key']}: {f['value']}" for f in state["facts"][:20])
        lines.append(f"Known about the user:\n{facts_txt}")
    if state.get("memory_hits"):
        mem_txt = "\n".join(f"- {m['key']}: {m['value']}" for m in state["memory_hits"][:8])
        lines.append(f"Relevant memories:\n{mem_txt}")
    chunks = state.get("chunks") or []
    if chunks:
        g = state.get("global_chunks") or []
        w = state.get("workspace_chunks") or []
        if g or w:
            if g:
                g_txt = "\n\n".join(f"[G{i}] ({c['source']}) {c['content']}"
                                    for i, c in enumerate(g, 1))
                lines.append(f"Global agent knowledge (cite as [G1], [G2], ...):\n{g_txt}")
            if w:
                w_txt = "\n\n".join(f"[W{i}] ({c['source']}) {c['content']}"
                                    for i, c in enumerate(w, 1))
                lines.append(f"Workspace knowledge (cite as [W1], [W2], ...):\n{w_txt}")
        else:
            ctx_txt = "\n\n".join(f"[{i}] ({c['source']}) {c['content']}"
                                  for i, c in enumerate(chunks, 1))
            lines.append(f"Retrieved context (cite as [1], [2], ...):\n{ctx_txt}")
    history = (state.get("history") or [])[-MAX_HISTORY_TURNS:]
    if history:
        hist_txt = "\n".join(f"{m['role']}: {m['content'][:800]}" for m in history)
        lines.append(f"Recent conversation:\n{hist_txt}")
    lines.append(f"User: {state['message']}\nAssistant (answer in Persian):")
    return {"prompt": "\n\n".join(lines)}


def node_llm_call(state: dict, deps: RuntimeDeps) -> dict:
    generate = deps.generate_fn
    if generate is None:
        from app.ai.openai_compat import OpenAICompatProvider

        generate = OpenAICompatProvider().generate
    answer = generate(state["prompt"], model=deps.model,
                      temperature=deps.temperature, max_tokens=deps.max_tokens)
    citations = [
        {"n": i, "chunk_id": c["chunk_id"], "source": c["source"],
         "page_no": c["page_no"], "start_ms": c["start_ms"], "end_ms": c["end_ms"]}
        for i, c in enumerate(state.get("chunks", []), 1)
    ]
    return {"answer": (answer or "").strip(), "citations": citations}


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("load_memory", lambda s, config=None: node_load_memory(s, config["configurable"]["deps"]))
    g.add_node("retrieve_knowledge", lambda s, config=None: node_retrieve_knowledge(s, config["configurable"]["deps"]))
    g.add_node("build_prompt", lambda s, config=None: node_build_prompt(s, config["configurable"]["deps"]))
    g.add_node("llm_call", lambda s, config=None: node_llm_call(s, config["configurable"]["deps"]))
    g.set_entry_point("load_memory")
    g.add_edge("load_memory", "retrieve_knowledge")
    g.add_edge("retrieve_knowledge", "build_prompt")
    g.add_edge("build_prompt", "llm_call")
    g.add_edge("llm_call", END)
    return g.compile()


_COMPILED = None


def run_agent(initial_state: dict, deps: RuntimeDeps) -> dict:
    global _COMPILED
    if _COMPILED is None:
        _COMPILED = build_graph()
    return dict(_COMPILED.invoke(initial_state, config={"configurable": {"deps": deps}}))
