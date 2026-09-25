from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.service import get_current_user
from app.common.base import coerce_uuid
from app.core.database import get_db
from app.memory import schemas
from app.memory.postgres_provider import get_memory_provider
from app.users.models import User
from app.workspaces.models import Workspace
from app.workspaces.service import require_workspace_role, resolve_workspace

router = APIRouter(tags=["memory"])


def _target_user(ws, db, user, user_id: str | None) -> User:
    """Default: self. Reading another user's memory needs owner/admin role."""
    if not user_id or str(user.id) == str(user_id):
        return user
    require_workspace_role(db, ws, user, ("owner", "admin"))
    target = db.query(User).filter(User.id == coerce_uuid(user_id)).first()
    if target is None:
        from fastapi import HTTPException

        raise HTTPException(404, "user not found")
    return target


@router.post("/workspaces/{workspace_id}/memory/facts",
             response_model=schemas.MemoryFactOut, status_code=201)
def post_fact(
    body: schemas.MemoryUpsert,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return get_memory_provider(db).remember(ws.id, user.id, body.key, body.value, body.category)


@router.get("/workspaces/{workspace_id}/memory/facts",
            response_model=list[schemas.MemoryFactOut])
def get_facts(
    user_id: str | None = None,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    target = _target_user(ws, db, user, user_id)
    return get_memory_provider(db).facts_for_user(ws.id, target.id)


@router.post("/workspaces/{workspace_id}/memory/search",
             response_model=list[schemas.MemoryHit])
def post_search(
    body: schemas.MemorySearch,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return get_memory_provider(db).recall(ws.id, user.id, body.query, body.top_k)


@router.post("/workspaces/{workspace_id}/memory/summaries",
             response_model=schemas.SummaryOut, status_code=201)
def post_summary(
    body: schemas.SummaryUpsert,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return get_memory_provider(db).summarize(ws.id, user.id, body.scope, body.summary)


@router.get("/workspaces/{workspace_id}/memory/summaries",
            response_model=list[schemas.SummaryOut])
def get_summaries(
    user_id: str | None = None,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.memory.models import ConversationSummary

    target = _target_user(ws, db, user, user_id)
    return (
        db.query(ConversationSummary)
        .filter(
            ConversationSummary.workspace_id == ws.id,
            ConversationSummary.user_id == target.id,
        )
        .all()
    )
