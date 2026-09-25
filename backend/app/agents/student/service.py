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


def _load_definition(db: Session, agent):
    try:
        from app.agents.definitions_service import get_definition

        did = getattr(agent, "definition_id", None)
        return get_definition(db, did) if did else None
    except Exception:
        return None


def _model(db: Session, definition=None) -> tuple[str | None, dict]:
    """Effective (model, chat_cfg) via the central resolver (provider +
    model + temperature + max_tokens, definition over agent key over chat)."""
    from app.ai.settings import resolve_agent_chat

    cfg = resolve_agent_chat(db, AGENT_KEY, definition)
    return cfg.get("model"), cfg


def _plan_detail_text(state: dict) -> str:
    """Full task-level plan state: which tasks are done vs pending."""
    plan = state.get("plan") or {}
    weeks = plan.get("weeks", []) or []
    if not weeks:
        return ""
    done = set((state.get("progress") or {}).get("completed", []))
    all_ids = {t.get("id") for w in weeks for t in (w.get("tasks", []) or [])}
    total = len(all_ids)
    lines = [f"Current plan: {len(weeks)} weeks, "
             f"{len(done & all_ids)}/{total} tasks done."]
    for w in weeks[:6]:
        lines.append(f"Week {w.get('week')}: {w.get('focus', '')}")
        for t in (w.get("tasks", []) or [])[:10]:
            mark = "x" if t.get("id") in done else " "
            lines.append(f"  [{mark}] {t.get('id')}: {t.get('title', '')} "
                         f"({t.get('subject', '')}, {t.get('minutes', 0)}m)")
    notes = (state.get("progress") or {}).get("notes", [])[-3:]
    if notes:
        lines.append("Recent notes: " + "; ".join(str(n) for n in notes))
    return "\n".join(lines)


def _provider_for(chat_cfg: dict):
    from app.ai.factory import get_provider

    try:
        return get_provider((chat_cfg or {}).get("provider") or "openai_compat")
    except ValueError:
        from app.ai.factory import get_provider as _gp

        return _gp("openai_compat")


def generate_study_plan(db: Session, ws: Workspace, agent, user: User, body: StudyPlanIn,
                        embed_fn=None, generate_structured_fn=None) -> tuple[StudyPlanOut, str]:
    from app.agents.student.schemas import StudyPlanBody

    _require_key(agent)
    prof = read_profile(db, ws, agent, user)
    definition = _load_definition(db, agent)
    model, chat = _model(db, definition)
    if generate_structured_fn is None:
        generate_structured_fn = _provider_for(chat).generate_structured

    context = ""
    if body.kb_id is not None:
        hits = hybrid_search(db, ws.id, " ".join(body.goals), body.kb_id, top_k=6, embed_fn=embed_fn)
        context = "\n".join(f"- {h.chunk.content[:500]}" for h in hits)

    from app.agents import prompt_templates as PT

    prompt = PT.render(PT.get_template(definition, "study_plan"), {
        "profile": prof["profile"] or "(unknown)",
        "goals": "; ".join(body.goals),
        "weekly_hours": body.weekly_hours,
        "context": context or "(none)",
    })
    if prompt is None:
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
                  conversation_id=None, kb_id=None, embed_fn=None,
                  with_tools: bool = False):
    """Shared preparation for sync + streaming coach turns."""
    from app.agents import prompt_templates as PT

    conv = _conversation(db, ws, agent, user, conversation_id)
    prof = read_profile(db, ws, agent, user)
    definition = _load_definition(db, agent)
    mem = _memory(db, ws, user)
    mem_hits = mem.recall(ws.id, user.id, clean, top_k=6)
    state = dict(conv.state or {})
    plan_txt = _plan_detail_text(state)
    context = ""
    if kb_id is not None:
        hits = hybrid_search(db, ws.id, clean, kb_id, top_k=6, embed_fn=embed_fn)
        context = "\n".join(f"[{i}] {h.chunk.content[:600]}" for i, h in enumerate(hits, 1))

    mem_txt = "; ".join(f"{m['key']}: {m['value']}" for m in mem_hits[:6]) or "(none)"
    hist = _history(db, conv)
    hist_txt = "; ".join(f"{m['role']}: {m['content'][:300]}" for m in hist) or "(new)"
    prompt = PT.render(PT.get_template(definition, "coach_chat"), {
        "profile": prof["profile"] or "(unknown)",
        "goals": prof["goals"] or "(none)",
        "plan": plan_txt or "(no plan yet)",
        "memories": mem_txt,
        "context": context or "(none)",
        "history": hist_txt,
        "message": clean,
    })
    if prompt is None:
        prompt = (
            "You are a supportive academic coach. Answer in Persian, concretely.\n"
            f"Profile: {prof['profile'] or '(unknown)'} | Goals: {prof['goals'] or '(none)'}\n"
            f"{plan_txt}\n"
            f"Relevant memories: {mem_txt}\n"
            f"Reference: {context or '(none)'}\n"
            f"History: {hist_txt}\n"
            f"Student: {clean}\nCoach:"
        )
    if with_tools:
        prompt += "\n\n" + PT.COACH_TOOLS_GUIDE
    model, chat = _model(db, definition)
    return conv, state, mem, prompt, model, chat


def _coach_persist(db: Session, ws: Workspace, agent, user: User,
                   conv: Conversation, state: dict, mem, clean: str, answer: str) -> None:
    db.add(Message(workspace_id=ws.id, conversation_id=conv.id, role="user", content=clean))
    db.add(Message(workspace_id=ws.id, conversation_id=conv.id, role="assistant", content=answer))
    # Re-read: coach tools may have updated conv.state after `state` was
    # snapshotted (same session) — merging preserves their writes instead
    # of clobbering them with the stale copy.
    fresh = dict(conv.state or {})
    fresh["turns"] = int(fresh.get("turns", 0)) + 1
    conv.state = fresh
    # rolling summary keeps long coaching stateful without re-reading everything
    mem.summarize(ws.id, user.id, f"agent:{AGENT_KEY}",
                  f"Last exchange: student asked {clean[:200]}; coach replied {answer[:200]}")
    db.commit()
    audit(db, "agents.chat", actor_user_id=user.id, workspace_id=ws.id,
          entity="agent", entity_id=agent.id, meta={"conversation_id": str(conv.id)})


COACH_TOOLS = [
    {"type": "function",
     "function": {"name": "get_study_plan",
                  "description": "Read the student's latest study plan with per-task done/pending status.",
                  "parameters": {"type": "object", "properties": {}}}},
    {"type": "function",
     "function": {"name": "create_study_plan",
                  "description": "Build a new structured study plan for the student from goals and weekly hours.",
                  "parameters": {"type": "object",
                                 "properties": {
                                     "goals": {"type": "array",
                                               "items": {"type": "string"}},
                                     "weekly_hours": {"type": "integer"}},
                                 "required": ["goals"]}}},
    {"type": "function",
     "function": {"name": "update_progress",
                  "description": "Mark study-plan task ids as done, with an optional note.",
                  "parameters": {"type": "object",
                                 "properties": {
                                     "completed_task_ids": {"type": "array",
                                                            "items": {"type": "string"}},
                                     "note": {"type": "string"}},
                                 "required": ["completed_task_ids"]}}},
]

_COACH_TOOL_ROUNDS = 3


def _latest_plan_conv(db: Session, ws: Workspace, agent, user):
    rows = (db.query(Conversation)
            .filter(Conversation.workspace_id == ws.id,
                    Conversation.agent_id == agent.id,
                    Conversation.user_id == user.id)
            .order_by(Conversation.created_at.desc()).all())
    for row in rows:
        if (dict(row.state or {}).get("plan") or {}).get("weeks"):
            return row
    return None


def _execute_coach_tool(db: Session, ws: Workspace, agent, user, conv,
                        kb_id, embed_fn, call: dict) -> tuple[str, bool]:
    """Run one coach tool call. Returns (model-readable result, plan_created)."""
    from app.auth.service import audit

    name = call.get("name")
    args = call.get("arguments") or {}
    made_plan = False
    try:
        if name == "get_study_plan":
            plan_conv = _latest_plan_conv(db, ws, agent, user) or conv
            detail = _plan_detail_text(dict(plan_conv.state or {}))
            result = detail or "No study plan exists yet."
        elif name == "create_study_plan":
            goals = [str(g)[:200] for g in (args.get("goals") or [])
                     if str(g).strip()][:10]
            if not goals:
                result = "Error: goals is required (non-empty list)."
            else:
                try:
                    hours = max(1, min(80, int(args.get("weekly_hours", 5))))
                except (TypeError, ValueError):
                    hours = 5
                plan, new_cid = generate_study_plan(
                    db, ws, agent, user,
                    StudyPlanIn(goals=goals, weekly_hours=hours, kb_id=kb_id),
                    embed_fn=embed_fn)
                made_plan = True
                result = (f"Created plan {new_cid}: {len(plan.weeks)} weeks. " +
                          "; ".join(f"W{w.week} {w.focus}" for w in plan.weeks[:6]))
        elif name == "update_progress":
            ids = [str(i) for i in (args.get("completed_task_ids") or [])
                   if str(i).strip()]
            if not ids:
                result = "Error: completed_task_ids is required."
            else:
                plan_conv = _latest_plan_conv(db, ws, agent, user)
                if plan_conv is None:
                    result = "Error: no study plan exists yet."
                else:
                    out = update_progress(
                        db, ws, agent, user,
                        ProgressUpdate(conversation_id=plan_conv.id,
                                       completed_task_ids=ids,
                                       note=str(args.get("note") or "")[:500]))
                    result = (f"Marked done: {out.completed_task_ids}. "
                              f"Total tasks: {out.total_tasks}.")
        else:
            result = f"Error: unknown tool {name}."
    except HTTPException as exc:
        result = f"Error: {exc.detail}"
    except Exception as exc:
        result = f"Error: {type(exc).__name__}: {exc}"[:500]
    audit(db, "agents.coach_tool", actor_user_id=user.id, workspace_id=ws.id,
          entity="agent", entity_id=agent.id,
          meta={"tool": str(name), "conversation_id": str(conv.id),
                "ok": not result.startswith("Error:"),
                "args": str(call.get("arguments"))[:500],
                "result": result[:500]})
    return result, made_plan


def coach_chat(db: Session, ws: Workspace, agent, user: User, message: str,
               conversation_id=None, kb_id=None, embed_fn=None, generate_fn=None,
               generate_tools_fn=None) -> CoachChatOut:
    _require_key(agent)
    clean = (message or "").strip()
    if not clean:
        raise HTTPException(422, "message is empty")
    clean = clean[:4000]
    conv, state, mem, prompt, model, chat = _coach_prompt(
        db, ws, agent, user, clean, conversation_id, kb_id, embed_fn,
        with_tools=True)
    if generate_fn is None:
        generate_fn = _provider_for(chat).generate
    temperature = float(chat.get("temperature", 0.7))
    max_tokens = int(chat.get("max_tokens", 1500))

    # Agentic loop: the assistant reads/creates plans and marks progress
    # through tools (same assistant, same turn). Plain-text path preserved
    # when no tools fn is injected or no call is made.
    tools_fn = generate_tools_fn
    tools_used: list[str] = []
    made_plan = False
    answer = ""
    if callable(tools_fn):
        messages = [{"role": "user", "content": prompt}]
        for _ in range(_COACH_TOOL_ROUNDS):
            try:
                turn = tools_fn(messages, COACH_TOOLS, model=model,
                                temperature=temperature, max_tokens=max_tokens)
            except Exception:
                turn = None
            if not isinstance(turn, dict) or not (turn.get("calls") or []):
                if isinstance(turn, dict) and turn.get("text"):
                    answer = str(turn["text"]).strip()
                break
            failed = False
            for call in turn["calls"]:
                tools_used.append(str(call.get("name")))
                result, created = _execute_coach_tool(
                    db, ws, agent, user, conv, kb_id, embed_fn, call)
                made_plan = made_plan or created
                failed = failed or result.startswith("Error:")
                messages.append({"role": "user", "content":
                                 f"[tool {call.get('name')} result]\n{result}"})
            # On failure the loop continues so the model can self-correct
            # instead of presenting a failed action as done.
            if failed:
                continue
            if turn.get("text"):
                answer = str(turn["text"]).strip()
                break
            messages.append({"role": "assistant", "content": ""})
        if not answer and tools_used:
            transcript = "\n\n".join(
                f"{m['role']}: {m['content']}" for m in messages)
            answer = (generate_fn(
                transcript + "\nNow give the final answer to the student in Persian:",
                model=model, temperature=temperature,
                max_tokens=max_tokens) or "").strip()
    if not answer:
        answer = (generate_fn(prompt, model=model, temperature=temperature,
                              max_tokens=max_tokens) or "").strip()

    has_plan = bool(state.get("plan")) or made_plan
    if not has_plan and tools_used:
        # tools may act on the latest plan conversation, not this one
        has_plan = _latest_plan_conv(db, ws, agent, user) is not None
    _coach_persist(db, ws, agent, user, conv, state, mem, clean, answer)
    return CoachChatOut(answer=answer, conversation_id=str(conv.id),
                        has_plan=has_plan, tools_used=tools_used)


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
        stream_fn = _provider_for(chat).stream
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
