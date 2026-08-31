"""Strict API contracts for consented, provider-backed SMS."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class SmsProviderConfigUpdate(BaseModel):
    account_sid: str = Field(min_length=3, max_length=100)
    auth_token: str = Field(min_length=8, max_length=500)
    messaging_service_sid: str | None = Field(default=None, max_length=100)
    from_number: str | None = Field(default=None, max_length=30)
    sender_ready: bool = False
    is_active: bool = False
    compliance_snapshot: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def active_config_has_compliance_evidence(self):
        self.account_sid = self.account_sid.strip()
        self.auth_token = self.auth_token.strip()
        self.messaging_service_sid = (
            self.messaging_service_sid.strip() if self.messaging_service_sid else None
        )
        self.from_number = self.from_number.strip() if self.from_number else None
        if not self.account_sid or not self.auth_token:
            raise ValueError("SMS provider credentials cannot be blank")
        if self.is_active and not self.sender_ready:
            raise ValueError("Active SMS configuration requires a ready sender")
        if self.sender_ready or self.is_active:
            required = {"ownership_model", "consent_policy", "quiet_hours_policy"}
            missing = sorted(
                key
                for key in required
                if not str(self.compliance_snapshot.get(key) or "").strip()
            )
            if missing:
                raise ValueError(
                    "Active SMS configuration requires compliance evidence: "
                    + ", ".join(missing)
                )
            if not self.messaging_service_sid and not self.from_number:
                raise ValueError(
                    "Active SMS configuration requires a messaging service or sender"
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
    from_number: str | None = None
    body: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class SmsReviewDecision(BaseModel):
    decision: str = Field(pattern=r"^(resolve|reject)$")
    contact_id: uuid.UUID | None = None
    matter_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def resolved_route_is_complete(self):
        if self.decision == "resolve" and (not self.contact_id or not self.matter_id):
            raise ValueError("Resolution requires one contact and matter")
        if self.decision == "reject" and (self.contact_id or self.matter_id):
            raise ValueError("Rejected inbound messages cannot be routed")
        return self


class SmsReconciliationRequest(BaseModel):
    resolution: str = Field(pattern=r"^(confirmed_not_sent|provider_accepted)$")
    provider_message_id: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def provider_acceptance_has_identity(self):
        if (
            self.resolution == "provider_accepted"
            and not str(self.provider_message_id or "").strip()
        ):
            raise ValueError("Provider acceptance requires the provider message id")
        if self.provider_message_id:
            self.provider_message_id = self.provider_message_id.strip()
        return self
