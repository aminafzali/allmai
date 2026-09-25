"""Student coach contracts. The study plan lives in conversation state;
profile/goals live in memory facts (profile.*, goal.*)."""

import uuid

from pydantic import BaseModel, Field


class ProfileUpdate(BaseModel):
    facts: dict[str, str] = Field(default_factory=dict, max_length=20)


class StudyTaskOut(BaseModel):
    id: str
    title: str
    subject: str = ""
    minutes: int = 0


class StudyWeekOut(BaseModel):
    week: int
    focus: str = ""
    tasks: list[StudyTaskOut] = []
    milestones: list[str] = []


class StudyPlanBody(BaseModel):
    """What the LLM generates (ids attached by the service)."""

    weeks: list[StudyWeekOut] = []
    advice: list[str] = []


class StudyPlanOut(StudyPlanBody):
    pass


class StudyPlanIn(BaseModel):
    goals: list[str] = Field(min_length=1, max_length=10)
    weekly_hours: int = Field(ge=1, le=80, default=10)
    kb_id: uuid.UUID | None = None


class CoachChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = None
    kb_id: uuid.UUID | None = None


class CoachChatOut(BaseModel):
    answer: str
    conversation_id: str
    has_plan: bool = False


class ProgressUpdate(BaseModel):
    conversation_id: uuid.UUID
    completed_task_ids: list[str] = []
    note: str = Field(max_length=1000, default="")


class ProgressOut(BaseModel):
    conversation_id: str
    completed_task_ids: list[str]
    total_tasks: int
    notes: list[str]
