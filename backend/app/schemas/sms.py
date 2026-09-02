"""Strict API contracts for consented, provider-backed SMS."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


SmsMessageStatus = Literal[
    "queued",
    "dispatching",
    "provider_unknown",
    "blocked_number_suppression",
    "blocked_consent_changed",
    "blocked_quiet_hours",
    "blocked_provider_config",
    "blocked_matter_authorization_changed",
    "provider_failed",
    "provider_failed_after_acceptance",
    "submitted",
    "delivered",
    "received",
    "review_required",
    "route_rejected",
]
SmsDeliveryCertainty = Literal[
    "not_attempted",
    "outcome_unknown",
    "provider_rejected",
    "provider_accepted",
    "provider_failed_after_acceptance",
    "confirmed_sent",
    "confirmed_received",
]
SmsReviewStatus = Literal["pending", "resolved", "rejected"]
SmsProviderStatus = Literal[
    "queued",
    "accepted",
    "sending",
    "sent",
    "delivered",
    "read",
    "undelivered",
    "failed",
    "received",
]
SmsReconciliationResolution = Literal[
    "operator_attested_unknown",
    "provider_lookup",
    "signed_provider_callback",
    "signed_callback_overrode_operator_attestation",
]


class SmsComplianceSnapshot(BaseModel):
    ownership_model: Literal["firm-owned"]
    consent_policy: Literal["documented-opt-in"]
    quiet_hours_policy: Literal["recipient-timezone"]

    model_config = {"extra": "forbid"}


class SmsProviderConfigUpdate(BaseModel):
    account_sid: str = Field(min_length=3, max_length=100)
    auth_token: str = Field(min_length=8, max_length=500)
    messaging_service_sid: str | None = Field(default=None, max_length=100)
    from_number: str | None = Field(default=None, max_length=30)
    sender_ready: bool = False
    is_active: bool = False
    compliance_snapshot: SmsComplianceSnapshot | None = None

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
            snapshot = (
                self.compliance_snapshot.model_dump()
                if self.compliance_snapshot is not None
                else {}
            )
            missing = sorted(key for key in required if not snapshot.get(key))
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
    compliance_snapshot: SmsComplianceSnapshot | None
    generation: int

    @field_validator("compliance_snapshot", mode="before")
    @classmethod
    def empty_snapshot_is_unconfigured(cls, value):
        return None if value in ({}, None) else value

    model_config = {"from_attributes": True}


class SmsSendRequest(BaseModel):
    contact_id: uuid.UUID
    matter_id: uuid.UUID | None = None
    body: str = Field(min_length=1, max_length=1_600)
    category: str = Field(default="staff_authored", min_length=1, max_length=50)
    idempotency_key: str = Field(min_length=8, max_length=200)


class SmsMessageResponse(BaseModel):
    id: uuid.UUID
    contact_id: uuid.UUID | None
    matter_id: uuid.UUID | None
    status: SmsMessageStatus
    direction: Literal["inbound", "outbound"]
    delivery_certainty: SmsDeliveryCertainty
    provider_message_id: str | None
    provider_status: SmsProviderStatus | None
    provider_error_code: str | None
    provider_config_generation: int | None
    provider_submission_started_at: datetime | None
    provider_created_at: datetime | None
    from_number: str | None
    to_number: str | None
    body: str
    category: str
    reconciliation_required_at: datetime | None
    reconciliation_resolved_at: datetime | None
    reconciliation_resolution: SmsReconciliationResolution | None
    reconciliation_resolved_by_user_id: uuid.UUID | None
    operator_observed_absent_at: datetime | None
    operator_observed_absent_by_user_id: uuid.UUID | None
    last_event_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SmsRouteCandidate(BaseModel):
    id: uuid.UUID
    label: str


class SmsReviewResponse(BaseModel):
    id: uuid.UUID
    sms_message_id: uuid.UUID
    reason: str
    status: SmsReviewStatus
    candidate_contact_ids: list[uuid.UUID]
    candidate_matter_ids: list[uuid.UUID]
    candidate_contacts: list[SmsRouteCandidate] = Field(default_factory=list)
    candidate_matters: list[SmsRouteCandidate] = Field(default_factory=list)
    from_number: str | None = None
    body: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class SmsReviewDecision(BaseModel):
    decision: Literal["resolve", "reject"]
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
    resolution: Literal["operator_attested_unknown", "provider_lookup"]
    provider_message_id: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def provider_lookup_has_identity_when_needed(self):
        if (
            self.resolution == "operator_attested_unknown"
            and self.provider_message_id is not None
        ):
            raise ValueError(
                "An operator attestation cannot bind a provider message id"
            )
        if self.provider_message_id:
            self.provider_message_id = self.provider_message_id.strip()
        return self


class SmsReconciliationItemResponse(BaseModel):
    id: uuid.UUID
    contact_id: uuid.UUID | None
    matter_id: uuid.UUID | None
    status: SmsMessageStatus
    direction: Literal["outbound"]
    delivery_certainty: SmsDeliveryCertainty
    provider_message_id: str | None
    provider_status: SmsProviderStatus | None
    provider_error_code: str | None
    provider_submission_started_at: datetime | None
    provider_created_at: datetime | None
    from_number: str | None
    to_number: str | None
    body: str
    category: str
    reconciliation_required_at: datetime | None
    reconciliation_resolved_at: datetime | None
    reconciliation_resolution: SmsReconciliationResolution | None
    reconciliation_resolved_by_user_id: uuid.UUID | None
    operator_observed_absent_at: datetime | None
    operator_observed_absent_by_user_id: uuid.UUID | None
    last_event_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
