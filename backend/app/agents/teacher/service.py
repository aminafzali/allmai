"""Teacher lesson-planner service: retrieval + structured generation.

Usable only on agents whose key is `teacher_lesson_planner` (422 otherwise),
so the generic engine never hard-codes product behaviour.
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agents.teacher.prompts import build_lesson_prompt
from app.agents.teacher.models import LessonPlan
from app.agents.teacher.schemas import (
    LessonPlanIn,
    LessonPlanOut,
    LessonPlanResponse,
    SourceRefOut,
)
from app.auth.service import audit
from app.common.base import coerce_uuid
from app.conversations.models import Conversation, Message
from app.knowledge.retrieval.hybrid import hybrid_search
from app.users.models import User
from app.workspaces.models import Workspace

AGENT_KEY = "teacher_lesson_planner"


def _provider_for(chat_cfg: dict):
    from app.ai.factory import get_provider

    try:
        return get_provider((chat_cfg or {}).get("provider") or "openai_compat")
    except ValueError:
        from app.ai.factory import get_provider as _gp

        return _gp("openai_compat")


def get_generate_structured_fn(db=None):
    from app.ai.factory import get_chat_provider

    return get_chat_provider(db).generate_structured


def build_lesson_plan(
    db: Session,
    ws: Workspace,
    agent,
    user: User,
    body: LessonPlanIn,
    embed_fn=None,
    generate_structured_fn=None,
) -> LessonPlanResponse:
    _require_agent(agent)
    from app.agents.definitions_service import require_agent_access

    require_agent_access(agent, user)
    from app.ai.settings import resolve_agent_chat

    definition = None
    try:
        from app.agents.definitions_service import get_definition

        did = getattr(agent, "definition_id", None)
        if did:
            definition = get_definition(db, did)
    except Exception:
        definition = None
    cfg = resolve_agent_chat(db, AGENT_KEY, definition)
    model = cfg.get("model")
    if generate_structured_fn is None:
        generate_structured_fn = _provider_for(cfg).generate_structured

    query = f"{body.chapter} {body.instructions}".strip()
    hits = hybrid_search(db, ws.id, query, body.kb_id, top_k=8, embed_fn=embed_fn)

    parts = []
    for i, h in enumerate(hits, 1):
        seg = h.segment
        loc = f"p.{seg.page_no}" if seg and seg.page_no else "audio"
        parts.append(f"[{i}] ({h.source.filename if h.source else '?'} {loc})\n{h.chunk.content}")
    context = "\n\n".join(parts)

    from app.agents.teacher.schemas import LessonPlanBody
    from app.agents import prompt_templates as PT

    extra = (f"\nTeacher's extra instructions: {body.instructions}"
             if (body.instructions or "").strip() else "")
    prompt = PT.render(PT.get_template(definition, "lesson_plan"), {
        "chapter": body.chapter, "grade": body.grade,
        "duration_minutes": body.duration_minutes,
        "teaching_style": body.teaching_style,
        "instructions_block": extra, "context": context or "(empty)",
    })
    if prompt is None:
        prompt = build_lesson_prompt(body.chapter, body.grade, body.duration_minutes,
                                     body.teaching_style, body.instructions, context)
    generated: LessonPlanBody = generate_structured_fn(prompt, LessonPlanBody, model=model)

    refs = [
        SourceRefOut(
            n=i, chunk_id=str(h.chunk.id), source=h.source.filename if h.source else "",
            page_no=h.segment.page_no if h.segment else None,
            start_ms=h.segment.start_ms if h.segment else None,
            end_ms=h.segment.end_ms if h.segment else None,
        )
        for i, h in enumerate(hits, 1)
    ]
    plan = LessonPlanOut(**generated.model_dump(), references=refs)

    conv = Conversation(workspace_id=ws.id, agent_id=agent.id, user_id=user.id,
                        state={"kind": "lesson_plan", "chapter": body.chapter,
                               "grade": body.grade, "kb_id": str(body.kb_id)})
    db.add(conv)
    db.flush()
    db.add(Message(workspace_id=ws.id, conversation_id=conv.id, role="user",
                   content=f"Lesson plan: {body.chapter} (grade {body.grade}, "
                           f"{body.duration_minutes} min, {body.teaching_style})"))
    db.add(Message(workspace_id=ws.id, conversation_id=conv.id, role="assistant",
                   content=plan.model_dump_json(ensure_ascii=False),
                   citations={"citations": [r.model_dump() for r in refs]}))
    db.flush()
    row = LessonPlan(
        workspace_id=ws.id, agent_id=agent.id, conversation_id=conv.id,
        user_id=user.id, kb_id=coerce_uuid(body.kb_id),
        chapter=body.chapter, grade=body.grade,
        duration_minutes=body.duration_minutes,
        teaching_style=body.teaching_style, instructions=body.instructions,
        plan=plan.model_dump(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    audit(db, "agents.lesson_plan", actor_user_id=user.id, workspace_id=ws.id,
          entity="agent", entity_id=agent.id,
          meta={"conversation_id": str(conv.id), "chapter": body.chapter,
                "lesson_plan_id": str(row.id)})
    return LessonPlanResponse(plan=plan, citations=refs, conversation_id=str(conv.id),
                              lesson_plan_id=str(row.id))


def list_lesson_plans(db: Session, ws: Workspace, agent, user) -> list[LessonPlan]:
    _require_agent(agent)
    return (
        db.query(LessonPlan)
        .filter(LessonPlan.workspace_id == ws.id, LessonPlan.agent_id == agent.id,
                LessonPlan.user_id == user.id)
        .order_by(LessonPlan.created_at.desc())
        .all()
    )


def get_lesson_plan(db: Session, ws: Workspace, agent, user, plan_id) -> LessonPlan:
    _require_agent(agent)
    row = (
        db.query(LessonPlan)
        .filter(LessonPlan.id == coerce_uuid(plan_id),
                LessonPlan.workspace_id == ws.id,
                LessonPlan.agent_id == agent.id,
                LessonPlan.user_id == user.id)
        .first()
    )
    if row is None:
        raise HTTPException(404, "lesson plan not found")
    return row


def get_lesson_plan_by_conversation(db: Session, ws: Workspace, user, conversation_id) -> LessonPlan:
    """User-scoped lookup for the detail view (any of the user's agents)."""
    row = (
        db.query(LessonPlan)
        .filter(LessonPlan.conversation_id == coerce_uuid(conversation_id),
                LessonPlan.workspace_id == ws.id,
                LessonPlan.user_id == user.id)
        .first()
    )
    if row is None:
        raise HTTPException(404, "lesson plan not found")
    return row


def _require_agent(agent) -> None:
    if agent.key != AGENT_KEY:
        raise HTTPException(422, f"agent {agent.key!r} is not a lesson planner")
