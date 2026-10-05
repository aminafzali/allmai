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
    excel_chunks: list
    web_chunks: list
    maps_chunks: list
    fulltext: object
    file_inventory: list
    file_text: object
    file_ambiguity: list
    suggested: list
    searched_kbs: list
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


# NOTE: web_search/maps_search are CLIENT-executed (browser runs them via
# /chat/resume; see agents/client_tools + service.chat_resume_events).
# This node never searches externally — server-side provider impls in
# tools.py stay dormant unless explicitly selected + keyed in config.


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
    if ("knowledge_search" not in tools and "global_knowledge_search" not in tools
            and "excel_query" not in tools):
        return {"chunks": [], "workspace_chunks": [], "global_chunks": [],
                "excel_chunks": [], "web_chunks": [], "maps_chunks": [],
                "fulltext": None, "file_inventory": [], "file_text": None,
                "file_ambiguity": [], "suggested": [], "searched_kbs": []}
    ctx = ToolContext(db=deps.db, workspace_id=state["workspace_id"],
                      user_id=state["user_id"], embed_fn=deps.embed_fn,
                      kb_ids=state.get("instance_kb_ids"),
                      generate_fn=deps.generate_fn)
    ws_chunks = knowledge_search(ctx, state["message"], kb_id=state.get("kb_id"), top_k=4) \
        if "knowledge_search" in tools else []
    glob_chunks: list = []
    if state.get("definition_id") and (
            "knowledge_search" in tools or "global_knowledge_search" in tools):
        ctx.global_kb_ids = _assigned_global_ids(deps, state)
        glob_chunks = global_knowledge_search(ctx, state["message"], top_k=4)
    # Structured Excel answers (DuckDB) for analytic questions; the tool
    # itself yields [] for informational questions so RAG handles those.
    excel_chunks: list = []
    if "excel_query" in tools:
        try:
            from app.agents.tools import excel_query

            excel_chunks = excel_query(ctx, state["message"],
                                       kb_id=state.get("kb_id"))
        except Exception:
            excel_chunks = []
    # Merge preserving workspace-first ordering, de-duplicated.
    # web_chunks/maps_chunks stay [] here: browser-gathered context is
    # injected by chat_resume_events (client flow), never searched here.
    web_chunks: list = []
    maps_chunks: list = []
    seen: dict[str, dict] = {}
    for c in list(ws_chunks) + list(glob_chunks):
        seen.setdefault(c["chunk_id"], c)
    merged = sorted(seen.values(), key=lambda d: d.get("score", 0), reverse=True)[:6]
    for c in excel_chunks:
        seen.setdefault(c["chunk_id"], c)
        merged.append(c)
    # Query rewrite (thin first round, OPTIONAL via
    # retrieval.hybrid.query_expansion_enabled): ONE cheap rewrite round,
    # then extra scoped searches merged in. Only when knowledge retrieval
    # came back thin (< 2 merged hits).
    expand_on = True
    try:
        from app.ai.settings import list_settings as _ls

        expand_on = bool((_ls(deps.db).get("retrieval.hybrid") or {}).get(
            "query_expansion_enabled", True))
    except Exception:
        expand_on = True
    if (expand_on
            and ("knowledge_search" in tools or "global_knowledge_search" in tools)
            and len(merged) < 2):
        try:
            from app.agents.query_rewrite import rewrite_queries

            for q in rewrite_queries(state["message"], state.get("history")):
                extra = knowledge_search(ctx, q, kb_id=state.get("kb_id"),
                                         top_k=4) \
                    if "knowledge_search" in tools else []
                if state.get("definition_id"):
                    extra += global_knowledge_search(ctx, q, top_k=4)
                for d in extra:
                    if d["chunk_id"] not in seen:
                        seen[d["chunk_id"]] = d
            merged = sorted(seen.values(), key=lambda d: d.get("score", 0),
                            reverse=True)[:6]
        except Exception:
            pass
    # Full extracted text when one source dominates (or is named): the
    # model answers from the whole article, citations stay chunk-level.
    fulltext = None
    try:
        from app.knowledge.retrieval.passages import maybe_source_fulltext

        fulltext = maybe_source_fulltext(
            deps.db, merged, state["message"])
    except Exception:
        fulltext = None
    # KB titles for the smart-empty message (which KBs were searched).
    searched_kbs: list = []
    try:
        from app.knowledge.models import KnowledgeBase as _KB

        from app.common.base import coerce_uuid as _coerce

        kids = list(state.get("instance_kb_ids") or [])
        gkids = list(getattr(ctx, "global_kb_ids", None) or [])
        allids = [str(k) for k in kids + gkids]
        if allids:
            rows = (deps.db.query(_KB)
                    .filter(_KB.id.in_([_coerce(k) for k in allids])).all())
            searched_kbs = [{"title": r.title or "", "scope": r.scope or ""}
                            for r in rows]
    except Exception:
        searched_kbs = []
    # Deterministic file tools (no guessing): complete inventory on
    # list-intent; on ambiguity ask first; else full text of the NAMED
    # file. Coaching suggestions come from real filenames when empty.
    file_inventory: list = []
    file_text = None
    file_ambiguity: list = []
    suggested: list = []
    if "knowledge_search" in tools or "global_knowledge_search" in tools:
        try:
            from app.agents.kb_files import (
                list_kb_files,
                read_named_file,
                wants_file_list,
            )

            # Locked scoping: the user's picked KB wins over the agent's
            # assigned KBs. Falling back to instance KBs (or the whole
            # workspace) while a KB is picked would answer from other KBs.
            picked = state.get("kb_id")
            scoped_kids = [picked] if picked else list(
                state.get("instance_kb_ids") or [])
            gkids = list(getattr(ctx, "global_kb_ids", None) or [])
            files = list_kb_files(deps.db, state["workspace_id"],
                                  scoped_kids, gkids)
            if wants_file_list(state["message"]):
                file_inventory = files
            else:
                from app.agents.kb_files import ambiguity_options as _amb

                nq = f" {(state['message'] or '').lower()} "
                tied = None
                if any(w in nq for w in
                       ("فایل", "متن", "سند", "مدرک", "file")):
                    tied = _amb(files, state["message"])
                if tied is not None:
                    file_ambiguity = tied[:4]
                else:
                    file_text = read_named_file(
                        deps.db, state["workspace_id"],
                        scoped_kids, gkids,
                        state["message"])
            if not merged and not file_inventory and not file_text \
                    and not file_ambiguity:
                names = [f["filename"] for f in files[:3] if f.get("filename")]
                if names:
                    suggested.append(f"«{names[0]}» درباره چیست؟")
                    suggested.append(f"نکات مهم «{names[0]}» را بگو")
                if len(names) >= 2:
                    suggested.append(
                        f"فرق «{names[0]}» و «{names[1]}» چیست؟")
        except Exception:
            file_inventory, file_text = [], None
            file_ambiguity, suggested = [], []
    return {"workspace_chunks": ws_chunks, "global_chunks": glob_chunks,
            "excel_chunks": excel_chunks, "web_chunks": web_chunks,
            "maps_chunks": maps_chunks, "chunks": merged,
            "fulltext": fulltext, "file_inventory": file_inventory,
            "file_text": file_text, "file_ambiguity": file_ambiguity,
            "suggested": suggested, "searched_kbs": searched_kbs}


def node_build_prompt(state: dict, deps: RuntimeDeps) -> dict:
    def _ref(c: dict) -> str:
        dt = (c.get("doc_title") or "").strip()
        return f" ({c['source']} / {dt})" if dt else f" ({c['source']})"

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
    inv = state.get("file_inventory") or []
    ftext = state.get("file_text") or None
    amb = state.get("file_ambiguity") or []
    tools = _tools_for(state.get("spec", {}))
    kb_on = ("knowledge_search" in tools
             or "global_knowledge_search" in tools)
    if chunks or inv or ftext or amb or kb_on:
        g = state.get("global_chunks") or []
        w = state.get("workspace_chunks") or []
        x = state.get("excel_chunks") or []
        s = state.get("web_chunks") or []
        m = state.get("maps_chunks") or []
        ft = state.get("fulltext") or None
        if g or w or x or s or m or inv or ftext or amb:
            if amb:
                amb_txt = "\n".join(
                    f"{i}. {f['filename']} ({f['type']})"
                    for i, f in enumerate(amb, 1))
                lines.append(
                    "The user named a file ambiguously — candidates:\n"
                    + amb_txt +
                    "\nAsk IN PERSIAN which one they mean by quoting EXACTLY "
                    "these options as a numbered list (no additions, no "
                    "duplicates) and wait for the choice. Do NOT answer "
                    "from either file yet.")
            if inv:
                inv_txt = "\n".join(
                    f"{i}. {f['filename']} ({f['type']}, {f['status']})"
                    for i, f in enumerate(inv, 1))
                lines.append(
                    "Files in the knowledge base (COMPLETE inventory — "
                    "answer file-list questions ONLY from this list, "
                    "using exact filenames):\n" + inv_txt)
            if isinstance(ftext, dict) and ftext.get("text"):
                lines.append(
                    f"FILE «{ftext.get('filename') or ''}» — FULL TEXT "
                    f"({ftext.get('type') or ''}"
                    f"{', truncated' if ftext.get('truncated') else ''}). "
                    f"TASK (do exactly this now, on the text below): "
                    f"{state['message']}\n"
                    "Rules: you HAVE the text — translate/summarize/quote it "
                    "directly as asked. Never refuse, never ask the user to "
                    "paste it, never substitute a vague summary for a "
                    "requested translation. Cite [W]/[G] chunks where they "
                    "support the answer:\n"
                    f"{ftext['text']}")
            if ft and isinstance(ft, dict) and ft.get("text"):
                title = ft.get("title") or ft.get("filename") or ""
                lines.append(
                    f"Full extracted text of «{title}» "
                    f"({'truncated' if ft.get('truncated') else 'complete'}). "
                    f"TASK (do exactly this now, on the text below): "
                    f"{state['message']}\n"
                    "Rules: answer from this text; directly perform requested "
                    "transformations (translate, summarize, quote) instead of "
                    "describing the file. Never refuse file operations; cite "
                    "the [W]/[G] chunks as usual:\n"
                    f"{ft['text']}")
            if g:
                g_txt = "\n\n".join(f"[G{i}]{_ref(c)} {c['content']}"
                                    for i, c in enumerate(g, 1))
                lines.append(f"Global agent knowledge (cite as [G1], [G2], ...):\n{g_txt}")
            if w:
                w_txt = "\n\n".join(f"[W{i}]{_ref(c)} {c['content']}"
                                    for i, c in enumerate(w, 1))
                lines.append(f"Workspace knowledge (cite as [W1], [W2], ...):\n{w_txt}")
            if x:
                # Structured Excel answers carry their own source/sheet/SQL
                # citations; the model must quote the numbers as-is.
                x_txt = "\n\n".join(
                    f"[X{i}] ({c['source']} / sheet {c.get('sheet')}) {c['content']}"
                    for i, c in enumerate(x, 1))
                lines.append(
                    "Structured Excel answers (cite as [X1], [X2], ...; "
                    f"numbers are computed, not estimated):\n{x_txt}")
            if s:
                s_txt = "\n\n".join(
                    f"[S{i}] ({c['source']}) {c['content']}"
                    for i, c in enumerate(s, 1))
                lines.append(
                    "Web search results via Google (cite as [S1], [S2], ...; "
                    f"prefer them for freshness/currency questions):\n{s_txt}")
            if m:
                m_txt = "\n\n".join(
                    f"[M{i}] ({c['source']}) {c['content']}"
                    for i, c in enumerate(m, 1))
                lines.append(
                    "Nearby places from Google Maps (cite as [M1], [M2], ...; "
                    f"quote address/rating/phone as-is):\n{m_txt}")
            if chunks:
                lines.append(
                    "The context blocks above come from the user's OWN "
                    "uploaded files and knowledge bases. Use them directly; "
                    "never claim you lack access to files or data.")
        else:
            if kb_on:
                kbs = [k.get("title") or "" for k in
                       (state.get("searched_kbs") or []) if k.get("title")]
                where = ("«" + "»، «".join(kbs) + "»") if kbs else "هیچ پایگاه دانشی"
                lines.append(
                    "Retrieval found NOTHING in the searched knowledge "
                    f"bases ({where}). Do NOT invent file contents. Say "
                    "briefly in Persian that nothing was found HERE, name "
                    "the searched bases, and suggest checking another base "
                    "or uploading the file. Never claim access limitations.")
                # drop the empty generic context line below for this case
                lines.append("Retrieved context: (empty)")
                sug = state.get("suggested") or []
                if sug:
                    lines.append(
                        "Offer EXACTLY these follow-up questions (in "
                        "Persian, as-is, no others):\n" +
                        "\n".join(f"- {q}" for q in sug))
            else:
                ctx_txt = "\n\n".join(f"[{i}]{_ref(c)} {c['content']}"
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
    """One agent turn with usage tracing (P3).

    Binds a fresh trace + workspace/user context (inherited by retrieval,
    rerank and model spans below) and records the turn-level agent span.
    Token-level costs land on the model span via the provider hook,
    linked by the same trace_id.
    """
    import time as _time

    from app.usage import context as _uctx
    from app.usage import recorder as _urec

    global _COMPILED
    state = initial_state or {}
    ws, user = state.get("workspace_id"), state.get("user_id")
    trace = _uctx.bind_usage_context(workspace_id=ws, user_id=user,
                                     trace_id=_uctx.new_trace_id())
    t0, err = _time.perf_counter(), ""
    try:
        if _COMPILED is None:
            _COMPILED = build_graph()
        result = dict(_COMPILED.invoke(
            initial_state, config={"configurable": {"deps": deps}}))
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"[:300]
        raise
    finally:
        _urec.record_event(
            "agent", workspace_id=ws, user_id=user, trace_id=trace,
            model=getattr(deps, "model", None),
            latency_ms=round((_time.perf_counter() - t0) * 1000, 1),
            ok=not err, error=err,
            meta={"agent_key": str(state.get("agent_key") or ""),
                  "agent_id": str(state.get("agent_id") or "")})
    return result
