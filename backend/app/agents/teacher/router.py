from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.agents.service import get_agent
from app.agents.teacher import schemas
from app.agents.teacher.models import LessonPlan
from app.agents.teacher.service import (
    build_lesson_plan,
    get_generate_structured_fn,
    get_lesson_plan,
    get_lesson_plan_by_conversation,
    list_lesson_plans,
)
from app.auth.service import get_current_user
from app.core.database import get_db
from app.knowledge.retrieval.hybrid import get_embed_fn
from app.users.models import User
from app.workspaces.models import Workspace
from app.workspaces.service import resolve_workspace

router = APIRouter(tags=["agents"])


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/lesson-plans",
             response_model=schemas.LessonPlanResponse, status_code=201)
def post_lesson_plan(
    agent_id: str,
    body: schemas.LessonPlanIn,
    request: Request,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    embed_fn=Depends(get_embed_fn),
    generate_structured_fn=Depends(get_generate_structured_fn),
):
    from app.common.rate_limit import check

    check("lesson-plan", str(user.id), calls=20, period_seconds=60)
    agent = get_agent(db, ws, agent_id, user)
    return build_lesson_plan(db, ws, agent, user, body,
                             embed_fn=embed_fn,
                             generate_structured_fn=generate_structured_fn)


def _to_detail(row: LessonPlan) -> schemas.LessonPlanDetailOut:
    return schemas.LessonPlanDetailOut(
        id=row.id, kb_id=row.kb_id, chapter=row.chapter, grade=row.grade,
        duration_minutes=row.duration_minutes, teaching_style=row.teaching_style,
        instructions=row.instructions, plan=row.plan,
        conversation_id=row.conversation_id, created_at=row.created_at,
    )


@router.get("/workspaces/{workspace_id}/agents/{agent_id}/lesson-plans",
            response_model=list[schemas.LessonPlanListOut])
def get_lesson_plans(
    agent_id: str,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    agent = get_agent(db, ws, agent_id, user)
    return list_lesson_plans(db, ws, agent, user)


@router.get("/workspaces/{workspace_id}/agents/{agent_id}/lesson-plans/{plan_id}",
            response_model=schemas.LessonPlanDetailOut)
def get_lesson_plan_one(
    agent_id: str,
    plan_id: str,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    agent = get_agent(db, ws, agent_id, user)
    return _to_detail(get_lesson_plan(db, ws, agent, user, plan_id))


@router.get("/workspaces/{workspace_id}/lesson-plans/by-conversation/{conversation_id}",
            response_model=schemas.LessonPlanDetailOut)
def get_lesson_plan_by_conv(
    conversation_id: str,
    ws: Workspace = Depends(resolve_workspace),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _to_detail(get_lesson_plan_by_conversation(db, ws, user, conversation_id))
