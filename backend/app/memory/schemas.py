import uuid
from datetime import datetime

from pydantic import BaseModel


class MemoryUpsert(BaseModel):
    key: str
    value: str
    category: str = "fact"


class MemoryFactOut(BaseModel):
    key: str
    value: str
    category: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class MemorySearch(BaseModel):
    query: str
    top_k: int = 8


class MemoryHit(BaseModel):
    key: str
    value: str
    category: str
    score: float


class SummaryUpsert(BaseModel):
    scope: str = "general"
    summary: str


class SummaryOut(BaseModel):
    scope: str
    summary: str
    updated_at: datetime

    model_config = {"from_attributes": True}
