from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.service import get_current_user
from app.core.database import get_db
from app.users.models import User
from app.workspaces import schemas
from app.workspaces.models import Workspace
from app.workspaces.service import (
    add_member,
    create_workspace,
    delete_workspace,
    list_members,
    list_workspaces,
    resolve_workspace,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("", response_model=schemas.WorkspaceOut, status_code=201)
def create_ws(
    body: schemas.WorkspaceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return create_workspace(db, user, body.name, body.type)


@router.get("", response_model=list[schemas.WorkspaceOut])
def list_ws(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return list_workspaces(db, user)


@router.get("/{workspace_id}", response_model=schemas.WorkspaceOut)
def get_ws(ws: Workspace = Depends(resolve_workspace)):
    return ws


@router.delete("/{workspace_id}")
def delete_ws(
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    delete_workspace(db, ws, user)
    return {"ok": True}


@router.get("/{workspace_id}/members")
def get_members(ws: Workspace = Depends(resolve_workspace), db: Session = Depends(get_db)):
    return list_members(db, ws)


@router.post("/{workspace_id}/members", status_code=201)
def post_member(
    body: schemas.MemberAdd,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    member = add_member(db, ws, user, body.user_id, body.role)
    return {"user_id": str(member.user_id), "role": member.role}
