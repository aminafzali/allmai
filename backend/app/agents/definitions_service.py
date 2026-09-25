"""Agent Definition service (Phase 1).

- Definitions are GLOBAL (no workspace, no RLS). Authorization is enforced
  at the router via require_admin; this module does plain persistence.
- agent_definition_knowledge is the CANONICAL and ONLY Definition<->KB
  link. Invariant (locked): only KBs with scope='global' AND
  workspace_id IS NULL may be assigned. Workspace KBs -> ValueError
  (router maps to 422). No DB trigger by design: every write funnels
  through assign_knowledge() below (single admin-gated choke point).
- Instance visibility rule (locked, shared with agents/service.py):
  owner_user_id IS NULL (shared) OR owner == current user OR admin.
  Violations raise HTTPException(404) — never leak existence.
"""

import re
import uuid

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.definitions_models import (
    AgentDefinition,
    AgentDefinitionKnowledge,
    AgentKnowledgeAssignment,
)
from app.common.base import coerce_uuid
from app.knowledge.models import KnowledgeBase

_KEY_RE = re.compile(r"^[a-z0-9_]{3,80}$")
_DEF_TYPES = ("assistant", "agent")


def _not_found(what: str = "agent definition not found") -> HTTPException:
    return HTTPException(404, what)


def require_agent_access(agent, user) -> None:
    """Locked visibility rule. Raises 404 for inaccessible personal agents."""
    if agent is None:
        raise _not_found("agent not found")
    owner = getattr(agent, "owner_user_id", None)
    if owner is None:
        return
    if user is not None and (
        str(owner) == str(user.id) or bool(getattr(user, "is_admin", False))
    ):
        return
    raise HTTPException(404, "agent not found")


def visible_agents_query(db: Session, ws_id, user):
    """Shared instances + own personal instances (+all for admins)."""
    from app.agents.models import Agent

    q = db.query(Agent).filter(Agent.workspace_id == coerce_uuid(ws_id))
    if user is not None and bool(getattr(user, "is_admin", False)):
        return q
    return q.filter(
        (Agent.owner_user_id.is_(None))
        | (Agent.owner_user_id == coerce_uuid(user.id))
    )


def validate_owner_membership(db: Session, ws, owner_id) -> None:
    """owner_user_id must be a member of the same workspace (locked)."""
    from app.workspaces.models import WorkspaceMember

    row = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == coerce_uuid(ws.id),
            WorkspaceMember.user_id == coerce_uuid(owner_id),
        )
        .first()
    )
    if row is None:
        raise HTTPException(422, "owner must be a member of the same workspace")


# ---------------- definitions CRUD ----------------

def create_definition(db: Session, key: str, title: str = "", type: str = "agent",
                      **fields) -> AgentDefinition:
    if not _KEY_RE.match(key or ""):
        raise HTTPException(422, "key must match [a-z0-9_]{3,80}")
    if type not in _DEF_TYPES:
        raise HTTPException(422, f"type must be one of {_DEF_TYPES}")
    cols = ("instructions", "behavior_rules", "methodology", "capabilities",
            "tools", "workflow", "model_defaults", "safety_rules",
            "output_format", "description", "is_active")
    row = AgentDefinition(key=key, title=(title or key)[:200], type=type)
    for c in cols:
        if c in fields and fields[c] is not None:
            setattr(row, c, fields[c])
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "definition key already exists")
    db.refresh(row)
    return row


def get_definition(db: Session, definition_id) -> AgentDefinition:
    row = (
        db.query(AgentDefinition)
        .filter(AgentDefinition.id == coerce_uuid(definition_id))
        .first()
    )
    if row is None:
        raise _not_found()
    return row


def get_definition_by_key(db: Session, key: str) -> AgentDefinition | None:
    return db.query(AgentDefinition).filter(AgentDefinition.key == key).first()


def list_definitions(db: Session, active_only: bool = False):
    q = db.query(AgentDefinition).order_by(AgentDefinition.created_at.desc())
    if active_only:
        q = q.filter(AgentDefinition.is_active.is_(True))
    return q.all()


def update_definition(db: Session, definition_id, patch: dict) -> AgentDefinition:
    row = get_definition(db, definition_id)
    allowed = ("title", "description", "type", "instructions", "behavior_rules",
               "methodology", "capabilities", "tools", "workflow",
               "model_defaults", "safety_rules", "output_format", "is_active")
    for k, v in (patch or {}).items():
        if k in allowed and v is not None:
            if k == "type" and v not in _DEF_TYPES:
                raise HTTPException(422, f"type must be one of {_DEF_TYPES}")
            setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


def delete_definition(db: Session, definition_id) -> None:
    from app.agents.models import Agent

    row = get_definition(db, definition_id)
    dependents = (
        db.query(Agent)
        .filter(Agent.definition_id == coerce_uuid(definition_id))
        .count()
    )
    if dependents:
        # Locked (audit result A): never orphan instances into fake legacy.
        raise HTTPException(
            409, f"definition has {dependents} dependent agent instance(s); "
                 f"resolve the instances before deleting the definition")
    db.delete(row)
    db.commit()


# ---------------- canonical assignment ----------------

def assigned_global_kb_ids(db: Session, definition_id) -> list[uuid.UUID]:
    rows = (
        db.query(AgentDefinitionKnowledge)
        .filter(AgentDefinitionKnowledge.definition_id == coerce_uuid(definition_id))
        .all()
    )
    return [r.kb_id for r in rows]


def _require_global_kb(db: Session, kb_id) -> KnowledgeBase:
    kb = (
        db.query(KnowledgeBase)
        .filter(KnowledgeBase.id == coerce_uuid(kb_id))
        .first()
    )
    if kb is None:
        raise HTTPException(404, "knowledge base not found")
    if not (
        getattr(kb, "scope", "workspace") == "global" and kb.workspace_id is None
    ):
        # Locked invariant: workspace KBs can never ride a Definition link.
        raise HTTPException(
            422, "only global knowledge bases (scope='global') "
                 "can be assigned to a definition")
    return kb


def assign_knowledge(db: Session, definition_id, kb_ids: list) -> list[uuid.UUID]:
    """Replace the global-KB set of a definition (admin-gated at router)."""
    get_definition(db, definition_id)
    wanted = []
    for kb_id in (kb_ids or []):
        kb = _require_global_kb(db, kb_id)
        wanted.append(kb.id)
    db.query(AgentDefinitionKnowledge).filter(
        AgentDefinitionKnowledge.definition_id == coerce_uuid(definition_id)
    ).delete()
    for kid in dict.fromkeys(wanted):  # de-dup, preserve order
        db.add(AgentDefinitionKnowledge(
            definition_id=coerce_uuid(definition_id), kb_id=kid))
    db.commit()
    return assigned_global_kb_ids(db, definition_id)


# ---------------- instance <-> workspace KB ----------------

def instance_kb_ids(db: Session, agent) -> list[uuid.UUID]:
    rows = (
        db.query(AgentKnowledgeAssignment)
        .filter(AgentKnowledgeAssignment.agent_id == agent.id)
        .all()
    )
    return [r.kb_id for r in rows]


def set_instance_knowledge(db: Session, ws, agent, kb_ids: list) -> list[uuid.UUID]:
    """Instance manages ONLY its own workspace KBs (locked UX rule)."""
    wanted = []
    for kb_id in (kb_ids or []):
        kb = (
            db.query(KnowledgeBase)
            .filter(KnowledgeBase.id == coerce_uuid(kb_id))
            .first()
        )
        if kb is None or kb.workspace_id is None:
            raise HTTPException(404, "knowledge base not found")
        if str(kb.workspace_id) != str(ws.id):
            raise HTTPException(404, "knowledge base not found")
        if getattr(kb, "scope", "workspace") != "workspace":
            raise HTTPException(422, "global knowledge is inherited "
                                    "read-only from the definition")
        wanted.append(kb.id)
    db.query(AgentKnowledgeAssignment).filter(
        AgentKnowledgeAssignment.agent_id == agent.id
    ).delete()
    for kid in dict.fromkeys(wanted):
        db.add(AgentKnowledgeAssignment(agent_id=agent.id, kb_id=kid,
                                        workspace_id=ws.id))
    db.commit()
    return instance_kb_ids(db, agent)


def knowledge_view(db: Session, agent) -> dict:
    """Inherited global (read-only) + own workspace KBs for the Instance UX."""
    from app.agents.models import Agent as _A  # noqa: F401 (registry)

    definition_id = getattr(agent, "definition_id", None)
    glob = assigned_global_kb_ids(db, definition_id) if definition_id else []
    own = instance_kb_ids(db, agent)
    return {"global": [str(k) for k in glob], "workspace": [str(k) for k in own]}
