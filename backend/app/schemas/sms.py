"""Strict API contracts for consented, provider-backed SMS."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class SmsProviderConfigUpdate(BaseModel):
    account_sid: str = Field(min_length=3, max_length=100)
    auth_token: str = Field(min_length=8, max_length=500)
    webhook_secret: str = Field(min_length=8, max_length=500)
    messaging_service_sid: str | None = Field(default=None, max_length=100)
    from_number: str | None = Field(default=None, max_length=30)
    sender_ready: bool = False
    is_active: bool = False
    compliance_snapshot: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def active_config_has_compliance_evidence(self):
        if self.sender_ready or self.is_active:
            required = {"ownership_model", "consent_policy", "quiet_hours_policy"}
            missing = sorted(required - set(self.compliance_snapshot))
            if missing:
                raise ValueError(
                    "Active SMS configuration requires compliance evidence: "
                    + ", ".join(missing)
                )
        return self


class SmsProviderConfigResponse(BaseModel):
    provider: str
    account_sid: str | None
    messaging_service_sid: str | None
    from_number: str | None
    sender_ready: bool
    is_active: bool
    compliance_snapshot: dict


class SmsSendRequest(BaseModel):
    contact_id: uuid.UUID
    matter_id: uuid.UUID | None = None
    body: str = Field(min_length=1, max_length=1_600)
    category: str = Field(default="staff_authored", min_length=1, max_length=50)
    idempotency_key: str = Field(min_length=8, max_length=200)


class SmsMessageResponse(BaseModel):
    id: uuid.UUID
    status: str
    direction: str
    provider_message_id: str | None
    provider_status: str | None
    to_number: str | None
    body: str
    category: str
    created_at: datetime

    model_config = {"from_attributes": True}


class SmsReviewResponse(BaseModel):
    id: uuid.UUID
    sms_message_id: uuid.UUID
    reason: str
    status: str
    candidate_contact_ids: list
    candidate_matter_ids: list

    model_config = {"from_attributes": True}
