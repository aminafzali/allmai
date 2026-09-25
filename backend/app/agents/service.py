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
    """Provider instance honoring the resolved agent provider."""
    from app.ai.factory import get_provider

    try:
        return get_provider((chat_cfg or {}).get("provider") or "openai_compat")
    except ValueError:
        from app.ai.factory import get_provider as _gp

        return _gp("openai_compat")


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


def _persist_turn(db: Session, ws: Workspace, agent: Agent, user: User,
                  conv: Conversation, clean: str, answer: str,
                  citations: list, kb_id) -> None:
    from app.agents.state import update_conversation_state, write_instance_summary
    from app.memory.postgres_provider import PostgresMemoryProvider  # noqa: F401

    db.add(Message(workspace_id=ws.id, conversation_id=conv.id, role="user", content=clean))
    db.add(Message(workspace_id=ws.id, conversation_id=conv.id, role="assistant",
                   content=answer, citations={"citations": citations}))
    db.commit()
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

    clean, conv, history, definition, spec, instructions, model, chat_cfg = \
        _prepare_chat(db, ws, agent, user, message, conversation_id, kb_id)
    if generate_fn is None:
        generate_fn = _provider_for(chat_cfg).generate
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
    _persist_turn(db, ws, agent, user, conv, clean, answer, citations, kb_id)
    return {"answer": answer, "citations": citations, "conversation_id": str(conv.id)}


async def chat_stream_events(db: Session, ws: Workspace, agent: Agent, user: User,
                             message: str, conversation_id=None, kb_id=None,
                             embed_fn=None, stream_fn=None):
    """Async generator of SSE-ready dicts: meta -> token* -> done.

    Retrieval + memory + prompt run first (same nodes as the graph);
    the answer streams token-by-token; persistence happens once,
    after the stream completes.
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
        state.update(node_build_prompt(state, deps))

        chunks = state.get("chunks", [])
        citations = [
            {"n": i, "chunk_id": c["chunk_id"], "source": c["source"],
             "page_no": c["page_no"], "start_ms": c["start_ms"], "end_ms": c["end_ms"]}
            for i, c in enumerate(chunks, 1)
        ]
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
    _persist_turn(db, ws, agent, user, conv, clean, answer, citations, kb_id)
    yield {"type": "done", "conversation_id": str(conv.id), "citations": citations}


def list_conversations(db: Session, ws: Workspace, agent: Agent, user: User):
    from app.agents.definitions_service import require_agent_access

    require_agent_access(agent, user)
    return (
        db.query(Conversation)
        .filter(Conversation.workspace_id == ws.id, Conversation.agent_id == agent.id,
                Conversation.user_id == user.id)
        .order_by(Conversation.created_at.desc()).all()
    )


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
