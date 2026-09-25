"""Teacher lesson-planner contracts.

The LLM produces the pedagogical body; source references are attached by
the service from ACTUAL retrieved chunks (never hallucinated by the model).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class LessonPlanIn(BaseModel):
    kb_id: uuid.UUID
    chapter: str = Field(min_length=2, max_length=300)
    grade: str = Field(min_length=1, max_length=100)
    duration_minutes: int = Field(ge=5, le=480, default=45)
    teaching_style: str = Field(min_length=1, max_length=200, default="interactive lecture")
    instructions: str = Field(max_length=2000, default="")


class TopicOut(BaseModel):
    title: str
    points: list[str] = []


class SourceRefOut(BaseModel):
    n: int
    chunk_id: str
    source: str = ""
    page_no: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None


class LessonPlanBody(BaseModel):
    """What the LLM generates (references attached separately)."""

    title: str
    objectives: list[str] = []
    prerequisites: list[str] = []
    topics: list[TopicOut] = []
    activities: list[str] = []
    examples: list[str] = []
    questions: list[str] = []
    assessment: list[str] = []


class LessonPlanOut(LessonPlanBody):
    references: list[SourceRefOut] = []


class LessonPlanResponse(BaseModel):
    plan: LessonPlanOut
    citations: list[SourceRefOut]
    conversation_id: str
    lesson_plan_id: str


class LessonPlanListOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    chapter: str
    grade: str
    duration_minutes: int
    teaching_style: str
    created_at: datetime

    model_config = {"from_attributes": True}


class LessonPlanDetailOut(BaseModel):
    id: uuid.UUID
    kb_id: uuid.UUID
    chapter: str
    grade: str
    duration_minutes: int
    teaching_style: str
    instructions: str
    plan: LessonPlanOut
    conversation_id: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}
