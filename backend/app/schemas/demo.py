from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


class DemoSessionRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    access_code: str = Field(min_length=1, max_length=256)

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Name is required")
        return normalized


class DemoSessionResponse(BaseModel):
    user_id: str
    tenant_id: str
    session_id: str
    expires_at: datetime
    quota: int
    used: int


class DemoInfo(BaseModel):
    session_id: str
    expires_at: datetime
    quota: int
    reserved: int
    used: int
