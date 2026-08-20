"""Pydantic schemas for MatterDocument endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class MatterDocumentResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    matter_id: UUID
    task_id: UUID | None = None
    uploaded_by_user_id: UUID | None
    filename: str
    content_type: str | None
    file_size: int | None
    description: str | None
    document_category: str | None
    portal_visible: bool = False
    storage_backend: str = "local"
    storage_provider: str | None = None
    provider_object_id: str | None = None
    provider_drive_id: str | None = None
    provider_parent_id: str | None = None
    storage_error: str | None = None
    cloud_url: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MatterDocumentListResponse(BaseModel):
    items: list[MatterDocumentResponse]
    total: int


class MatterDocumentUpdate(BaseModel):
    description: str | None = None
    document_category: str | None = None
    portal_visible: bool | None = None
