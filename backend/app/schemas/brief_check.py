from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BriefCheckDecision(BaseModel):
    item_id: str = Field(min_length=1, max_length=120)
    decision: str = Field(pattern="^(open|accepted|rejected|needs_followup)$")
    note: str | None = Field(default=None, max_length=4000)


class BriefCheckResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    matter_id: UUID
    input_filename: str
    input_sha256: str
    input_size: int
    status: str
    result: dict
    created_at: datetime
    updated_at: datetime


class BriefCheckListResponse(BaseModel):
    items: list[BriefCheckResponse]
