"""Workspace service: tenant CRUD, membership, role checks.

Every read/write verifies membership app-layer (non-members get 404 so
workspace existence never leaks). `resolve_workspace` also binds the
transaction to the workspace for PostgreSQL RLS (no-op elsewhere).
"""

from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.service import audit, get_current_user
from app.common.base import coerce_uuid
from app.core.database import get_db, set_workspace_context
from app.users.models import User
from app.workspaces.models import MEMBER_ROLES, WORKSPACE_TYPES, Workspace, WorkspaceMember


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "workspace not found")


def _member_role(db: Session, workspace_id, user_id) -> str | None:
    row = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == coerce_uuid(workspace_id),
            WorkspaceMember.user_id == coerce_uuid(user_id),
        )
        .first()
    )
    return row.role if row else None


def create_workspace(db: Session, user: User, name: str, type: str) -> Workspace:
    if type not in WORKSPACE_TYPES:
        raise HTTPException(422, f"type must be one of {WORKSPACE_TYPES}")
    ws = Workspace(name=name.strip(), type=type, owner_user_id=user.id)
    db.add(ws)
    db.flush()
    # Bind the rest of this transaction to the new workspace so RLS
    # WITH CHECK passes for the owner membership row below.
    set_workspace_context(db, ws.id)
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    db.commit()
    # New transaction after commit: re-bind before refresh (RLS-gated SELECT).
    set_workspace_context(db, ws.id)
    db.refresh(ws)
    audit(db, "workspaces.create", actor_user_id=user.id,
          workspace_id=ws.id, entity="workspace", entity_id=ws.id)
    return ws


def list_workspaces(db: Session, user: User) -> list[Workspace]:
    if user.is_admin:
        return db.query(Workspace).order_by(Workspace.created_at.desc()).all()
    return (
        db.query(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .filter(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.created_at.desc())
        .all()
    )


def resolve_workspace(
    workspace_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Workspace:
    """Dependency: fetch + membership-gate + RLS-bind a workspace.

    Use this in every `{workspace_id}`-scoped endpoint.
    """
    # Provisionally bind the claimed workspace FIRST: RLS hides the rows
    # the membership check itself needs to read. The gate below still
    # rejects non-members (404), so no data can leak through this.
    set_workspace_context(db, coerce_uuid(workspace_id))
    ws = db.query(Workspace).filter(Workspace.id == coerce_uuid(workspace_id)).first()
    if ws is None:
        raise _not_found()
    if not user.is_admin and _member_role(db, ws.id, user.id) is None:
        raise _not_found()
    return ws


def require_workspace_role(db: Session, ws: Workspace, user: User, roles: tuple[str, ...]) -> str:
    if user.is_admin:
        return "admin"
    role = _member_role(db, ws.id, user.id)
    if role not in roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient workspace role")
    return role


def delete_workspace(db: Session, ws: Workspace, actor: User) -> None:
    """Owner (or global admin) only. Tenant rows cascade via FK deletes."""
    require_workspace_role(db, ws, actor, ("owner",))
    ws_id = str(ws.id)
    db.delete(ws)
    db.commit()
    audit(db, "workspaces.delete", actor_user_id=actor.id,
          entity="workspace", entity_id=ws_id)


def add_member(db: Session, ws: Workspace, actor: User, user_id, role: str) -> WorkspaceMember:
    require_workspace_role(db, ws, actor, ("owner", "admin"))
    if role not in MEMBER_ROLES:
        raise HTTPException(422, f"role must be one of {MEMBER_ROLES}")
    target = db.query(User).filter(User.id == coerce_uuid(user_id)).first()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    if _member_role(db, ws.id, target.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "already a member")
    member = WorkspaceMember(workspace_id=ws.id, user_id=target.id, role=role)
    db.add(member)
    db.commit()
    audit(db, "workspaces.member_add", actor_user_id=actor.id, workspace_id=ws.id,
          entity="user", entity_id=target.id, meta={"role": role})
    return member


def list_members(db: Session, ws: Workspace) -> list[dict]:
    rows = (
        db.query(WorkspaceMember, User.email)
        .join(User, User.id == WorkspaceMember.user_id)
        .filter(WorkspaceMember.workspace_id == ws.id)
        .all()
    )
    return [{"user_id": str(m.user_id), "email": email, "role": m.role} for m, email in rows]
