"""Admin-editable prompt templates (Agent Studio).

Each template is plain text with {placeholders}; the service fills them per
turn. An empty/missing template means "use the built-in default" (the exact
texts the engine used before Studio existed). Unrenderable templates
(unknown leftover {placeholder}) also fall back to the default — a bad
template never breaks a turn, it just gets ignored.

Template keys (stable contract, shown in Studio):
- lesson_plan : teacher lesson-plan generation
- study_plan  : student study-plan generation
- coach_chat  : student coach chat preamble
- note_chat   : note-taking assistant chat preamble
"""

import re

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

LESSON_PLAN_DEFAULT = (
    "You are an expert curriculum designer. Write a professional lesson plan "
    "in Persian (keep technical terms as-is). Base EVERY section strictly on "
    "the retrieved context below; do not invent facts beyond it.\n\n"
    "Chapter/topic: {chapter}\nGrade: {grade}\n"
    "Duration: {duration_minutes} minutes\nTeaching style: {teaching_style}"
    "{instructions_block}\n\nRetrieved context:\n{context}\n\n"
    "Return: title, objectives (measurable), prerequisites, topics "
    "(each with key points), activities fitted to the duration and style, "
    "concrete examples, check-for-understanding questions, and assessment ideas."
)

STUDY_PLAN_DEFAULT = (
    "You are an academic coach. Design a realistic weekly study plan in Persian.\n"
    "Student profile: {profile}\n"
    "Goals: {goals}\nAvailable: {weekly_hours} hours/week.\n"
    "Reference material:\n{context}\n"
    "Return weeks (week number, focus, tasks with title/subject/minutes, "
    "milestones) plus short advice. Keep total minutes near the budget."
)

COACH_CHAT_DEFAULT = (
    "You are a supportive academic coach. Answer in Persian, concretely.\n"
    "Profile: {profile} | Goals: {goals}\n"
    "{plan}\n"
    "Relevant memories: {memories}\n"
    "Reference: {context}\n"
    "History: {history}\n"
    "Student: {message}\nCoach:"
)

COACH_TOOLS_GUIDE = (
    "You can act on study plans with tools: get_study_plan (read the full "
    "plan with task status), create_study_plan (build a new plan from goals "
    "and weekly hours when the student asks for planning), update_progress "
    "(mark task ids done, with an optional note). Rules: (1) when the "
    "student asks about their plan, progress, or remaining tasks, call "
    "get_study_plan first and answer from its result; (2) when the student "
    "says they finished/completed a task (e.g. task id mentioned with "
    "words like finished/done), you MUST call update_progress with that "
    "task id instead of only congratulating; (3) when the student asks for "
    "(re)planning, call create_study_plan. Otherwise just answer. "
    "Always reply in Persian."
)

TEMPLATE_DOCS = {
    "lesson_plan": {
        "description": "Teacher lesson-plan generation",
        "placeholders": ["chapter", "grade", "duration_minutes",
                         "teaching_style", "instructions_block", "context"],
    },
    "study_plan": {
        "description": "Student study-plan generation",
        "placeholders": ["profile", "goals", "weekly_hours", "context"],
    },
    "coach_chat": {
        "description": "Student coach chat preamble (tools guide appended automatically)",
        "placeholders": ["profile", "goals", "plan", "memories",
                         "context", "history", "message"],
    },
    "note_chat": {
        "description": "Note-taking assistant chat preamble",
        "placeholders": ["facts", "memories", "context", "history",
                         "message"],
    },
}

NOTE_CHAT_DEFAULT = (
    "You are a helpful study-notes assistant. Answer in Persian, concretely.\n"
    "Known about the user:\n{facts}\n"
    "Relevant memories:\n{memories}\n"
    "Reference material (files, notes, links):\n{context}\n"
    "History:\n{history}\n"
    "Student: {message}\nAssistant:"
)

DEFAULTS = {
    "lesson_plan": LESSON_PLAN_DEFAULT,
    "study_plan": STUDY_PLAN_DEFAULT,
    "coach_chat": COACH_CHAT_DEFAULT,
    "note_chat": NOTE_CHAT_DEFAULT,
}


def render(template: str | None, values: dict) -> str | None:
    """Fill {placeholders}. None when missing/empty/unrenderable."""
    if not isinstance(template, str) or not template.strip():
        return None
    try:
        out = template
        for k, v in (values or {}).items():
            out = out.replace("{" + str(k) + "}", str(v))
        if _PLACEHOLDER_RE.search(out):
            return None
        return out
    except Exception:
        return None


def get_template(definition, key: str) -> str | None:
    """Admin override from the definition, or None (use built-in default)."""
    try:
        tpl = (getattr(definition, "prompt_templates", None) or {}).get(key)
    except Exception:
        return None
    return tpl if isinstance(tpl, str) and tpl.strip() else None
