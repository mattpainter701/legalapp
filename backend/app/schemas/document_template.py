"""Pydantic schemas for DocumentTemplate."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

CATEGORIES = ["engagement_letter", "retainer", "NDA", "motion", "other"]


class DocumentTemplateCreate(BaseModel):
    title: str
    body: str
    category: str = "other"


class DocumentTemplateUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    category: Optional[str] = None
    is_active: Optional[bool] = None


class DocumentTemplateResponse(BaseModel):
    id: str
    title: str
    body: str
    category: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DocumentTemplateListResponse(BaseModel):
    items: list[DocumentTemplateResponse]
    total: int


class DocumentTemplateRenderRequest(BaseModel):
    variables: dict[str, str] = Field(default_factory=dict)
    matter_id: Optional[str] = None


class DocumentTemplateRenderResponse(BaseModel):
    rendered: str
    matter_document_id: Optional[str] = None
