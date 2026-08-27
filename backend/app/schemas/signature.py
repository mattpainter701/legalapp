"""Pydantic schemas for native e-signature endpoints."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ── Firm-side: create ───────────────────────────────────────────────────────


class SignerCreate(BaseModel):
    name: str
    email: str
    contact_id: str | None = None
    role: str | None = "signer"
    sign_order: int = 0


class SignatureRequestCreate(BaseModel):
    document_id: str
    signers: list[SignerCreate]
    provider: str = "internal"
    expires_at: datetime | None = None
    reminders: dict | None = None
    reminder_days: list[int] = Field(default_factory=list)
    enforce_signing_order: bool = False


class SignatureRequestVoid(BaseModel):
    reason: str | None = None


# ── Responses ───────────────────────────────────────────────────────────────


class SignerResponse(BaseModel):
    id: str
    name: str
    email: str
    role: str = "signer"
    sign_order: int
    status: str
    signed_at: datetime | None = None
    declined_at: datetime | None = None
    decline_reason: str | None = None
    invitation_delivery_status: str | None = None
    invitation_sent_at: datetime | None = None
    reminder_delivery_status: str | None = None
    last_reminder_at: datetime | None = None
    viewed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class SignatureRequestResponse(BaseModel):
    id: str
    matter_id: str
    document_id: str | None = None
    document_name: str | None = None
    status: str
    provider: str
    sent_at: datetime | None = None
    completed_at: datetime | None = None
    expires_at: datetime | None = None
    reminders: dict | None = None
    enforce_signing_order: bool = False
    declined_at: datetime | None = None
    decline_reason: str | None = None
    voided_at: datetime | None = None
    void_reason: str | None = None
    created_at: datetime
    signers: list[SignerResponse] = []
    signed_document_id: str | None = None
    completion_artifact_id: str | None = None
    artifact_type: str = "signature_acknowledgment_certificate"
    source_document_sha256: str | None = None
    completion_artifact_sha256: str | None = None
    evidence_sha256: str | None = None

    model_config = ConfigDict(from_attributes=True)


# ── Client portal: sign ─────────────────────────────────────────────────────


class PortalSignRequest(BaseModel):
    typed_signature: str
    signer_id: str | None = None  # optional; defaults to next pending signer
    consent_to_electronic_signature: bool = False
    consent_text_version: Literal["clarity-esign-consent-v1"] = (
        "clarity-esign-consent-v1"
    )


class PortalDeclineRequest(BaseModel):
    reason: str | None = None
    signer_id: str | None = None
