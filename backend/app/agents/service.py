"""AgentService: the ONLY entry point clients (Next.js web app) use.

Owns agent CRUD, conversation/state persistence and chat orchestration.
The graph runtime stays internal (agents/runtime/).

Phase 1: instances resolve their spec from the global AgentDefinition
(DB first, AGENT_CATALOG legacy fallback); effective instructions come
from state.get_effective_instructions (Definition + custom; config legacy
fallback only); summaries are per-instance with locked legacy fallback
(see agents/state.py); global+workspace retrieval merges in the runner.
"""

import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.models import Agent
from app.agents.registry import AGENT_CATALOG, get_agent_spec
from app.agents.runtime.langgraph_runner import RuntimeDeps, run_agent
from app.agents.safety import (
    MAX_HISTORY_TURNS,
    contains_injection_attempt,
    sanitize_user_message,
)
from app.ai.settings import get_setting
from app.auth.service import audit
from app.common.base import coerce_uuid
from app.conversations.models import Conversation, Message
from app.core.database import set_definition_context
from app.users.models import User
from app.workspaces.models import Workspace

AGENT_TYPES = ("assistant", "agent")
_KEY_RE = re.compile(r"^[a-z0-9_]{3,80}$")


def _load_definition(db: Session, agent):
    did = getattr(agent, "definition_id", None)
    if did is None:
        return None
    from app.agents.definitions_models import AgentDefinition

    return (
        db.query(AgentDefinition)
        .filter(AgentDefinition.id == coerce_uuid(did))
        .first()
    )


def create_agent(db: Session, ws: Workspace, user: User, key: str, type: str,
                 name: str = "", config: dict | None = None,
                 definition_id=None, owner_user_id=None,
                 custom_instructions: str = "",
                 kb_ids: list | None = None) -> Agent:
    from app.agents.definitions_service import (
        set_instance_knowledge,
        validate_owner_membership,
    )

    if not _KEY_RE.match(key or ""):
        raise HTTPException(422, "key must match [a-z0-9_]{3,80}")
    if type not in AGENT_TYPES:
        raise HTTPException(422, f"type must be one of {AGENT_TYPES}")
    if key in AGENT_CATALOG:
        spec = get_agent_spec(key)
        type = spec["type"]
        name = name or key
    # Resolve definition: explicit id wins, else seed lookup by key.
    definition = None
    if definition_id is not None:
        from app.agents.definitions_service import get_definition

        definition = get_definition(db, definition_id)
        if not definition.is_active:
            raise HTTPException(422, "definition is not active")
    else:
        from app.agents.definitions_service import get_definition_by_key

        definition = get_definition_by_key(db, key)
    # Owner rule (locked): plain members only for self; workspace
    # owner/admin or system admin may create for another member.
    owner = None
    if owner_user_id is not None:
        from app.workspaces.service import require_workspace_role

        try:
            require_workspace_role(db, ws, user, ("owner", "admin"))
            owner = coerce_uuid(owner_user_id)
        except HTTPException:
            if str(owner_user_id) != str(user.id):
                raise HTTPException(403, "cannot create personal agent for another user")
            owner = coerce_uuid(user.id)
        validate_owner_membership(db, ws, owner)
    # Locked: one personal instance per (workspace, definition, user).
    # App-layer pre-check (portable across SQLite/PG); the partial unique
    # index is the second layer (and wins races via IntegrityError below).
    if owner is not None and definition is not None:
        dup = (
            db.query(Agent)
            .filter(Agent.workspace_id == ws.id,
                    Agent.definition_id == definition.id,
                    Agent.owner_user_id == owner)
            .first()
        )
        if dup is not None:
            raise HTTPException(409, "personal instance already exists for "
                                     "this workspace/definition/user")
    agent = Agent(workspace_id=ws.id, key=key, type=type, name=name[:200],
                  config=config or {},
                  definition_id=(definition.id if definition else None),
                  owner_user_id=owner,
                  custom_instructions=(custom_instructions or "")[:5000],
                  runtime_state={})
    db.add(agent)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "personal instance already exists for "
                                 "this workspace/definition/user")
    db.refresh(agent)
    if kb_ids:
        set_instance_knowledge(db, ws, agent, kb_ids)
        db.refresh(agent)
    audit(db, "agents.create", actor_user_id=user.id, workspace_id=ws.id,
          entity="agent", entity_id=agent.id, meta={"key": key})
    return agent


def list_agents(db: Session, ws: Workspace, user: User | None = None) -> list[Agent]:
    from app.agents.definitions_service import visible_agents_query

    if user is None:
        return (
            db.query(Agent).filter(Agent.workspace_id == ws.id)
            .order_by(Agent.created_at.desc()).all()
        )
    return (
        visible_agents_query(db, ws.id, user)
        .order_by(Agent.created_at.desc()).all()
    )


def get_agent(db: Session, ws: Workspace, agent_id, user: User | None = None) -> Agent:
    from app.agents.definitions_service import require_agent_access

    agent = (
        db.query(Agent)
        .filter(Agent.id == coerce_uuid(agent_id), Agent.workspace_id == ws.id)
        .first()
    )
    if agent is None:
        raise HTTPException(404, "agent not found")
    if user is not None:
        require_agent_access(agent, user)
    return agent


def update_agent(db: Session, ws: Workspace, agent: Agent, user: User,
                 patch: dict) -> Agent:
    """Update custom_instructions / runtime_state / workspace KBs / name."""
    from app.agents.definitions_service import (
        set_instance_knowledge,
        validate_owner_membership,
    )

    if "custom_instructions" in patch and patch["custom_instructions"] is not None:
        agent.custom_instructions = str(patch["custom_instructions"])[:5000]
    if "name" in patch and patch["name"] is not None:
        agent.name = str(patch["name"])[:200]
    if "runtime_state" in patch and isinstance(patch["runtime_state"], dict):
        from app.agents.state import update_agent_state

        update_agent_state(db, agent, patch["runtime_state"])
        db.refresh(agent)
        return agent
    if "owner_user_id" in patch and patch["owner_user_id"] is not None:
        from app.workspaces.service import require_workspace_role

        try:
            require_workspace_role(db, ws, user, ("owner", "admin"))
        except HTTPException:
            raise HTTPException(403, "cannot reassign personal agent owner")
        validate_owner_membership(db, ws, patch["owner_user_id"])
        agent.owner_user_id = coerce_uuid(patch["owner_user_id"])
    db.commit()
    db.refresh(agent)
    if "kb_ids" in patch and patch["kb_ids"] is not None:
        set_instance_knowledge(db, ws, agent, patch["kb_ids"])
        db.refresh(agent)
    return agent


def _conversation(db: Session, ws: Workspace, agent: Agent, user: User,
                  conversation_id) -> Conversation:
    from app.agents.definitions_service import require_agent_access

    # Locked: creating/reading a conversation through someone else's
    # personal agent is 404 (no existence leak).
    require_agent_access(agent, user)
    if conversation_id is not None:
        conv = (
            db.query(Conversation)
            .filter(
                Conversation.id == coerce_uuid(conversation_id),
                Conversation.workspace_id == ws.id,
                Conversation.agent_id == agent.id,
                Conversation.user_id == user.id,
            )
            .first()
        )
        if conv is None:
            raise HTTPException(404, "conversation not found")
        return conv
    conv = Conversation(workspace_id=ws.id, agent_id=agent.id, user_id=user.id, state={})
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def _history(db: Session, conv: Conversation) -> list[dict]:
    rows = (
        db.query(Message).filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc()).limit(MAX_HISTORY_TURNS * 2).all()
    )
    return [{"role": m.role, "content": m.content} for m in reversed(rows)]


def _tools_of(definition, spec: dict) -> list[str]:
    if definition is not None:
        raw = getattr(definition, "tools", None)
        if isinstance(raw, dict) and isinstance(raw.get("tools"), list):
            return [str(t) for t in raw["tools"]]
        if isinstance(raw, list):
            return [str(t) for t in raw]
    return list(spec.get("tools", []))


def _model_config(db: Session, agent: Agent, definition=None) -> tuple[str | None, dict]:
    """Effective (model, chat_cfg) via the central resolver.

    chat_cfg carries provider/model/temperature/max_tokens (+sources);
    existing callers keep working unchanged.
    """
    from app.ai.settings import resolve_agent_chat

    cfg = resolve_agent_chat(db, agent.key, definition)
    return cfg.get("model"), cfg


def _provider_for(chat_cfg: dict):
    """Provider instance honoring the resolved agent provider.

    Keyless dormant providers (e.g. direct gemini selected in Studio
    without GEMINI_API_KEY) fall back to openai_compat LOUDLY instead of
    raising at call time — a misconfigured dropdown must never 500 a turn.
    """
    import logging as _logging

    from app.ai.factory import get_provider

    try:
        p = get_provider((chat_cfg or {}).get("provider") or "openai_compat")
    except ValueError:
        return get_provider("openai_compat")
    if getattr(p, "provider_name", "") == "gemini" and not getattr(p, "api_key", ""):
        _logging.getLogger(__name__).warning(
            "provider 'gemini' selected without GEMINI_API_KEY; "
            "falling back to openai_compat (check Studio model settings)")
        return get_provider("openai_compat")
    return p


def _spec_for(agent: Agent, definition=None) -> dict:
    if definition is not None:
        goal = (getattr(definition, "instructions", None) or "").strip()
        return {"type": getattr(definition, "type", agent.type) or agent.type,
                "goal": goal,
                "tools": _tools_of(definition, {}),
                "definition_id": str(definition.id)}
    try:
        return get_agent_spec(agent.key)
    except KeyError:
        return {"type": agent.type, "goal": "", "tools": []}


def _base_state(ws: Workspace, agent: Agent, user: User, clean: str,
                kb_id, history: list, spec: dict, instructions: str,
                definition=None, agent_state: dict | None = None,
                conv_state: dict | None = None,
                instance_kb_ids: list | None = None) -> dict:
    return {
        "workspace_id": str(ws.id), "user_id": str(user.id),
        "agent_key": agent.key, "agent_id": str(agent.id),
        "definition_id": str(definition.id) if definition else None,
        "spec": spec, "instructions": instructions,
        "agent_state": agent_state or {}, "conv_state": conv_state or {},
        "instance_kb_ids": [str(k) for k in (instance_kb_ids or [])],
        "message": clean, "kb_id": str(kb_id) if kb_id else None, "history": history,
    }


def maybe_title_conversation(db: Session, conv, clean: str,
                             generate_fn=None) -> str:
    """Set a short AI title once (first turn only). Fail-open: any error
    (or missing generator) leaves the title empty. One tiny LLM call."""
    try:
        if getattr(conv, "title", "") or generate_fn is None:
            return getattr(conv, "title", "") or ""
        prompt = ("یک عنوان فارسی خیلی کوتاه (حداکثر ۶ کلمه) برای این گفتگو "
                  "بساز. فقط خود عنوان را برگردان، بدون نقل‌قول و توضیح:\n"
                  f"کاربر: {(clean or '')[:300]}")
        title = str(generate_fn(prompt, temperature=0.3, max_tokens=60) or "")
        title = title.strip().strip("\"'«»").strip()[:80]
        if len(title) >= 3:
            conv.title = title
            db.commit()
            return title
    except Exception:
        pass
    return getattr(conv, "title", "") or ""


def _persist_turn(db: Session, ws: Workspace, agent: Agent, user: User,
                  conv: Conversation, clean: str, answer: str,
                  citations: list, kb_id, generate_fn=None) -> None:
    from app.agents.state import update_conversation_state, write_instance_summary
    from app.memory.postgres_provider import PostgresMemoryProvider  # noqa: F401

    db.add(Message(workspace_id=ws.id, conversation_id=conv.id, role="user", content=clean))
    db.add(Message(workspace_id=ws.id, conversation_id=conv.id, role="assistant",
                   content=answer, citations={"citations": citations}))
    db.commit()
    maybe_title_conversation(db, conv, clean, generate_fn)
    # Conversation-level counters stay in conversations.state ...
    update_conversation_state(db, conv, {
        "turns": int((conv.state or {}).get("turns", 0)) + 1,
        **({"last_kb_id": str(kb_id)} if kb_id else {}),
    })
    # ... while the rolling summary is per-instance (locked scoping).
    write_instance_summary(
        db, ws.id, user.id, agent,
        f"Last exchange: user asked {clean[:200]}; assistant replied {answer[:200]}",
    )
    if contains_injection_attempt(clean):
        audit(db, "agents.injection_attempt", actor_user_id=user.id, workspace_id=ws.id,
              entity="agent", entity_id=agent.id,
              meta={"conversation_id": str(conv.id), "excerpt": clean[:200]})
    audit(db, "agents.chat", actor_user_id=user.id, workspace_id=ws.id,
          entity="agent", entity_id=agent.id,
          meta={"conversation_id": str(conv.id), "citations": len(citations)})


def _prepare_chat(db: Session, ws: Workspace, agent: Agent, user: User,
                  message: str, conversation_id=None, kb_id=None):
    from app.agents.definitions_service import require_agent_access
    from app.agents.state import get_agent_state, get_conversation_state

    require_agent_access(agent, user)
    if kb_id is not None:
        # Locked scoping: a picked KB must belong to this workspace,
        # otherwise retrieval/file tools could answer from elsewhere.
        from app.knowledge.service import get_kb as _get_kb

        _get_kb(db, ws, kb_id)  # 404 when foreign/missing
    try:
        clean = sanitize_user_message(message)
    except ValueError:
        raise HTTPException(422, "message is empty")
    conv = _conversation(db, ws, agent, user, conversation_id)
    history = _history(db, conv)

    definition = _load_definition(db, agent)
    spec = _spec_for(agent, definition)
    from app.agents.state import get_effective_instructions

    instructions = get_effective_instructions(definition, agent)
    model, chat_cfg = _model_config(db, agent, definition)
    # Bind definition GUC for global-KB RLS (reset on release).
    set_definition_context(db, definition.id if definition else None)
    return clean, conv, history, definition, spec, instructions, model, chat_cfg


def chat(db: Session, ws: Workspace, agent: Agent, user: User, message: str,
         conversation_id=None, kb_id=None, embed_fn=None, generate_fn=None) -> dict:
    from app.agents.definitions_service import instance_kb_ids
    from app.agents.state import get_agent_state, get_conversation_state

def _translate_piece(chat_cfg: dict, filename: str, piece: str,
                     idx_human: int, total: int,
                     generate_fn=None) -> str | None:
    """Translate one piece (fail-open -> None).

    Uses the turn's EFFECTIVE generate_fn (same provider the chat answer
    itself uses) — never re-resolve from chat_cfg, whose provider field
    may name a keyless/dormant provider (silent mismatch otherwise).
    """
    import logging as _logging

    try:
        from app.agents.translate_flow import (
            PIECE_MAX_TOKENS,
            translate_prompt,
        )

        if generate_fn is None:
            generate_fn = _provider_for(chat_cfg).generate
        text = generate_fn(
            translate_prompt(piece, idx_human, total, filename),
            temperature=0.3, max_tokens=PIECE_MAX_TOKENS) or ""
        text = text.strip()
        return text or None
    except Exception as exc:
        _logging.getLogger("translate_dbg").warning(
            "translate piece failed: %r", exc)
        return None


def _kb_scope(db: Session, agent, definition, kb_id) -> tuple[list, list]:
    """(workspace kb_ids, global kb_ids) for file tools. Never raises."""
    try:
        from app.agents.definitions_service import (
            assigned_global_kb_ids as _gk,
            instance_kb_ids as _ikb,
        )

        gkids = list(_gk(db, definition.id)) if definition is not None else []
        kids = [kb_id] if kb_id else _ikb(db, agent)
        return kids, gkids
    except Exception:
        return [], []


def _translate_turn(db: Session, ws: Workspace, agent, definition, kb_id,
                    clean: str, conv, chat_cfg: dict,
                    generate_fn=None) -> tuple[str, int] | None:
    """Chunked long-document translation (db-driven, both chat paths).

    Returns (answer, remaining) or None to continue normally. Turn 1
    translates piece 1 and stores the rest in conv.state; "ادامه بده"
    serves the next piece. Short texts return None (single-turn flow).
    """
    from app.agents import kb_files as _kbf
    from app.agents.state import (
        get_conversation_state,
        update_conversation_state,
    )
    from app.agents.translate_flow import (
        LONG_TEXT_CHARS,
        is_continue,
        is_translate_request,
        split_pieces,
    )

    cstate = get_conversation_state(conv) or {}
    tr = cstate.get("translating") or {}
    # "ادامه بده": next stored piece (no retrieval needed).
    if is_continue(clean) and isinstance(tr, dict) and tr.get("remaining"):
        pieces = list(tr["remaining"])
        total = int(tr.get("total") or len(pieces))
        done_n = total - len(pieces) + 1
        out = _translate_piece(chat_cfg, tr.get("filename") or "",
                               pieces.pop(0), done_n, total,
                               generate_fn=generate_fn)
        if out is None:
            return None
        update_conversation_state(
            db, conv, {"translating": {**tr, "remaining": pieces}})
        tail = (f"\n\n(بخش {done_n} از {total} — برای ادامه بگو «ادامه بده»)"
                if pieces else "\n\n(پایان ترجمه ✓)")
        return out + tail, len(pieces)
    # New translate request on a LONG file text.
    if not is_translate_request(clean):
        return None
    import logging as _logging

    _tlog = _logging.getLogger("translate_dbg")
    try:
        kids, gkids = _kb_scope(db, agent, definition, kb_id)
        hit = _kbf.read_named_file(db, ws.id, kids, gkids, clean)
    except Exception as exc:
        _tlog.warning("translate scope/read failed: %r", exc)
        return None
    _tlog.warning("translate hit=%s len=%s kids=%s",
                  (hit or {}).get("filename"),
                  len((hit or {}).get("text") or ""), kids)
    if (not isinstance(hit, dict)) or len(hit.get("text") or "") <= LONG_TEXT_CHARS:
        return None  # short text (or no file): single-turn flow handles it
    pieces = split_pieces(hit["text"])
    if len(pieces) <= 1:
        return None
    out = _translate_piece(chat_cfg, hit.get("filename") or "",
                           pieces[0], 1, len(pieces),
                           generate_fn=generate_fn)
    if out is None:
        return None
    update_conversation_state(db, conv, {
        "translating": {"filename": hit.get("filename") or "",
                        "remaining": pieces[1:], "total": len(pieces)}})
    return (out + f"\n\n(بخش ۱ از {len(pieces)} — برای ادامه بگو «ادامه بده»)",
            len(pieces) - 1)


def _ambiguity_answer(db: Session, ws: Workspace, agent, definition,
                      kb_id, clean: str) -> str | None:
    """Deterministic disambiguation (no LLM): tied filename matches get
    a canned numbered question instead of a guessed answer. Only fires
    when the message is actually ABOUT files (else content questions
    sharing a token would be hijacked)."""
    try:
        from app.agents import kb_files as _kbf

        nq = f" {(clean or '').lower()} "
        if not any(w in nq for w in ("فایل", "متن", "سند", "مدرک", "file")):
            return None
        kids, gkids = _kb_scope(db, agent, definition, kb_id)
        files = _kbf.list_kb_files(db, ws.id, kids, gkids)
        options = _kbf.ambiguity_options(files, clean)
        if options is None:
            return None
        return _kbf.ambiguity_question(options)
    except Exception:
        return None


def _verbatim_answer(db: Session, ws: Workspace, agent: Agent,
                     definition, kb_id, clean: str) -> dict | None:
    """Deterministic exact-text tool (kb_files). Returns the answer dict
    or None. Scope: selected KB -> instance KBs -> whole workspace, plus
    definition-assigned global KBs. Never raises (fail-open to LLM)."""
    try:
        from app.agents import kb_files as _kbf

        kids, gkids = _kb_scope(db, agent, definition, kb_id)
        return _kbf.build_verbatim_answer(db, ws.id, kids, gkids, clean)
    except Exception:
        return None


def chat(db: Session, ws: Workspace, agent: Agent, user: User, message: str,
         conversation_id=None, kb_id=None, embed_fn=None, generate_fn=None) -> dict:
    from app.agents.definitions_service import instance_kb_ids
    from app.agents.state import get_agent_state, get_conversation_state

    clean, conv, history, definition, spec, instructions, model, chat_cfg = \
        _prepare_chat(db, ws, agent, user, message, conversation_id, kb_id)
    if generate_fn is None:
        generate_fn = _provider_for(chat_cfg).generate
    # Deterministic verbatim tool FIRST (no LLM): "send me the exact text
    # of file X" is answered straight from the transcript.
    verb = _verbatim_answer(db, ws, agent, definition, kb_id, clean)
    if verb is not None:
        set_definition_context(db, None)
        _persist_turn(db, ws, agent, user, conv, clean, verb["answer"], [],
                      kb_id, generate_fn=generate_fn)
        return {"answer": verb["answer"], "citations": [],
                "conversation_id": str(conv.id)}
    # Deterministic disambiguation SECOND (no LLM): tied filenames get a
    # numbered question instead of a guessed answer.
    amb = _ambiguity_answer(db, ws, agent, definition, kb_id, clean)
    if amb is not None:
        set_definition_context(db, None)
        _persist_turn(db, ws, agent, user, conv, clean, amb, [], kb_id,
                      generate_fn=generate_fn)
        return {"answer": amb, "citations": [],
                "conversation_id": str(conv.id)}
    # Chunked long-document translation (multi-turn "ادامه بده").
    tr = _translate_turn(db, ws, agent, definition, kb_id, clean, conv,
                         chat_cfg, generate_fn=generate_fn)
    if tr is not None:
        answer, remaining = tr
        set_definition_context(db, None)
        _persist_turn(db, ws, agent, user, conv, clean, answer, [], kb_id,
                      generate_fn=generate_fn)
        return {"answer": answer, "citations": [],
                "conversation_id": str(conv.id), "remaining": remaining}
    try:
        deps = RuntimeDeps(db=db, embed_fn=embed_fn, generate_fn=generate_fn, model=model,
                           temperature=float(chat_cfg.get("temperature", 0.7)),
                           max_tokens=int(chat_cfg.get("max_tokens", 1500)))
        result = run_agent(_base_state(
            ws, agent, user, clean, kb_id, history, spec, instructions,
            definition, get_agent_state(agent), get_conversation_state(conv),
            instance_kb_ids=instance_kb_ids(db, agent)), deps)
        answer = result.get("answer", "")
        citations = result.get("citations", [])
    finally:
        set_definition_context(db, None)
    _persist_turn(db, ws, agent, user, conv, clean, answer, citations, kb_id,
                  generate_fn=generate_fn)
    return {"answer": answer, "citations": citations, "conversation_id": str(conv.id)}


def chat_preview(db: Session, ws: Workspace, agent: Agent, user: User,
                 message: str, kb_id=None, embed_fn=None,
                 generate_fn=None) -> dict:
    """Run one grounded assistant answer without creating a conversation,
    storing a message, updating memory, or adding chat history.

    Used by independent meeting summary/tasks panels: results stay in their
    source UI instead of appearing in the chat thread.
    """
    from app.agents.definitions_service import instance_kb_ids, require_agent_access
    from app.agents.runtime.langgraph_runner import (
        RuntimeDeps,
        node_build_prompt,
        node_load_memory,
        node_retrieve_knowledge,
    )
    from app.agents.safety import sanitize_user_message
    from app.agents.state import get_agent_state, get_effective_instructions

    require_agent_access(agent, user)
    if kb_id is not None:
        from app.knowledge.service import get_kb as _get_kb

        _get_kb(db, ws, kb_id)  # 404 when foreign/missing
    try:
        clean = sanitize_user_message(message)
    except ValueError:
        raise HTTPException(422, "message is empty")
    definition = _load_definition(db, agent)
    spec = _spec_for(agent, definition)
    instructions = get_effective_instructions(definition, agent)
    model, chat_cfg = _model_config(db, agent, definition)
    if generate_fn is None:
        generate_fn = _provider_for(chat_cfg).generate
    set_definition_context(db, definition.id if definition else None)
    try:
        deps = RuntimeDeps(
            db=db,
            embed_fn=embed_fn,
            generate_fn=generate_fn,
            model=model,
            temperature=float(chat_cfg.get("temperature", 0.7)),
            max_tokens=int(chat_cfg.get("max_tokens", 1500)),
        )
        state = _base_state(
            ws, agent, user, clean, kb_id, [], spec, instructions,
            definition, get_agent_state(agent), {},
            instance_kb_ids=instance_kb_ids(db, agent),
        )
        state.update(node_load_memory(state, deps))
        state.update(node_retrieve_knowledge(state, deps))
        state.update(node_build_prompt(state, deps))
        result = run_agent(state, deps)
        return {
            "answer": str(result.get("answer", "")),
            "citations": result.get("citations", []),
        }
    finally:
        set_definition_context(db, None)


async def chat_stream_events(db: Session, ws: Workspace, agent: Agent, user: User,
                             message: str, conversation_id=None, kb_id=None,
                             embed_fn=None, stream_fn=None, generate_fn=None):
    """Async generator of SSE-ready dicts: meta -> token* -> done.

    Retrieval + memory + prompt run first (same nodes as the graph);
    the answer streams token-by-token; persistence happens once,
    after the stream completes.

    Client-tool turns (web_search/maps_search enabled on the definition):
    one extra LLM routing round decides; on a call the generator yields
    meta -> tool_call(s) and RETURNS (nothing persisted yet). The browser
    executes in the user's device and POSTs to /chat/resume, which
    validates, stores leads, and streams the final answer. At most one
    external tool per turn. NOTE: the non-stream chat() path below does
    NOT run client tools (admin panel test uses it) — stream only.
    """
    from app.agents.definitions_service import instance_kb_ids
    from app.agents.runtime.langgraph_runner import (
        node_build_prompt,
        node_load_memory,
        node_retrieve_knowledge,
    )
    from app.agents.state import get_agent_state, get_conversation_state

    clean, conv, history, definition, spec, instructions, model, chat_cfg = \
        _prepare_chat(db, ws, agent, user, message, conversation_id, kb_id)
    from app.usage import context as _uctx
    from app.usage import recorder as _urec

    import time as _st
    _s_trace = _uctx.bind_usage_context(
        workspace_id=ws.id, user_id=user.id, trace_id=_uctx.new_trace_id())
    _s_t0 = _st.perf_counter()

    def _record_stream_turn(ok: bool, error: str = "",
                            answer_chars: int = 0, paused: bool = False) -> None:
        _urec.record_event(
            "agent", workspace_id=ws.id, user_id=user.id, trace_id=_s_trace,
            model=model, latency_ms=round((_st.perf_counter() - _s_t0) * 1000, 1),
            ok=ok, error=error,
            meta={"agent_key": agent.key, "agent_id": str(agent.id),
                  "stream": True, "answer_chars": answer_chars,
                  "paused": paused})
    # Deterministic verbatim tool FIRST (no LLM): stream the exact file
    # text as tokens so the UX matches normal streaming.
    verb = _verbatim_answer(db, ws, agent, definition, kb_id, clean)
    if verb is not None:
        set_definition_context(db, None)
        yield {"type": "meta", "conversation_id": str(conv.id), "citations": []}
        text = verb["answer"]
        for i in range(0, len(text), 800):
            yield {"type": "token", "text": text[i:i + 800]}
        _persist_turn(db, ws, agent, user, conv, clean, text, [], kb_id)
        yield {"type": "done", "conversation_id": str(conv.id),
               "citations": [], "title": getattr(conv, "title", "") or ""}
        return
    # Deterministic disambiguation SECOND (no LLM).
    amb = _ambiguity_answer(db, ws, agent, definition, kb_id, clean)
    if amb is not None:
        set_definition_context(db, None)
        yield {"type": "meta", "conversation_id": str(conv.id), "citations": []}
        for i in range(0, len(amb), 800):
            yield {"type": "token", "text": amb[i:i + 800]}
        _persist_turn(db, ws, agent, user, conv, clean, amb, [], kb_id)
        yield {"type": "done", "conversation_id": str(conv.id),
               "citations": [], "title": getattr(conv, "title", "") or ""}
        return
    # Chunked long-document translation (multi-turn "ادامه بده").
    tr = _translate_turn(db, ws, agent, definition, kb_id, clean, conv,
                         chat_cfg, generate_fn=generate_fn)
    if tr is not None:
        set_definition_context(db, None)
        answer, remaining = tr
        yield {"type": "meta", "conversation_id": str(conv.id), "citations": []}
        for i in range(0, len(answer), 800):
            yield {"type": "token", "text": answer[i:i + 800]}
        _persist_turn(db, ws, agent, user, conv, clean, answer, [], kb_id)
        yield {"type": "done", "conversation_id": str(conv.id),
               "citations": [], "remaining": remaining,
               "title": getattr(conv, "title", "") or ""}
        return
    try:
        deps = RuntimeDeps(db=db, embed_fn=embed_fn, model=model,
                           temperature=float(chat_cfg.get("temperature", 0.7)),
                           max_tokens=int(chat_cfg.get("max_tokens", 1500)))

        state = _base_state(
            ws, agent, user, clean, kb_id, history, spec, instructions,
            definition, get_agent_state(agent), get_conversation_state(conv),
            instance_kb_ids=instance_kb_ids(db, agent))
        state.update(node_load_memory(state, deps))
        state.update(node_retrieve_knowledge(state, deps))

        # Client tools: web may already be answered server-side (stashed);
        # maps (or unserved web) pauses for the browser.
        pause, stashed = _maybe_pause_for_client_tools(
            db, conv, history, definition, spec, model, clean, kb_id)
        for c in stashed:
            (state["maps_chunks"] if c["chunk_id"].startswith("maps:")
             else state["web_chunks"]).append(c)
            state["chunks"].append(c)
        state.update(node_build_prompt(state, deps))

        chunks = state.get("chunks", [])
        citations = [
            {"n": i, "chunk_id": c["chunk_id"], "source": c["source"],
             "page_no": c["page_no"], "start_ms": c["start_ms"], "end_ms": c["end_ms"]}
            for i, c in enumerate(chunks, 1)
        ]
        yield {"type": "meta", "conversation_id": str(conv.id), "citations": citations}

        if pause is not None:
            for call in pause:
                yield call
            _record_stream_turn(True, paused=True)
            return

        if stream_fn is None:
            stream_fn = _provider_for(chat_cfg).stream
        parts: list[str] = []
        try:
            async for delta in stream_fn(state["prompt"], model=model,
                                         temperature=deps.temperature,
                                         max_tokens=deps.max_tokens):
                if delta:
                    parts.append(delta)
                    yield {"type": "token", "text": delta}
        except Exception as exc:
            _record_stream_turn(False,
                                error=f"{type(exc).__name__}: {exc}"[:300])
            yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"[:300]}
            return
        answer = "".join(parts).strip()
    finally:
        set_definition_context(db, None)
    _persist_turn(db, ws, agent, user, conv, clean, answer, citations, kb_id,
                  generate_fn=_provider_for(chat_cfg).generate)
    _record_stream_turn(True, answer_chars=len(answer or ""))
    yield {"type": "done", "conversation_id": str(conv.id),
           "citations": citations, "title": getattr(conv, "title", "") or ""}


def _client_spec_for(name: str) -> dict:
    """Browser executor descriptor for a client tool (provider-owned)."""
    if name == "maps_search":
        from app.ai.places_providers import OverpassClientSpec

        return OverpassClientSpec().client_spec()
    if name == "web_search":
        from app.ai.web_providers import DDGServerProvider

        return DDGServerProvider().client_spec()
    return {}


def _server_web_results(query: str) -> list[dict]:
    """Immediate server-side web search (GapGPT chain). Returns prompt-ready
    chunk dicts (empty when no server provider serves). This is why web
    questions usually need NO browser round at all."""
    from app.ai.web_providers import get_web_chain

    try:
        results, served = get_web_chain().run(query, max_results=6)
    except Exception:
        return []
    out = []
    for i, r in enumerate(results):
        out.append({
            "content": "\n".join(l for l in
                                 [r.title or "", r.uri or "", r.snippet or ""]
                                 if l),
            "chunk_id": f"websrv:{i}",
            "source": r.title or r.uri or "",
            "url": r.uri or "",
            "page_no": None, "start_ms": None, "end_ms": None,
            "score": round(0.95 - i * 0.02, 3),
        })
    return out


def _maybe_pause_for_client_tools(db: Session, conv, history, definition,
                                  spec: dict, model, clean: str, kb_id
                                  ) -> tuple[list | None, list]:
    """Returns (pause_events|None, stashed_chunks).

    web_search is satisfied SERVER-side first (GapGPT chain): on success
    its chunks are stashed straight into this turn (no pause). Only calls
    with no server answer (typically maps_search) pause for the browser.
    At most one browser tool per turn.
    """
    from app.agents.client_tools import (
        CLIENT_TOOL_SPECS,
        MAX_CLIENT_ROUNDS,
        request_client_calls,
    )
    from app.agents.state import get_conversation_state, update_conversation_state

    tools = [t for t in _tools_of(definition, spec) if t in CLIENT_TOOL_SPECS]
    if not tools:
        return None, []
    cstate = get_conversation_state(conv) or {}
    round_no = int(cstate.get("client_round", 0)) + 1
    if round_no > MAX_CLIENT_ROUNDS:
        return None, []
    calls = request_client_calls(clean, history, tools, model)
    if not calls:
        return None, []
    import logging as _logging

    _logging.getLogger(__name__).info(
        "client tools routed: %s",
        [(c["name"], c["arguments"]["query"][:80]) for c in calls])
    stashed: list = []
    pending = []
    for i, c in enumerate(calls):
        q = c["arguments"]["query"]
        if c["name"] == "web_search":
            stashed = _server_web_results(q)
            if stashed:
                continue  # answered server-side: no browser round needed
        pending.append({"call_id": f"c{round_no}-{i}", "name": c["name"],
                        "query": q, "round": round_no})
        break  # at most ONE browser tool per turn
    if not pending:
        return None, stashed
    update_conversation_state(db, conv, {
        "pending_client_calls": pending,
        "client_round": round_no,
        "client_message": clean,
        **({"client_kb_id": str(kb_id)} if kb_id else {}),
    })
    events: list[dict] = [{
        "type": "progress", "conversation_id": str(conv.id),
        "stage": "routing",
        "detail": " + ".join(f"{p['name']}: {p['query'][:100]}" for p in pending),
    }]
    for p in pending:
        events.append({"type": "tool_call", "conversation_id": str(conv.id),
                       "call_id": p["call_id"], "name": p["name"],
                       "arguments": {"query": p["query"]},
                       "client_spec": _client_spec_for(p["name"])})
    return events, stashed


def _client_chunks(name: str, items: list[dict], round_no: int) -> list[dict]:
    """Validated resume results -> prompt/citation chunk dicts."""
    out = []
    if name == "maps_search":
        for i, p in enumerate(items):
            lines = [p.get("name") or ""]
            if p.get("address"):
                lines.append(p["address"])
            if p.get("phone"):
                lines.append(f"تلفن: {p['phone']}")
            if p.get("hours"):
                lines.append(p["hours"])
            if p.get("website"):
                lines.append(p["website"])
            out.append({
                "content": "\n".join(l for l in lines if l),
                "chunk_id": f"maps:r{round_no}:{i}",
                "source": p.get("name") or "",
                "url": p.get("website") or p.get("maps_uri") or "",
                "page_no": None, "start_ms": None, "end_ms": None,
                "score": round(0.95 - i * 0.02, 3),
            })
    elif name == "web_search":
        for i, w in enumerate(items):
            out.append({
                "content": "\n".join(l for l in
                                     [w.get("title") or "", w.get("uri") or "",
                                      w.get("snippet") or ""] if l),
                "chunk_id": f"web:r{round_no}:{i}",
                "source": w.get("title") or w.get("uri") or "",
                "url": w.get("uri") or "",
                "page_no": None, "start_ms": None, "end_ms": None,
                "score": round(0.95 - i * 0.02, 3),
            })
    return out


async def chat_resume_events(db: Session, ws: Workspace, agent: Agent, user: User,
                             conversation_id, results: list,
                             embed_fn=None, stream_fn=None):
    """Continue a paused client-tool turn: validate browser results, store
    leads, run the server-side chain remainder on browser failure, then
    stream the final answer. Always finishes (no second routing round)."""
    from app.agents import leads as _leads
    from app.agents.definitions_service import instance_kb_ids, require_agent_access
    from app.agents.runtime.langgraph_runner import (
        node_build_prompt,
        node_load_memory,
        node_retrieve_knowledge,
    )
    from app.agents.state import (
        get_agent_state,
        get_conversation_state,
        update_conversation_state,
    )
    from app.ai.places_providers import get_places_chain, validate_place_results
    from app.ai.web_providers import get_web_chain, validate_web_results

    require_agent_access(agent, user)
    conv = (
        db.query(Conversation)
        .filter(Conversation.id == coerce_uuid(conversation_id),
                Conversation.workspace_id == ws.id,
                Conversation.user_id == user.id)
        .first()
    )
    if conv is None or str(conv.agent_id) != str(agent.id):
        raise HTTPException(404, "conversation not found")
    cstate = get_conversation_state(conv) or {}
    pending = [p for p in (cstate.get("pending_client_calls") or [])
               if isinstance(p, dict)]
    if not pending:
        raise HTTPException(409, "no pending tool calls for this conversation")
    clean = str(cstate.get("client_message") or "")
    kb_id = cstate.get("client_kb_id")
    round_no = int(cstate.get("client_round", 1))
    if not clean:
        raise HTTPException(409, "paused turn lost its message")

    res_by_id = {r.get("call_id"): r for r in (results or [])
                 if isinstance(r, dict) and r.get("call_id")}
    accumulated = list(cstate.get("client_context") or [])
    leads_saved = 0
    import logging as _logging

    _rlog = _logging.getLogger(__name__)
    progress: list[dict] = []
    for p in pending:
        name, query = p.get("name"), p.get("query") or ""
        r = res_by_id.get(p.get("call_id")) or {}
        browser_ok = bool(r.get("ok")) and not r.get("error")
        _rlog.info(
            "resume %s %s: browser ok=%s served_by=%s n=%d err=%s debug=%s",
            name, (query or "")[:80], browser_ok, r.get("served_by"),
            len(r.get("results") or []), str(r.get("error") or "")[:200],
            str(r.get("debug") or "")[:200])
        if name == "maps_search":
            items = validate_place_results(r.get("results"), 100)
            served = str(r.get("served_by") or "")
            if not items:  # browser failed/empty -> server chain remainder
                extra, served2 = get_places_chain().run(query, max_results=10)
                items = [{"name": e.name, "address": e.address, "phone": e.phone,
                          "hours": "", "website": "", "lat": e.lat, "lng": e.lng,
                          "maps_uri": e.maps_uri, "rating": e.rating,
                          "raw": {}} for e in extra]
                served = served2
            if items:
                saved = _leads.save_leads(db, ws.id, agent.id, conv.id, query,
                                          items, served or "browser")
                leads_saved += len(saved)
                accumulated.extend(_client_chunks(name, items, round_no))
        elif name == "web_search":
            items = validate_web_results(r.get("results"), 10)
            served = str(r.get("served_by") or "")
            if not items:
                extra, served2 = get_web_chain().run(query, max_results=5)
                items = [{"title": e.title, "uri": e.uri, "snippet": e.snippet}
                         for e in extra]
                served = served2
            accumulated.extend(_client_chunks(name, items, round_no))
        progress.append({
            "type": "progress", "conversation_id": str(conv.id),
            "stage": "browser_result",
            "detail": f"{name}: {len(items)} نتیجه از {served or '؟'}"
                      + ("" if browser_ok else
                         f" (مرورگر: {str(r.get('error') or 'ناموفق')[:120]})"),
        })
    update_conversation_state(db, conv, {
        "pending_client_calls": [], "client_context": accumulated})

    definition = _load_definition(db, agent)
    spec = _spec_for(agent, definition)
    from app.agents.state import get_effective_instructions

    instructions = get_effective_instructions(definition, agent)
    if not accumulated:
        # Honesty guard: with zero tool context the model must report the
        # ACTUAL stage failure (visible in the activity feed) instead of a
        # generic "I can't" refusal.
        failures = "; ".join(
            f"{p.get('name')}: {str((res_by_id.get(p.get('call_id')) or {}).get('error') or 'empty')[:100]}"
            for p in pending)
        instructions += (
            "\n\nابزارهای جستجو هیچ نتیجه‌ای برنگرداندند"
            f" ({failures}). این را شفاف و کوتاه به کاربر بگو (کدام ابزار، چه شد)، "
            "هرگز ادعای «محدودیت» کلی یا حریم خصوصی نکن، و یک قدم مشخص بعدی پیشنهاد بده "
            "(مثلاً دقیق‌تر کردن شهر/دسته، یا گفتن «ادامه بده» برای تلاش دوباره).")
    model, chat_cfg = _model_config(db, agent, definition)
    history = _history(db, conv)
    set_definition_context(db, definition.id if definition else None)
    try:
        deps = RuntimeDeps(db=db, embed_fn=embed_fn, model=model,
                           temperature=float(chat_cfg.get("temperature", 0.7)),
                           max_tokens=int(chat_cfg.get("max_tokens", 1500)))
        state = _base_state(
            ws, agent, user, clean, kb_id, history, spec, instructions,
            definition, get_agent_state(agent), get_conversation_state(conv),
            instance_kb_ids=instance_kb_ids(db, agent))
        state.update(node_load_memory(state, deps))
        state.update(node_retrieve_knowledge(state, deps))
        # Inject browser-gathered context (fresh retrieve above never
        # searches externally; client tools live only in these lists).
        for c in accumulated:
            (state["maps_chunks"] if c["chunk_id"].startswith("maps:")
             else state["web_chunks"]).append(c)
            state["chunks"].append(c)
        state.update(node_build_prompt(state, deps))
        chunks = state.get("chunks", [])
        citations = [
            {"n": i, "chunk_id": c["chunk_id"], "source": c["source"],
             "page_no": c["page_no"], "start_ms": c["start_ms"], "end_ms": c["end_ms"]}
            for i, c in enumerate(chunks, 1)
        ]
        for ev in progress:
            yield ev
        if leads_saved:
            yield {"type": "progress", "conversation_id": str(conv.id),
                   "stage": "leads_saved", "detail": f"{leads_saved} لید ذخیره شد"}
        yield {"type": "meta", "conversation_id": str(conv.id), "citations": citations}
        if stream_fn is None:
            stream_fn = _provider_for(chat_cfg).stream
        parts: list[str] = []
        try:
            async for delta in stream_fn(state["prompt"], model=model,
                                         temperature=deps.temperature,
                                         max_tokens=deps.max_tokens):
                if delta:
                    parts.append(delta)
                    yield {"type": "token", "text": delta}
        except Exception as exc:
            yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"[:300]}
            return
        answer = "".join(parts).strip()
    finally:
        set_definition_context(db, None)
    _persist_turn(db, ws, agent, user, conv, clean, answer, citations, kb_id,
                  generate_fn=_provider_for(chat_cfg).generate)
    yield {"type": "done", "conversation_id": str(conv.id),
           "citations": citations, "leads_saved": leads_saved,
           "title": getattr(conv, "title", "") or ""}


def list_conversations(db: Session, ws: Workspace, agent: Agent, user: User):
    from app.agents.definitions_service import require_agent_access

    require_agent_access(agent, user)
    return (
        db.query(Conversation)
        .filter(Conversation.workspace_id == ws.id, Conversation.agent_id == agent.id,
                Conversation.user_id == user.id)
        .order_by(Conversation.pinned.desc(), Conversation.created_at.desc()).all()
    )


def patch_conversation(db: Session, ws: Workspace, agent: Agent, user: User,
                       conversation_id, title=None, pinned=None) -> Conversation:
    """User-editable title + pin. Scoped to own conversation."""
    from app.agents.definitions_service import require_agent_access

    require_agent_access(agent, user)
    conv = (
        db.query(Conversation)
        .filter(Conversation.id == coerce_uuid(conversation_id),
                Conversation.workspace_id == ws.id,
                Conversation.agent_id == agent.id,
                Conversation.user_id == user.id)
        .first()
    )
    if conv is None:
        raise HTTPException(404, "conversation not found")
    if title is not None:
        conv.title = str(title)[:80]
    if pinned is not None:
        conv.pinned = bool(pinned)
    db.commit()
    db.refresh(conv)
    return conv


def get_messages(db: Session, ws: Workspace, user: User, conversation_id) -> list[Message]:
    conv = (
        db.query(Conversation)
        .filter(Conversation.id == coerce_uuid(conversation_id),
                Conversation.workspace_id == ws.id, Conversation.user_id == user.id)
        .first()
    )
    if conv is None:
        raise HTTPException(404, "conversation not found")
    # Locked: the parent agent's ownership gates the messages too.
    agent = (
        db.query(Agent)
        .filter(Agent.id == conv.agent_id, Agent.workspace_id == ws.id)
        .first()
    )
    from app.agents.definitions_service import require_agent_access

    require_agent_access(agent, user)
    return (
        db.query(Message).filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at).all()
    )


def get_agent_service() -> None:
    return None  # kept for backwards-compat; use module functions
