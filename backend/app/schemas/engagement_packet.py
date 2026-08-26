"""Contracts for preparing a reviewable fee-agreement packet."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class PacketInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class PacketSigner(PacketInput):
    name: str = Field(min_length=1, max_length=300)
    email: EmailStr
    role: str = Field(default="client", min_length=1, max_length=100)


class PacketClient(PacketInput):
    name: str = Field(min_length=1, max_length=300)
    email: EmailStr


class PacketAttorney(PacketInput):
    name: str = Field(min_length=1, max_length=300)


class PacketCreate(PacketInput):
    idempotency_key: str = Field(min_length=1, max_length=200)
    template_id: uuid.UUID
    fee_amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    fee_structure: str = Field(min_length=1, max_length=500)
    scope_bullets: list[str] = Field(min_length=1, max_length=100)
    exclusions: list[str] = Field(default_factory=list, max_length=100)
    client: PacketClient
    attorney: PacketAttorney
    signers: list[PacketSigner] = Field(min_length=1, max_length=20)

    @field_validator("scope_bullets", "exclusions")
    @classmethod
    def clean_bullets(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned and value:
            raise ValueError("Scope entries cannot be blank")
        if any(len(item) > 2000 for item in cleaned):
            raise ValueError("Scope entries must be 2,000 characters or fewer")
        return cleaned


class PacketUpdate(PacketInput):
    expected_version: int = Field(ge=1)
    template_id: uuid.UUID | None = None
    fee_amount: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )
    fee_structure: str | None = Field(default=None, min_length=1, max_length=500)
    scope_bullets: list[str] | None = Field(default=None, max_length=100)
    exclusions: list[str] | None = Field(default=None, max_length=100)
    client: PacketClient | None = None
    attorney: PacketAttorney | None = None
    signers: list[PacketSigner] | None = Field(default=None, max_length=20)
    scope_wording: list[str] | None = Field(default=None, max_length=100)
    cover_email: str | None = Field(default=None, max_length=20_000)

    @field_validator("template_id")
    @classmethod
    def template_cannot_be_cleared(cls, value: uuid.UUID | None) -> uuid.UUID:
        if value is None:
            raise ValueError("template_id cannot be cleared")
        return value

    @field_validator("scope_bullets", "exclusions")
    @classmethod
    def clean_optional_bullets(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned and value:
            raise ValueError("Scope entries cannot be blank")
        if any(len(item) > 2000 for item in cleaned):
            raise ValueError("Scope entries must be 2,000 characters or fewer")
        return cleaned


class PacketApprove(BaseModel):
    expected_version: int = Field(ge=1)


class PacketResponse(BaseModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    prospect_id: uuid.UUID
    status: Literal["draft", "previewed", "approved"]
    template_id: uuid.UUID
    fields: dict[str, Any]
    provenance: dict[str, Any]
    unresolved_fields: list[str]
    preview: str | None = None
    version: int


class PacketApprovalResponse(PacketResponse):
    approved_at: str
