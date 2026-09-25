"""Persian lesson-plan prompt builder (pure function, unit-testable)."""


def build_lesson_prompt(chapter: str, grade: str, duration_minutes: int,
                        teaching_style: str, instructions: str, context: str) -> str:
    extra = f"\nTeacher's extra instructions: {instructions}" if instructions.strip() else ""
    return (
        "You are an expert curriculum designer. Write a professional lesson plan "
        "in Persian (keep technical terms as-is). Base EVERY section strictly on "
        "the retrieved context below; do not invent facts beyond it.\n\n"
        f"Chapter/topic: {chapter}\nGrade: {grade}\n"
        f"Duration: {duration_minutes} minutes\nTeaching style: {teaching_style}"
        f"{extra}\n\nRetrieved context:\n{context or '(empty)'}\n\n"
        "Return: title, objectives (measurable), prerequisites, topics "
        "(each with key points), activities fitted to the duration and style, "
        "concrete examples, check-for-understanding questions, and assessment ideas."
    )
