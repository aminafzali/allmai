from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.agents.service import get_agent
from app.agents.sse import sse_response
from app.agents.student import schemas
from app.agents.student.service import (
    coach_chat,
    coach_chat_stream_events,
    generate_study_plan,
    read_profile,
    save_profile,
    update_progress,
)
from app.agents.teacher.service import get_generate_structured_fn
from app.auth.service import get_current_user
from app.core.database import get_db
from app.knowledge.retrieval.hybrid import get_embed_fn, get_generate_fn, get_stream_fn
from app.users.models import User
from app.workspaces.models import Workspace
from app.workspaces.service import resolve_workspace

router = APIRouter(tags=["agents"])


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/profile")
def post_profile(
    body: schemas.ProfileUpdate,
    agent_id: str,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return {"saved": save_profile(db, ws, get_agent(db, ws, agent_id, user), user, body.facts)}


@router.get("/workspaces/{workspace_id}/agents/{agent_id}/profile")
def get_profile(
    agent_id: str,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return read_profile(db, ws, get_agent(db, ws, agent_id, user), user)


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/study-plans", status_code=201)
def post_study_plan(
    body: schemas.StudyPlanIn,
    agent_id: str,
    request: Request,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    embed_fn=Depends(get_embed_fn),
    generate_structured_fn=Depends(get_generate_structured_fn),
):
    from app.common.rate_limit import check

    check("study-plan", str(user.id), calls=20, period_seconds=60)
    agent = get_agent(db, ws, agent_id, user)
    plan, conv_id = generate_study_plan(db, ws, agent, user, body,
                                        embed_fn=embed_fn,
                                        generate_structured_fn=generate_structured_fn)
    return {"plan": plan, "conversation_id": conv_id}


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/coach/chat",
             response_model=schemas.CoachChatOut)
def post_coach_chat(
    body: schemas.CoachChatIn,
    agent_id: str,
    request: Request,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    embed_fn=Depends(get_embed_fn),
    generate_fn=Depends(get_generate_fn),
):
    from app.common.rate_limit import check

    check("chat", str(user.id), calls=60, period_seconds=60)
    agent = get_agent(db, ws, agent_id, user)
    return coach_chat(db, ws, agent, user, body.message, body.conversation_id,
                      body.kb_id, embed_fn=embed_fn, generate_fn=generate_fn)


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/coach/chat/stream")
def post_coach_chat_stream(
    body: schemas.CoachChatIn,
    agent_id: str,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    embed_fn=Depends(get_embed_fn),
    stream_fn=Depends(get_stream_fn),
):
    from fastapi import HTTPException

    if not (body.message or "").strip():
        raise HTTPException(422, "message is empty")
    agent = get_agent(db, ws, agent_id, user)
    return sse_response(coach_chat_stream_events(db, ws, agent, user, body.message,
                                                 body.conversation_id, body.kb_id,
                                                 embed_fn=embed_fn, stream_fn=stream_fn))


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/progress",
             response_model=schemas.ProgressOut)
def post_progress(
    body: schemas.ProgressUpdate,
    agent_id: str,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    agent = get_agent(db, ws, agent_id, user)
    return update_progress(db, ws, agent, user, body)
