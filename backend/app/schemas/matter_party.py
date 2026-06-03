"""Pydantic schemas for matter parties."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class MatterPartyCreate(BaseModel):
    matter_id: uuid.UUID
    contact_id: uuid.UUID
    role: str = "other"
    is_primary: bool = False
    notes: Optional[str] = None


class MatterPartyUpdate(BaseModel):
    role: Optional[str] = None
    is_primary: Optional[bool] = None
    notes: Optional[str] = None


class MatterPartyResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    matter_id: uuid.UUID
    contact_id: Optional[uuid.UUID] = None
    role: str
    is_primary: bool
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    contact_display_name: Optional[str] = None

    class Config:
        from_attributes = True


class MatterPartyListResponse(BaseModel):
    items: list[MatterPartyResponse]
    total: int
