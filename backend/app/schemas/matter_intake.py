import uuid
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, EmailStr, Field, model_validator


class IntakeQuestion(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")
    label: str = Field(min_length=1, max_length=500)
    required: bool = True


class IntakeStart(BaseModel):
    owner_id: uuid.UUID | None = None
    email: EmailStr
    channels: list[Literal["email", "sms"]] = Field(min_length=1, max_length=2)
    timezone: str = "America/Chicago"
    sms_permission_verified: bool = False
    questions: list[IntakeQuestion] = Field(min_length=1, max_length=50)
    confirm_send: Literal[True]

    @model_validator(mode="after")
    def valid(self):
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Choose a valid client timezone") from exc
        if len({q.key for q in self.questions}) != len(self.questions):
            raise ValueError("Question keys must be unique")
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("Choose each delivery channel once")
        return self


class IntakeAnswers(BaseModel):
    answers: dict[str, str] = Field(max_length=50)
    confirm_complete: Literal[True]


class IntakeReceipt(BaseModel):
    requirement: Literal["fee_agreement", "questionnaire"]
    document_id: uuid.UUID
    note: str = Field(min_length=1, max_length=1000)


class IntakeMeeting(BaseModel):
    kind: Literal["conference_call", "in_person"]
    starts_at: datetime
    details: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def aware(self):
        if self.starts_at.tzinfo is None:
            raise ValueError("Meeting time requires a timezone")
        return self


class IntakeRetry(BaseModel):
    delivery_key: str = Field(max_length=100)
    confirm_not_sent: Literal[True]
