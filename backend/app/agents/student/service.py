"""Student coach service: profile + memory + goals + stateful plan tracking.

Only runs on agents with key `student_academic_coach`.
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agents.student.schemas import (
    CoachChatOut,
    ProgressOut,
    ProgressUpdate,
    StudyPlanIn,
    StudyPlanOut,
)
from app.ai.settings import get_setting
from app.auth.service import audit
from app.conversations.models import Conversation, Message
from app.common.base import coerce_uuid
from app.knowledge.retrieval.hybrid import hybrid_search
from app.memory.postgres_provider import PostgresMemoryProvider
from app.users.models import User
from app.workspaces.models import Workspace

AGENT_KEY = "student_academic_coach"
PROFILE_PREFIX = "profile."


def _require_key(agent) -> None:
    if agent.key != AGENT_KEY:
        raise HTTPException(422, f"agent {agent.key!r} is not an academic coach")


def _memory(db: Session, ws: Workspace, user: User) -> PostgresMemoryProvider:
    return PostgresMemoryProvider(db)


def save_profile(db: Session, ws: Workspace, agent, user: User, facts: dict) -> dict:
    _require_key(agent)
    mem = _memory(db, ws, user)
    saved = {}
    for k, v in list(facts.items())[:20]:
        key = f"{PROFILE_PREFIX}{(k or '').strip()}"[:200]
        if not key.strip(".") or not (v or "").strip():
            continue
        mem.remember(ws.id, user.id, key, str(v)[:2000], category="profile")
        saved[key] = str(v)[:2000]
    audit(db, "agents.profile_save", actor_user_id=user.id, workspace_id=ws.id,
          entity="agent", entity_id=agent.id, meta={"keys": sorted(saved)})
    return saved


def read_profile(db: Session, ws: Workspace, agent, user: User) -> dict:
    _require_key(agent)
    facts = _memory(db, ws, user).facts_for_user(ws.id, user.id)
    profile = {f.key[len(PROFILE_PREFIX):]: f.value
               for f in facts if f.key.startswith(PROFILE_PREFIX)}
    goals = [f.value for f in facts if f.category == "goal"]
    return {"profile": profile, "goals": goals}


def _conversation(db: Session, ws: Workspace, agent, user: User, conversation_id) -> Conversation:
    from app.agents.definitions_service import require_agent_access

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
    conv = Conversation(workspace_id=ws.id, agent_id=agent.id, user_id=user.id,
                        state={"kind": "coach"})
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def _history(db: Session, conv: Conversation, limit: int = 12) -> list[dict]:
    rows = (
        db.query(Message).filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc()).limit(limit).all()
    )
    return [{"role": m.role, "content": m.content} for m in reversed(rows)]


def _model(db: Session) -> tuple[str | None, dict]:
    cfg = get_setting(db, f"agent.{AGENT_KEY}")
    chat = get_setting(db, "chat.default")
    return cfg.get("model") or chat.get("model"), chat


def generate_study_plan(db: Session, ws: Workspace, agent, user: User, body: StudyPlanIn,
                        embed_fn=None, generate_structured_fn=None) -> tuple[StudyPlanOut, str]:
    from app.agents.student.schemas import StudyPlanBody

    _require_key(agent)
    if generate_structured_fn is None:
        from app.agents.teacher.service import get_generate_structured_fn

        generate_structured_fn = get_generate_structured_fn()
    prof = read_profile(db, ws, agent, user)
    model, chat = _model(db)

    context = ""
    if body.kb_id is not None:
        hits = hybrid_search(db, ws.id, " ".join(body.goals), body.kb_id, top_k=6, embed_fn=embed_fn)
        context = "\n".join(f"- {h.chunk.content[:500]}" for h in hits)

    prompt = (
        "You are an academic coach. Design a realistic weekly study plan in Persian.\n"
        f"Student profile: {prof['profile'] or '(unknown)'}\n"
        f"Goals: {'; '.join(body.goals)}\nAvailable: {body.weekly_hours} hours/week.\n"
        f"Reference material:\n{context or '(none)'}\n"
        "Return weeks (week number, focus, tasks with title/subject/minutes, "
        "milestones) plus short advice. Keep total minutes near the budget."
    )
    generated: StudyPlanBody = generate_structured_fn(prompt, StudyPlanBody, model=model)

    weeks = []
    for i, w in enumerate(generated.weeks[:12], 1):
        tasks = []
        for j, t in enumerate(w.tasks[:10], 1):
            tasks.append(t.model_copy(update={"id": f"w{i}t{j}", "subject": t.subject or "",
                                              "minutes": max(0, t.minutes)}))
        weeks.append(w.model_copy(update={"week": i, "tasks": tasks}))
    plan = StudyPlanOut(weeks=weeks, advice=generated.advice[:10])

    conv = _conversation(db, ws, agent, user, None)
    state = dict(conv.state or {})
    state.update({"kind": "coach", "plan": plan.model_dump(), "progress": {"completed": [], "notes": []}})
    conv.state = state
    db.add(Message(workspace_id=ws.id, conversation_id=conv.id, role="user",
                   content=f"Study plan request: {'; '.join(body.goals)} ({body.weekly_hours}h/week)"))
    db.add(Message(workspace_id=ws.id, conversation_id=conv.id, role="assistant",
                   content=plan.model_dump_json(ensure_ascii=False)))
    # goals become durable memory
    mem = _memory(db, ws, user)
    for g in body.goals:
        mem.remember(ws.id, user.id, f"goal.{g[:60]}", g[:2000], category="goal")
    db.commit()
    audit(db, "agents.study_plan", actor_user_id=user.id, workspace_id=ws.id,
          entity="agent", entity_id=agent.id, meta={"conversation_id": str(conv.id)})
    return plan, str(conv.id)


def _coach_prompt(db: Session, ws: Workspace, agent, user: User, clean: str,
                  conversation_id=None, kb_id=None, embed_fn=None):
    """Shared preparation for sync + streaming coach turns."""
    conv = _conversation(db, ws, agent, user, conversation_id)
    prof = read_profile(db, ws, agent, user)
    mem = _memory(db, ws, user)
    mem_hits = mem.recall(ws.id, user.id, clean, top_k=6)
    state = dict(conv.state or {})
    plan_txt = ""
    if state.get("plan"):
        weeks = state["plan"].get("weeks", [])
        done = set((state.get("progress") or {}).get("completed", []))
        total = sum(len(w.get("tasks", [])) for w in weeks)
        plan_txt = (f"Current plan: {len(weeks)} weeks, {len(done)}/{total} tasks done. "
                    f"Weeks: {'; '.join(w.get('focus', '') for w in weeks[:6])}")
    context = ""
    if kb_id is not None:
        hits = hybrid_search(db, ws.id, clean, kb_id, top_k=6, embed_fn=embed_fn)
        context = "\n".join(f"[{i}] {h.chunk.content[:600]}" for i, h in enumerate(hits, 1))

    mem_txt = "; ".join(f"{m['key']}: {m['value']}" for m in mem_hits[:6]) or "(none)"
    hist = _history(db, conv)
    hist_txt = "; ".join(f"{m['role']}: {m['content'][:300]}" for m in hist) or "(new)"
    prompt = (
        "You are a supportive academic coach. Answer in Persian, concretely.\n"
        f"Profile: {prof['profile'] or '(unknown)'} | Goals: {prof['goals'] or '(none)'}\n"
        f"{plan_txt}\n"
        f"Relevant memories: {mem_txt}\n"
        f"Reference: {context or '(none)'}\n"
        f"History: {hist_txt}\n"
        f"Student: {clean}\nCoach:"
    )
    model, chat = _model(db)
    return conv, state, mem, prompt, model, chat


def _coach_persist(db: Session, ws: Workspace, agent, user: User,
                   conv: Conversation, state: dict, mem, clean: str, answer: str) -> None:
    db.add(Message(workspace_id=ws.id, conversation_id=conv.id, role="user", content=clean))
    db.add(Message(workspace_id=ws.id, conversation_id=conv.id, role="assistant", content=answer))
    state["turns"] = int(state.get("turns", 0)) + 1
    conv.state = state
    # rolling summary keeps long coaching stateful without re-reading everything
    mem.summarize(ws.id, user.id, f"agent:{AGENT_KEY}",
                  f"Last exchange: student asked {clean[:200]}; coach replied {answer[:200]}")
    db.commit()
    audit(db, "agents.chat", actor_user_id=user.id, workspace_id=ws.id,
          entity="agent", entity_id=agent.id, meta={"conversation_id": str(conv.id)})


def coach_chat(db: Session, ws: Workspace, agent, user: User, message: str,
               conversation_id=None, kb_id=None, embed_fn=None, generate_fn=None) -> CoachChatOut:
    _require_key(agent)
    clean = (message or "").strip()
    if not clean:
        raise HTTPException(422, "message is empty")
    clean = clean[:4000]
    if generate_fn is None:
        from app.ai.factory import get_chat_provider

        generate_fn = get_chat_provider(db).generate

    conv, state, mem, prompt, model, chat = _coach_prompt(
        db, ws, agent, user, clean, conversation_id, kb_id, embed_fn)
    answer = (generate_fn(prompt, model=model, temperature=chat.get("temperature", 0.7),
                          max_tokens=chat.get("max_tokens", 1500)) or "").strip()

    _coach_persist(db, ws, agent, user, conv, state, mem, clean, answer)
    return CoachChatOut(answer=answer, conversation_id=str(conv.id),
                        has_plan=bool(state.get("plan")))


async def coach_chat_stream_events(db: Session, ws: Workspace, agent, user: User,
                                   message: str, conversation_id=None, kb_id=None,
                                   embed_fn=None, stream_fn=None):
    """meta -> token* -> done; persistence happens once at the end."""
    _require_key(agent)
    clean = (message or "").strip()
    if not clean:
        raise HTTPException(422, "message is empty")
    clean = clean[:4000]

    conv, state, mem, prompt, model, chat = _coach_prompt(
        db, ws, agent, user, clean, conversation_id, kb_id, embed_fn)
    yield {"type": "meta", "conversation_id": str(conv.id),
           "has_plan": bool(state.get("plan"))}

    if stream_fn is None:
        from app.ai.factory import get_chat_provider

        stream_fn = get_chat_provider(db).stream
    parts: list[str] = []
    try:
        async for delta in stream_fn(prompt, model=model,
                                     temperature=float(chat.get("temperature", 0.7)),
                                     max_tokens=int(chat.get("max_tokens", 1500))):
            if delta:
                parts.append(delta)
                yield {"type": "token", "text": delta}
    except Exception as exc:
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"[:300]}
        return
    answer = "".join(parts).strip()
    _coach_persist(db, ws, agent, user, conv, state, mem, clean, answer)
    yield {"type": "done", "conversation_id": str(conv.id),
           "has_plan": bool(state.get("plan"))}


def update_progress(db: Session, ws: Workspace, agent, user: User,
                    body: ProgressUpdate) -> ProgressOut:
    _require_key(agent)
    conv = _conversation(db, ws, agent, user, body.conversation_id)
    state = dict(conv.state or {})
    plan = state.get("plan") or {}
    valid_ids = {t.get("id") for w in plan.get("weeks", []) for t in w.get("tasks", [])}
    unknown = [t for t in body.completed_task_ids if t not in valid_ids]
    if unknown:
        raise HTTPException(422, f"unknown task ids: {unknown}")
    progress = dict(state.get("progress") or {"completed": [], "notes": []})
    completed = sorted(set(progress.get("completed", [])) | set(body.completed_task_ids))
    notes = list(progress.get("notes", []))
    if body.note.strip():
        notes.append(body.note.strip()[:500])
    progress.update({"completed": completed, "notes": notes[-20:]})
    state["progress"] = progress
    conv.state = state
    db.add(Message(workspace_id=ws.id, conversation_id=conv.id, role="user",
                   content=f"Progress update: completed {body.completed_task_ids}. {body.note.strip()}"[:1000]))
    db.commit()
    total = sum(len(w.get("tasks", [])) for w in plan.get("weeks", []))
    return ProgressOut(conversation_id=str(conv.id), completed_task_ids=completed,
                       total_tasks=total, notes=notes)
