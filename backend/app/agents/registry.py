"""Generic agent catalogue. Adding a future agent (career, fitness,
personal/business/document assistant, ...) means registering a new entry
here + its prompts/tools — no engine rewrite."""

AGENT_CATALOG: dict[str, dict] = {
    "teacher_lesson_planner": {
        "type": "agent",
        "goal": "Build a professional lesson plan from a knowledge base and teacher request.",
        "tools": ["knowledge_search", "excel_query"],
        "model": None,  # resolved from ENV at runtime
    },
    "student_academic_coach": {
        "type": "agent",
        "goal": "Guide study planning, track progress and adjust plans using profile, memory and goals.",
        "tools": ["knowledge_search", "memory_search", "excel_query"],
        "model": None,
    },
    "note_taking_assistant": {
        "type": "agent",
        "goal": "Answer from the user's files, notes and links; help organize study notes.",
        "tools": ["knowledge_search", "memory_search", "excel_query"],
        "model": None,  # resolved from ENV at runtime
    },
    "data_extraction_assistant": {
        "type": "agent",
        "goal": "Extract structured data and business leads from the public internet (web + maps) in the user's browser; save leads to the database.",
        "tools": ["web_search", "maps_search"],
        "model": None,  # resolved from ENV at runtime
    },
}


def get_agent_spec(key: str) -> dict:
    try:
        return AGENT_CATALOG[key]
    except KeyError as exc:
        raise KeyError(f"unknown agent key: {key!r}") from exc
