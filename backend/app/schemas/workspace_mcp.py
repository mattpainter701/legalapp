"""Strict argument contracts for Workspace MCP-only lifecycle tools."""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.chat_action import ChatActionModel


class SearchClientsArgs(ChatActionModel):
    query: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["prospect", "active", "inactive", "former"] | None = None
    active_only: bool = True
    limit: int = Field(default=25, ge=1, le=50)


class GetClientArgs(ChatActionModel):
    client_id: UUID
    matter_limit: int = Field(default=25, ge=1, le=50)
    related_contact_limit: int = Field(default=25, ge=1, le=50)


class SearchIntakesArgs(ChatActionModel):
    query: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = Field(default=None, min_length=1, max_length=50)
    practice_area: str | None = Field(default=None, min_length=1, max_length=100)
    assigned_to_user_id: UUID | None = None
    limit: int = Field(default=25, ge=1, le=50)


class GetIntakeArgs(ChatActionModel):
    intake_id: UUID


class SearchMattersArgs(ChatActionModel):
    query: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = Field(default=None, min_length=1, max_length=100)
    practice_area: str | None = Field(default=None, min_length=1, max_length=200)
    include_closed: bool = False
    limit: int = Field(default=25, ge=1, le=50)


class SearchTasksArgs(ChatActionModel):
    query: str | None = Field(default=None, min_length=1, max_length=200)
    matter_id: UUID | None = None
    contact_id: UUID | None = None
    assigned_to_user_id: UUID | None = None
    status: str | None = Field(default=None, min_length=1, max_length=50)
    priority: str | None = Field(default=None, min_length=1, max_length=20)
    task_type: str | None = Field(default=None, min_length=1, max_length=50)
    due_before: date | None = None
    due_after: date | None = None
    limit: int = Field(default=25, ge=1, le=50)


class GetTaskArgs(ChatActionModel):
    task_id: UUID
    event_limit: int = Field(default=25, ge=1, le=100)


class GetDocumentTemplateTextArgs(ChatActionModel):
    matter_id: UUID
    template_id: UUID
    max_characters: int = Field(default=20_000, ge=100, le=50_000)


class ProposeDocumentFromTemplateArgs(ChatActionModel):
    """Render an approved template into cloud work awaiting staged review."""

    matter_id: UUID
    template_id: UUID
    variables: dict[str, str] = Field(default_factory=dict, max_length=200)
    client_request_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    due_date: date | None = None
    source_ids: list[str] = Field(default_factory=list, max_length=10)
    staff_reviewer_user_id: UUID | None = None
    attorney_reviewer_user_id: UUID | None = None

    @field_validator("variables")
    @classmethod
    def bound_variables(cls, value: dict[str, str]) -> dict[str, str]:
        total = 0
        normalized: dict[str, str] = {}
        for raw_name, raw_value in value.items():
            name = str(raw_name).strip()
            if not name or len(name) > 120:
                raise ValueError("Template variable names must be 1-120 characters")
            variable_value = str(raw_value)
            if len(variable_value) > 10_000:
                raise ValueError(
                    f"Template variable {name!r} exceeds 10,000 characters"
                )
            total += len(variable_value)
            normalized[name] = variable_value
        if total > 20_000:
            raise ValueError("Combined template variables exceed 20,000 characters")
        return normalized
