"""Generic agent catalogue. Adding a future agent (career, fitness,
personal/business/document assistant, ...) means registering a new entry
here + its prompts/tools — no engine rewrite."""

AGENT_CATALOG: dict[str, dict] = {
    "teacher_lesson_planner": {
        "type": "agent",
        "goal": "Build a professional lesson plan from a knowledge base and teacher request.",
        "tools": ["knowledge_search"],
        "model": None,  # resolved from ENV at runtime
    },
    "student_academic_coach": {
        "type": "agent",
        "goal": "Guide study planning, track progress and adjust plans using profile, memory and goals.",
        "tools": ["knowledge_search", "memory_search"],
        "model": None,
    },
}


def get_agent_spec(key: str) -> dict:
    try:
        return AGENT_CATALOG[key]
    except KeyError as exc:
        raise KeyError(f"unknown agent key: {key!r}") from exc
