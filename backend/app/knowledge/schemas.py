import uuid
from datetime import datetime

from pydantic import BaseModel


class KnowledgeBaseCreate(BaseModel):
    title: str
    description: str = ""


class KnowledgeBaseOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID | None
    title: str
    description: str
    scope: str = "workspace"
    created_at: datetime

    model_config = {"from_attributes": True}


class SourceLinkCreate(BaseModel):
    type: str  # url | note
    title: str = ""
    url: str = ""
    content: str = ""


class SourceTextUpdate(BaseModel):
    text: str  # manual replacement of the extracted text (re-indexed)


class SourceReviseIn(BaseModel):
    instruction: str  # how the AI should re-extract/revise the text


class SuggestTitleIn(BaseModel):
    text: str  # message / transcript text to title (capped server-side)


class SuggestTitleOut(BaseModel):
    title: str


class SourceRenameIn(BaseModel):
    title: str  # new display title (stored in sources.filename)


class SourceOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID | None
    kb_id: uuid.UUID
    type: str
    filename: str
    mime: str
    size_bytes: int
    status: str
    error: str
    parse_meta: dict = {}
    processing_started_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SearchIn(BaseModel):
    query: str
    kb_id: uuid.UUID | None = None
    top_k: int = 8


class RetrievedChunkOut(BaseModel):
    chunk_id: uuid.UUID
    content: str
    score: float
    source_id: uuid.UUID | None = None
    filename: str = ""
    page_no: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    vector_rank: int | None = None
    fts_rank: int | None = None


class SearchOut(BaseModel):
    query: str
    chunks: list[RetrievedChunkOut]


class DebugOut(BaseModel):
    query: str
    model: str
    retrieved: list[RetrievedChunkOut]
    context: str
    answer: str
    retrieval_ms: int = 0
    generation_ms: int = 0
