"""Pydantic schemas for tasks and deadlines."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    task_type: str = "general"
    priority: str = "medium"
    due_date: Optional[date] = None
    due_time: Optional[time] = None
    matter_id: Optional[uuid.UUID] = None
    contact_id: Optional[uuid.UUID] = None
    assigned_to_user_id: Optional[uuid.UUID] = None

    @field_validator("task_type")
    @classmethod
    def validate_task_type(cls, v: str) -> str:
        allowed = {
            "deadline",
            "hearing",
            "filing",
            "deposition",
            "call",
            "follow_up",
            "review",
            "general",
        }
        if v not in allowed:
            raise ValueError(f"task_type must be one of {allowed}")
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        allowed = {"low", "medium", "high", "urgent"}
        if v not in allowed:
            raise ValueError(f"priority must be one of {allowed}")
        return v


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    task_type: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[date] = None
    due_time: Optional[time] = None
    matter_id: Optional[uuid.UUID] = None
    contact_id: Optional[uuid.UUID] = None
    assigned_to_user_id: Optional[uuid.UUID] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {"pending", "in_progress", "completed", "cancelled"}
        if v not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    title: str
    description: Optional[str]
    task_type: str
    status: str
    priority: str
    due_date: Optional[date]
    due_time: Optional[time]
    matter_id: Optional[uuid.UUID]
    contact_id: Optional[uuid.UUID]
    assigned_to_user_id: Optional[uuid.UUID]
    created_by_user_id: Optional[uuid.UUID]
    completed_at: Optional[datetime]
    source: str
    external_ref: Optional[str]
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[TaskResponse]
    total: int
