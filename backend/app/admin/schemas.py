from pydantic import BaseModel


class AISettingUpdate(BaseModel):
    key: str
    value: dict


class AdminUserOut(BaseModel):
    id: str
    email: str
    full_name: str
    is_admin: bool
    created_at: str


class AdminUserUpdate(BaseModel):
    is_admin: bool
