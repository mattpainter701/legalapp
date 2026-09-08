"""Named service grants and fixed schedules; no expressions or executable code."""

import uuid
from datetime import datetime, time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Unattended principals may read one rule's matter and prepare review work.
# Global client/intake search and outgoing communications are not service grants.
SERVICE_CAPABILITIES = frozenset(
    {
        "get_matter_context",
        "list_document_templates",
        "get_document_template_text",
        "get_matter_document_text",
        "list_matter_documents",
        "list_matter_tasks",
        "list_matter_recipients",
        "propose_task",
        "propose_matter_document",
        "propose_document_from_template",
    }
)


class ServiceIdentityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    capabilities: list[str] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def narrow_grant(self):
        self.name = self.name.strip()
        if not self.name or len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("A unique named capability grant is required")
        if not set(self.capabilities).issubset(SERVICE_CAPABILITIES):
            raise ValueError("This capability is unavailable to a service identity")
        return self


class ServiceSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["daily", "weekly", "workflow_event"]
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    local_time: str | None = Field(
        default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"
    )
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    event_rule_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def fixed_schedule(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Use a recognized IANA timezone") from error
        if self.kind == "workflow_event":
            if not self.event_rule_id or self.local_time or self.weekdays:
                raise ValueError(
                    "Event schedules require only an approved workflow rule"
                )
        else:
            if not self.local_time or self.event_rule_id:
                raise ValueError("Timed schedules require a local time")
            if self.kind == "weekly":
                if (
                    not self.weekdays
                    or len(set(self.weekdays)) != len(self.weekdays)
                    or any(day < 0 or day > 6 for day in self.weekdays)
                ):
                    raise ValueError(
                        "Choose distinct weekdays from Monday (0) to Sunday (6)"
                    )
            elif self.weekdays:
                raise ValueError("Daily schedules do not accept a weekday filter")
        return self


class ServiceRuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    identity_id: uuid.UUID
    source_run_id: uuid.UUID
    schedule: ServiceSchedule


def due_occurrence(schedule: ServiceSchedule, now: datetime, approved_at: datetime):
    """Return one local-date key; a gap fires after it and a fold cannot duplicate."""
    if now.tzinfo is None or approved_at.tzinfo is None:
        raise ValueError("Schedule timestamps must include timezones")
    if schedule.kind == "workflow_event":
        return None
    zone = ZoneInfo(schedule.timezone)
    local_now, local_approval = now.astimezone(zone), approved_at.astimezone(zone)
    if schedule.kind == "weekly" and local_now.weekday() not in schedule.weekdays:
        return None
    scheduled = time.fromisoformat(schedule.local_time)
    if local_now.time().replace(tzinfo=None) < scheduled:
        return None
    if local_now.date() < local_approval.date():
        return None
    if (
        local_now.date() == local_approval.date()
        and local_approval.time().replace(tzinfo=None) >= scheduled
    ):
        return None
    return f"date:{local_now.date().isoformat()}"
