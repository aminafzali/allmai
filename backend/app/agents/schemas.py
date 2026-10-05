import uuid
from datetime import datetime

from pydantic import BaseModel


class AgentCreate(BaseModel):
    key: str
    type: str = "assistant"
    name: str = ""
    config: dict = {}
    definition_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None
    custom_instructions: str = ""
    kb_ids: list[uuid.UUID] | None = None


class AgentUpdate(BaseModel):
    name: str | None = None
    custom_instructions: str | None = None
    runtime_state: dict | None = None
    owner_user_id: uuid.UUID | None = None
    kb_ids: list[uuid.UUID] | None = None


class AgentOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    key: str
    type: str
    name: str
    config: dict
    definition_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None
    custom_instructions: str = ""
    runtime_state: dict = {}
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentKnowledgeView(BaseModel):
    global_: list[str] = []
    workspace: list[str] = []

    model_config = {"populate_by_name": True}


class ChatIn(BaseModel):
    message: str
    conversation_id: uuid.UUID | None = None
    kb_id: uuid.UUID | None = None


class ChatPreviewIn(BaseModel):
    """One-off assistant generation that is deliberately NOT persisted."""
    message: str
    kb_id: uuid.UUID | None = None


class CitationOut(BaseModel):
    n: int
    chunk_id: str
    source: str = ""
    page_no: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None


class ChatOut(BaseModel):
    answer: str
    citations: list[CitationOut]
    conversation_id: str
    remaining: int = 0  # translation pieces left ("ادامه بده")


class ConversationOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    state: dict
    title: str = ""
    pinned: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationPatch(BaseModel):
    title: str | None = None
    pinned: bool | None = None


class MessageOut(BaseModel):
    role: str
    content: str
    citations: dict
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------- Agent Definitions (admin) ----------

class DefinitionCreate(BaseModel):
    key: str
    title: str = ""
    description: str = ""
    type: str = "agent"
    instructions: str = ""
    behavior_rules: dict = {}
    methodology: str = ""
    capabilities: dict = {}
    tools: dict = {}
    workflow: dict = {}
    model_defaults: dict = {}
    prompt_templates: dict = {}
    safety_rules: dict = {}
    output_format: dict = {}
    is_active: bool = True


class DefinitionUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    type: str | None = None
    instructions: str | None = None
    behavior_rules: dict | None = None
    methodology: str | None = None
    capabilities: dict | None = None
    tools: dict | None = None
    workflow: dict | None = None
    model_defaults: dict | None = None
    prompt_templates: dict | None = None
    safety_rules: dict | None = None
    output_format: dict | None = None
    is_active: bool | None = None


class DefinitionOut(BaseModel):
    id: uuid.UUID
    key: str
    title: str
    description: str
    type: str
    instructions: str
    behavior_rules: dict
    methodology: str
    capabilities: dict
    tools: dict
    workflow: dict
    model_defaults: dict
    prompt_templates: dict
    safety_rules: dict
    output_format: dict
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class DefinitionKnowledgeUpdate(BaseModel):
    kb_ids: list[uuid.UUID] = []


# ---------- Client-executed search tools (browser runs, server resumes) ----------

class ToolResultIn(BaseModel):
    call_id: str
    ok: bool = True
    results: list[dict] = []
    served_by: str = ""
    error: str = ""
    debug: str = ""  # browser-side trace (endpoint attempts), logged only


class ChatResumeIn(BaseModel):
    conversation_id: uuid.UUID
    results: list[ToolResultIn] = []


# ---------- Leads (lead-mining rows) ----------

class LeadOut(BaseModel):
    id: uuid.UUID
    query: str = ""
    name: str = ""
    address: str = ""
    phone: str = ""
    hours: str = ""
    website: str = ""
    lat: float | None = None
    lng: float | None = None
    source: str = ""
    status: str = "new"
    created_at: datetime

    model_config = {"from_attributes": True}


class LeadStatusIn(BaseModel):
    lead_ids: list[uuid.UUID] = []
    status: str = "new"


class LeadsImportIn(BaseModel):
    lead_ids: list[uuid.UUID] = []
