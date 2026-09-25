"""Runtime state abstraction (Phase 1).

Two strictly separated concepts — never two sources for one datum:

- Agent Instance Runtime State (``agents.runtime_state``): instance-level
  data shared across all conversations of this instance
  (current_lesson/current_course/current_plan/progress/preferences).
- Conversation State (``conversations.state``): per-conversation/turn data
  (turns/last_kb_id/kind/drafts).

Memory scoping rule (locked):
- summary scope = f"agent_instance:{agent_id}"
- fallback to legacy f"agent:{key}" ONLY when instance.definition_id IS NULL.
  New (esp. personal) instances never inherit legacy agent:{key} memory.
"""

from sqlalchemy.orm import Session

from app.common.base import coerce_uuid


def get_agent_state(agent) -> dict:
    return dict(getattr(agent, "runtime_state", None) or {})


def update_agent_state(db: Session, agent, patch: dict):
    """Merge-patch agents.runtime_state (instance-level only)."""
    state = get_agent_state(agent)
    state.update(patch or {})
    agent.runtime_state = state
    db.commit()
    db.refresh(agent)
    return state


def get_conversation_state(conv) -> dict:
    return dict(getattr(conv, "state", None) or {})


def update_conversation_state(db: Session, conv, patch: dict):
    """Merge-patch conversations.state (per-conversation only)."""
    state = get_conversation_state(conv)
    state.update(patch or {})
    conv.state = state
    db.commit()
    db.refresh(conv)
    return state


def summary_scope_for(agent) -> str:
    return f"agent_instance:{agent.id}"


def legacy_summary_scope_for(agent) -> str | None:
    """Legacy scope, readable only for pre-Phase-1 instances."""
    if getattr(agent, "definition_id", None) is not None:
        return None
    return f"agent:{agent.key}"


def get_effective_summary(db: Session, ws_id, user_id, agent) -> str:
    """Per-instance summary with locked legacy fallback."""
    from app.memory.postgres_provider import PostgresMemoryProvider

    mem = PostgresMemoryProvider(db)
    primary = mem.get_summary(ws_id, user_id, scope=summary_scope_for(agent))
    if primary:
        return primary
    legacy = legacy_summary_scope_for(agent)
    if legacy:
        return mem.get_summary(ws_id, user_id, scope=legacy)
    return ""


def write_instance_summary(db: Session, ws_id, user_id, agent, summary: str) -> None:
    """Write ONLY to the per-instance scope (never to legacy)."""
    from app.memory.postgres_provider import PostgresMemoryProvider

    PostgresMemoryProvider(db).summarize(
        ws_id, user_id, summary_scope_for(agent), summary
    )


def get_effective_instructions(definition, agent) -> str:
    """Single instruction source (locked):

    Definition.instructions [+ Workspace customization: custom_instructions].
    agents.config['instructions'] is legacy fallback ONLY: read when the
    instance has no custom_instructions AND no definition.
    """
    base = ""
    if definition is not None:
        base = (getattr(definition, "instructions", None) or "").strip()
    custom = (getattr(agent, "custom_instructions", None) or "").strip()
    if custom:
        if base:
            return f"{base}\n\n[Workspace customization]\n{custom}"
        return custom
    if base:
        return base
    # Legacy fallback for pre-Phase-1 rows only.
    if getattr(agent, "definition_id", None) is None:
        return ((agent.config or {}).get("instructions", "") or "").strip()
    return ""
