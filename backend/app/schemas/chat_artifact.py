"""Pydantic schemas for ChatArtifact endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatArtifactCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., min_length=1, max_length=200_000)
    format: str = Field(default="markdown", pattern="^markdown$")
    matter_id: UUID | None = None
    task_id: UUID | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("title must not be blank")
        return normalized

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class ChatArtifactUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    content: str | None = Field(default=None, min_length=1, max_length=200_000)
    matter_id: UUID | None = None
    task_id: UUID | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("title must not be blank")
        return normalized

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("content must not be blank")
        return value


class ChatArtifactResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    message_id: UUID | None
    created_by_user_id: UUID | None
    title: str
    content: str
    format: str
    version: int
    matter_id: UUID | None
    task_id: UUID | None
    saved_to_matter: bool
    saved_document_id: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChatArtifactListResponse(BaseModel):
    items: list[ChatArtifactResponse]
    total: int


class SaveArtifactToMatterRequest(BaseModel):
    matter_id: UUID
    task_id: UUID | None = None
    filename: str | None = Field(default=None, max_length=255)
    document_category: str = Field(
        default="generated",
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._ -]*$",
    )

    model_config = ConfigDict(str_strip_whitespace=True)


class SaveArtifactToMatterResponse(BaseModel):
    artifact_id: UUID
    document_id: UUID
    matter_id: UUID
    task_id: UUID | None
    filename: str
    download_url: str
    storage_backend: str
    storage_warning: str | None = None


class ExportArtifactRequest(BaseModel):
    format: str = Field(default="markdown", pattern="^(markdown|pdf|docx)$")
    filename: str | None = Field(default=None, max_length=255)

    model_config = ConfigDict(str_strip_whitespace=True)
