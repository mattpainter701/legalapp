"""Schemas for the deadline calendar endpoint."""

from pydantic import BaseModel
from datetime import date
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


class CalendarEventsResponse(BaseModel):
    events: list[CalendarEvent]
    total: int
