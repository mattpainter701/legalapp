"""Pydantic schemas for MatterDocument endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MatterDocumentTagResponse(BaseModel):
    id: UUID
    name: str
    color: str

    model_config = ConfigDict(from_attributes=True)


class MatterDocumentResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    matter_id: UUID
    task_id: UUID | None = None
    generated_artifact_id: UUID | None = None
    generated_artifact_revision_id: UUID | None = None
    supersedes_document_id: UUID | None = None
    uploaded_by_user_id: UUID | None
    folder_id: UUID | None = None
    # Materialized "Discovery/Depositions" path so a client can render a row's
    # location without walking the tree.
    folder_path: str | None = None
    tags: list[MatterDocumentTagResponse] = Field(default_factory=list)
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
    provider_etag: str | None = None
    provider_version_id: str | None = None
    provider_checksum: str | None = None
    provider_modified_at: datetime | None = None
    storage_verified_at: datetime | None = None
    storage_error: str | None = None
    document_role: str | None = None
    document_status: str | None = None
    storage_state: str | None = None
    document_sha256: str | None = None
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


# ── Folders ──────────────────────────────────────────────────────────────────


class MatterDocumentFolderResponse(BaseModel):
    id: UUID
    matter_id: UUID
    parent_id: UUID | None
    name: str
    path: str
    depth: int
    kind: str
    system_key: str | None
    # Documents filed directly in this folder, not counting subfolders.
    document_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MatterDocumentFolderListResponse(BaseModel):
    items: list[MatterDocumentFolderResponse]
    total: int
    # Documents sitting at the matter root, i.e. in no folder at all.
    root_document_count: int = 0


class MatterDocumentFolderCreate(BaseModel):
    name: str
    parent_id: UUID | None = None


class MatterDocumentFolderUpdate(BaseModel):
    """Rename and/or reparent. Omitting ``parent_id`` leaves the parent alone;
    sending it as ``null`` moves the folder to the matter root."""

    name: str | None = None
    parent_id: UUID | None = None


class MatterDocumentMoveRequest(BaseModel):
    document_ids: list[UUID] = Field(min_length=1, max_length=200)
    # ``null`` files the documents at the matter root.
    folder_id: UUID | None = None


class MatterDocumentMoveResponse(BaseModel):
    moved: int
    folder_id: UUID | None
    items: list[MatterDocumentResponse]


class MatterDocumentFolderDeleteResponse(BaseModel):
    deleted_folder_id: UUID
    documents_moved: int
    moved_to_folder_id: UUID | None


# ── Tags ─────────────────────────────────────────────────────────────────────


class MatterDocumentTagCreate(BaseModel):
    name: str
    color: str | None = None


class MatterDocumentTagUpdate(BaseModel):
    name: str | None = None
    color: str | None = None


class MatterDocumentTagListResponse(BaseModel):
    items: list[MatterDocumentTagResponse]
    total: int


class MatterDocumentTagAssignRequest(BaseModel):
    tag_ids: list[UUID] = Field(default_factory=list, max_length=25)
