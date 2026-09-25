import uuid
from datetime import datetime

from pydantic import BaseModel


class WorkspaceCreate(BaseModel):
    name: str
    type: str = "personal"  # personal | shared


class WorkspaceOut(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    owner_user_id: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class MemberAdd(BaseModel):
    user_id: uuid.UUID
    role: str = "member"  # owner | admin | member
