"""Schemas for the deadline calendar endpoint."""

from pydantic import BaseModel, field_validator
from datetime import date, datetime
from typing import Optional
import uuid


class CalendarEvent(BaseModel):
    id: str  # "task-{uuid}" | "matter-{uuid}-{key}" | "renewal-{uuid}"
    title: str
    date: date
    event_type: str  # "task_due" | "matter_key_date" | "renewal"
    matter_id: Optional[uuid.UUID] = None
    matter_name: Optional[str] = None
    task_id: Optional[uuid.UUID] = None
    url: Optional[str] = None  # frontend nav target
    is_completed: bool = False  # task is done → show with checkmark/strikethrough
    start: Optional[str] = None
    end: Optional[str] = None
    calendar_provider: Optional[str] = None
    meeting_provider: Optional[str] = None
    join_url: Optional[str] = None
    location: Optional[str] = None


class CalendarEventsResponse(BaseModel):
    events: list[CalendarEvent]
    total: int


class ExternalCalendarEventResponse(BaseModel):
    id: str
    provider: str
    subject: str | None = None
    start: str | None = None
    end: str | None = None
    location: str | None = None


class CalendarSyncRequest(BaseModel):
    provider: str = "microsoft"
    user_id: str | None = None
    sync_deadlines: bool = False


class CalendarSyncResponse(BaseModel):
    provider: str
    events: list[ExternalCalendarEventResponse]
    deadlines_created: int = 0


class ScheduledEventCreate(BaseModel):
    title: str
    description: str | None = None
    start_at: datetime
    end_at: datetime
    timezone: str = "UTC"
    attendees: list[str] = []
    matter_id: uuid.UUID | None = None
    calendar_provider: str | None = None
    meeting_provider: str = "none"

    @field_validator("calendar_provider")
    @classmethod
    def validate_calendar_provider(cls, value: str | None) -> str | None:
        if value in (None, "", "none"):
            return None
        if value not in {"microsoft", "google"}:
            raise ValueError("calendar_provider must be microsoft, google, or none")
        return value

    @field_validator("meeting_provider")
    @classmethod
    def validate_meeting_provider(cls, value: str) -> str:
        value = value or "none"
        if value not in {"none", "teams", "zoom"}:
            raise ValueError("meeting_provider must be none, teams, or zoom")
        return value


class ScheduledEventUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    timezone: str | None = None
    attendees: list[str] | None = None
    matter_id: uuid.UUID | None = None
    calendar_provider: str | None = None
    meeting_provider: str | None = None

    @field_validator("calendar_provider")
    @classmethod
    def validate_calendar_provider(cls, value: str | None) -> str | None:
        if value in (None, "", "none"):
            return None
        if value not in {"microsoft", "google"}:
            raise ValueError("calendar_provider must be microsoft, google, or none")
        return value

    @field_validator("meeting_provider")
    @classmethod
    def validate_meeting_provider(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return value
        if value not in {"none", "teams", "zoom"}:
            raise ValueError("meeting_provider must be none, teams, or zoom")
        return value


class ScheduledEventResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None = None
    start_at: datetime
    end_at: datetime
    timezone: str
    attendees: list[str] = []
    matter_id: uuid.UUID | None = None
    created_by_user_id: uuid.UUID | None = None
    calendar_provider: str | None = None
    meeting_provider: str
    external_calendar_event_id: str | None = None
    external_calendar_url: str | None = None
    meeting_id: str | None = None
    join_url: str | None = None
    sync_status: str
    sync_error: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
