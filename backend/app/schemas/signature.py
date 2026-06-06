"""Pydantic schemas for native e-signature endpoints."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


# ── Firm-side: create ───────────────────────────────────────────────────────


class SignerCreate(BaseModel):
    name: str
    email: str
    contact_id: str | None = None
    sign_order: int = 0


class SignatureRequestCreate(BaseModel):
    document_id: str
    signers: list[SignerCreate]
    provider: str = "internal"


# ── Responses ───────────────────────────────────────────────────────────────


class SignerResponse(BaseModel):
    id: str
    name: str
    email: str
    sign_order: int
    status: str
    signed_at: datetime | None = None

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
    created_at: datetime
    signers: list[SignerResponse] = []
    signed_document_id: str | None = None

    model_config = ConfigDict(from_attributes=True)


# ── Client portal: sign ─────────────────────────────────────────────────────


class PortalSignRequest(BaseModel):
    typed_signature: str
    signer_id: str | None = None  # optional; defaults to next pending signer
