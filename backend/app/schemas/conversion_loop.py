import re
import uuid
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, model_validator


class IntakeFormCreate(BaseModel):
    slug: str = Field(
        min_length=3, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    name: str = Field(min_length=1, max_length=200)
    form_schema: dict[str, Any] = Field(default_factory=dict, alias="schema_json")
    is_active: bool = True

    model_config = {"populate_by_name": True}


class IntakeFormResponse(IntakeFormCreate):
    id: uuid.UUID
    tenant_id: uuid.UUID
    version: int
    created_at: datetime

    model_config = {"from_attributes": True}


class IntakeSubmissionCreate(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)
    attribution: dict[str, str] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=200)
    website: str | None = Field(default=None, max_length=200)
    email_consent: bool = False
    sms_consent: bool = False
    disclosure_version: str | None = Field(default=None, max_length=80)
    consent_language: str | None = Field(default=None, max_length=20)
    consent_timezone: str | None = Field(default=None, max_length=100)
    quiet_hours_start: str | None = Field(default=None, max_length=5)
    quiet_hours_end: str | None = Field(default=None, max_length=5)
    consent_expires_at: datetime | None = None

    @model_validator(mode="after")
    def sms_opt_in_carries_disclosure_provenance(self):
        if self.sms_consent and not str(self.disclosure_version or "").strip():
            raise ValueError("SMS consent requires a disclosure version")
        _validate_quiet_hours(
            timezone_name=self.consent_timezone,
            start=self.quiet_hours_start,
            end=self.quiet_hours_end,
            required=self.sms_consent,
        )
        if self.consent_expires_at and (
            self.consent_expires_at.tzinfo is None
            or self.consent_expires_at <= datetime.now(timezone.utc)
        ):
            raise ValueError("Consent expiry must be in the future")
        return self


class BookingCreate(BaseModel):
    lead_id: uuid.UUID
    start_at: datetime
    end_at: datetime
    timezone: str = Field(default="UTC", max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=200)


class ConsentUpdate(BaseModel):
    email_allowed: bool = False
    sms_allowed: bool = False
    phone_verified: bool = False
    disclosure_version: str = Field(min_length=1, max_length=80)
    mobile_e164: str | None = Field(default=None, max_length=30)
    consent_source: str = Field(default="staff_recorded", min_length=1, max_length=80)
    consent_language: str | None = Field(default=None, max_length=20)
    consent_timezone: str | None = Field(default=None, max_length=100)
    quiet_hours_start: str | None = Field(default=None, max_length=5)
    quiet_hours_end: str | None = Field(default=None, max_length=5)
    allowed_categories: list[str] = Field(default_factory=list, max_length=20)
    consent_expires_at: datetime | None = None

    @model_validator(mode="after")
    def active_sms_consent_is_complete(self):
        categories = list(
            dict.fromkeys(category.strip() for category in self.allowed_categories)
        )
        if any(not category or len(category) > 50 for category in categories):
            raise ValueError("SMS consent categories must be 1-50 characters")
        self.allowed_categories = categories
        _validate_quiet_hours(
            timezone_name=self.consent_timezone,
            start=self.quiet_hours_start,
            end=self.quiet_hours_end,
            required=self.sms_allowed,
        )
        if self.sms_allowed and (
            not self.phone_verified or not self.mobile_e164 or not categories
        ):
            raise ValueError(
                "Active SMS consent requires a verified mobile and allowed categories"
            )
        if self.consent_expires_at and (
            self.consent_expires_at.tzinfo is None
            or self.consent_expires_at <= datetime.now(timezone.utc)
        ):
            raise ValueError("Consent expiry must be in the future")
        return self


def _validate_quiet_hours(
    *, timezone_name: str | None, start: str | None, end: str | None, required: bool
) -> None:
    if required and (not timezone_name or not start or not end):
        raise ValueError("Active SMS consent requires timezone-aware quiet hours")
    if bool(start) != bool(end):
        raise ValueError("Quiet hours require both a start and end")
    for value in (start, end):
        if value and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("Quiet hours must use HH:MM in 24-hour time")
    if start and end and start == end:
        raise ValueError("Quiet hours start and end must be different")
    if timezone_name:
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Consent timezone must be an IANA timezone") from exc


class TriageDecision(BaseModel):
    decision: str = Field(pattern=r"^(clear|hold|decline)$")
    note: str | None = Field(default=None, max_length=2000)


class FollowUpCreate(BaseModel):
    channel: str = Field(pattern=r"^(email|sms)$")
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)
    idempotency_key: str = Field(min_length=8, max_length=200)
