"""Pydantic schemas for tasks and deadlines."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


TASK_STATUSES = {
    "pending",
    "in_progress",
    "waiting",
    "review",
    "completed",
    "cancelled",
}
OPEN_TASK_STATUSES = {"pending", "in_progress", "waiting", "review"}
BOARD_TASK_STATUSES = ("pending", "in_progress", "waiting", "review", "completed")
BOARD_STATUS_LABELS = {
    "pending": "To Do",
    "in_progress": "In Progress",
    "waiting": "Waiting",
    "review": "Review",
    "completed": "Done",
}


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
    # Personal message from the assigner, included in the assignment email and
    # appended to the task description. Not a Task column.
    assignment_note: Optional[str] = None

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
            "intake",
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


class PendingActionEdit(BaseModel):
    """The only parts of a drafted action an attorney may rewrite.

    Recipients are absent on purpose. They were resolved server-side from the
    matter's own parties, and letting them be re-supplied here would reopen the
    hole that resolution closes — an edit endpoint is just as reachable by a
    confused or malicious caller as a tool argument is.
    """

    subject: Optional[str] = Field(None, min_length=1, max_length=300)
    body: Optional[str] = Field(None, min_length=1, max_length=20_000)
    expected_version: Optional[int] = Field(None, ge=1)


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
    # Personal message from the assigner when (re)assigning. Not a Task column.
    assignment_note: Optional[str] = None
    # Required when cancelling; optional context when completing.
    closed_reason: Optional[str] = None
    waiting_reason: Optional[str] = None
    waiting_follow_up_date: Optional[date] = None
    reviewer_user_id: Optional[uuid.UUID] = None
    expected_version: Optional[int] = Field(None, ge=1)

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in TASK_STATUSES:
            raise ValueError(f"status must be one of {TASK_STATUSES}")
        return v


class TaskTransitionRequest(BaseModel):
    to_status: str
    expected_version: int = Field(ge=1)
    reason: Optional[str] = Field(None, max_length=5000)
    waiting_follow_up_date: Optional[date] = None
    reviewer_user_id: Optional[uuid.UUID] = None

    @field_validator("to_status")
    @classmethod
    def validate_to_status(cls, v: str) -> str:
        if v not in TASK_STATUSES:
            raise ValueError(f"to_status must be one of {TASK_STATUSES}")
        return v

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() or None if v is not None else None

    @model_validator(mode="after")
    def validate_transition_metadata(self):
        if self.to_status == "waiting" and not self.reason:
            raise ValueError("A waiting reason is required")
        if self.to_status == "cancelled" and not self.reason:
            raise ValueError("A cancellation reason is required")
        if self.waiting_follow_up_date and self.to_status != "waiting":
            raise ValueError("waiting_follow_up_date is only valid for Waiting")
        if self.reviewer_user_id and self.to_status != "review":
            raise ValueError("reviewer_user_id is only valid for Review")
        return self


class TaskContactedRequest(BaseModel):
    method: str = "call"
    note: Optional[str] = None

    @field_validator("method")
    @classmethod
    def validate_method(cls, v: str) -> str:
        allowed = {"call", "email", "sms", "meeting", "other"}
        if v not in allowed:
            raise ValueError(f"method must be one of {allowed}")
        return v


class IntakeTaskQualifyRequest(BaseModel):
    assigned_to_user_id: uuid.UUID
    partner_notes: Optional[str] = None
    case_description: Optional[str] = None
    estimated_value: Optional[float] = None


class IntakeTaskQualifyResponse(BaseModel):
    lead_id: uuid.UUID
    contact_id: uuid.UUID
    partner_task_id: uuid.UUID
    attorney_task_id: uuid.UUID
    assigned_to_user_id: uuid.UUID
    lead_status: str


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
    viewed_at: Optional[datetime] = None
    customer_contacted_at: Optional[datetime] = None
    customer_contact_method: Optional[str] = None
    closed_reason: Optional[str] = None
    closed_by_user_id: Optional[uuid.UUID] = None
    status_changed_at: datetime
    waiting_reason: Optional[str] = None
    waiting_follow_up_date: Optional[date] = None
    reviewer_user_id: Optional[uuid.UUID] = None
    version: int = 1
    source: str
    external_ref: Optional[str]
    # Drafted follow-through awaiting approval, e.g. an email_client payload.
    # Present so the board can state what approving will actually do.
    pending_action: Optional[dict] = None
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[TaskResponse]
    total: int


class TaskCardPerson(BaseModel):
    id: uuid.UUID
    label: str


class TaskCardMatter(BaseModel):
    id: uuid.UUID
    label: str
    case_number: Optional[str] = None


class TaskBoardCard(BaseModel):
    id: uuid.UUID
    title: str
    task_type: str
    status: str
    priority: str
    due_date: Optional[date] = None
    due_time: Optional[time] = None
    matter_id: Optional[uuid.UUID] = None
    contact_id: Optional[uuid.UUID] = None
    assigned_to_user_id: Optional[uuid.UUID] = None
    created_by_user_id: Optional[uuid.UUID] = None
    reviewer_user_id: Optional[uuid.UUID] = None
    matter: Optional[TaskCardMatter] = None
    assignee: Optional[TaskCardPerson] = None
    reviewer: Optional[TaskCardPerson] = None
    viewed_at: Optional[datetime] = None
    customer_contacted_at: Optional[datetime] = None
    customer_contact_method: Optional[str] = None
    waiting_reason: Optional[str] = None
    waiting_follow_up_date: Optional[date] = None
    completed_at: Optional[datetime] = None
    closed_reason: Optional[str] = None
    source: str
    external_ref: Optional[str] = None
    version: int
    # Drafted follow-through this card will execute when approved out of Review.
    # The board must be able to say what approving does before it is clicked.
    pending_action: Optional[dict] = None
    status_changed_at: datetime
    updated_at: datetime


class TaskBoardColumn(BaseModel):
    status: str
    label: str
    total: int
    items: list[TaskBoardCard]
    next_cursor: Optional[str] = None


class TaskBoardRiskCounts(BaseModel):
    overdue: int = 0
    due_today: int = 0
    unassigned: int = 0
    waiting_follow_up_due: int = 0


class TaskBoardResponse(BaseModel):
    columns: list[TaskBoardColumn]
    risk_counts: TaskBoardRiskCounts
    scope: Literal["mine", "firm"]
    generated_at: datetime


class TaskBoardConfig(BaseModel):
    enabled: bool
    statuses: dict[str, str]
    default_completed_days: int = 14


class TaskBoardTelemetryRequest(BaseModel):
    event: Literal["board_selected", "list_selected"]
    scope: Optional[Literal["mine", "firm"]] = None


class TaskEventResponse(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    event_type: str
    actor_user_id: Optional[uuid.UUID] = None
    actor_label: Optional[str] = None
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    note: Optional[str] = None
    metadata_json: dict = Field(default_factory=dict)
    created_at: datetime


class TaskEventListResponse(BaseModel):
    items: list[TaskEventResponse]
    total: int
