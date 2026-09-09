import uuid
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, EmailStr, Field, model_validator


class IntakeQuestion(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")
    label: str = Field(min_length=1, max_length=500)
    required: bool = True


class IntakeDocumentSelection(BaseModel):
    document_id: uuid.UUID
    label: str = Field(min_length=1, max_length=200)
    requires_signature: bool = True


class IntakeUploadRequirement(BaseModel):
    key: str = Field(pattern=r"^upload_[a-z0-9_]{1,40}$")
    label: str = Field(min_length=1, max_length=200)
    required: bool = True


class IntakeStart(BaseModel):
    agreement_document_id: uuid.UUID | None = None
    selected_documents: list[IntakeDocumentSelection] = Field(
        default_factory=list, max_length=10
    )
    upload_requirements: list[IntakeUploadRequirement] = Field(
        default_factory=list, max_length=30
    )
    include_questionnaire: bool = True
    portal_after_signing: bool = False
    owner_id: uuid.UUID | None = None
    email: EmailStr
    channels: list[Literal["email", "sms"]] = Field(min_length=1, max_length=2)
    timezone: str = "America/Chicago"
    sms_permission_verified: bool = False
    questions: list[IntakeQuestion] = Field(default_factory=list, max_length=50)
    confirm_send: Literal[True]

    @model_validator(mode="after")
    def valid(self):
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Choose a valid client timezone") from exc
        if self.include_questionnaire and not self.questions:
            raise ValueError("Add at least one questionnaire question")
        if len({q.key for q in self.questions}) != len(self.questions):
            raise ValueError("Question keys must be unique")
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("Choose each delivery channel once")
        if len({item.document_id for item in self.selected_documents}) != len(
            self.selected_documents
        ):
            raise ValueError("Choose each document once")
        if self.agreement_document_id in {
            item.document_id for item in self.selected_documents
        }:
            raise ValueError("The fee agreement is already selected")
        if len({item.key for item in self.upload_requirements}) != len(
            self.upload_requirements
        ):
            raise ValueError("Upload requirements must have unique keys")
        return self


class IntakeSubmission(BaseModel):
    document_id: uuid.UUID


class IntakeAnswers(BaseModel):
    answers: dict[str, str] = Field(max_length=50)
    confirm_complete: Literal[True]


class IntakeReceipt(BaseModel):
    requirement: str = Field(pattern=r"^[a-z][a-z0-9_]{0,80}$")
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
