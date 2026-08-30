"""Operator publishing and tenant compliance visibility."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.compliance import AgreementDefinition
from app.models.tenant import Tenant
from app.routers.platform import _platform_tenant_scope, _require_platform_key
from app.services.compliance import agreement_status, retention_inventory

router = APIRouter(prefix="/platform", tags=["platform-compliance"])


class PublishAgreementRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")
    version: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=255)
    document_url: HttpUrl
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_for_onboarding: bool = True
    effective_at: datetime
    expires_at: datetime | None = None

    @field_validator("version", "title")
    @classmethod
    def non_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("document_url")
    @classmethod
    def https_only(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("document_url must use HTTPS")
        return value

    @field_validator("content_hash")
    @classmethod
    def real_hash(cls, value: str) -> str:
        if set(value) == {"0"}:
            raise ValueError("content_hash must identify the published document")
        return value

    @field_validator("effective_at", "expires_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("agreement timestamps must include a UTC offset")
        return value

    @model_validator(mode="after")
    def valid_window(self):
        if self.expires_at and self.expires_at <= self.effective_at:
            raise ValueError("expires_at must be after effective_at")
        return self


def _definition_payload(definition: AgreementDefinition) -> dict:
    return {
        "id": str(definition.id),
        "kind": definition.kind,
        "version": definition.version,
        "title": definition.title,
        "document_url": definition.document_url,
        "content_hash": definition.content_hash,
        "required_for_onboarding": definition.required_for_onboarding,
        "effective_at": definition.effective_at,
        "expires_at": definition.expires_at,
        "counsel_owned": definition.counsel_owned,
        "published_by_actor_id": definition.published_by_actor_id,
        "created_at": definition.created_at,
    }


@router.get("/agreements")
async def list_agreement_definitions(
    request: Request, db: AsyncSession = Depends(get_db)
):
    _require_platform_key(request)
    rows = list(
        (
            await db.scalars(
                select(AgreementDefinition).order_by(
                    AgreementDefinition.kind,
                    AgreementDefinition.effective_at.desc(),
                    AgreementDefinition.created_at.desc(),
                )
            )
        ).all()
    )
    return {"agreements": [_definition_payload(row) for row in rows]}


@router.post("/agreements", status_code=201)
async def publish_agreement_definition(
    body: PublishAgreementRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    principal = _require_platform_key(request)
    definition = AgreementDefinition(
        kind=body.kind,
        version=body.version,
        title=body.title,
        document_url=str(body.document_url),
        content_hash=body.content_hash,
        required_for_onboarding=body.required_for_onboarding,
        effective_at=body.effective_at.astimezone(timezone.utc),
        expires_at=(
            body.expires_at.astimezone(timezone.utc) if body.expires_at else None
        ),
        counsel_owned=True,
        published_by_actor_id=principal.actor_id,
    )
    db.add(definition)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="That agreement kind and version is already published",
        ) from exc
    await db.refresh(definition)
    return _definition_payload(definition)


@router.get("/tenants/{tenant_id}/compliance")
async def tenant_compliance(
    tenant_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_platform_key(request)
    if not await db.scalar(select(Tenant.id).where(Tenant.id == tenant_id)):
        raise HTTPException(status_code=404, detail="Tenant not found")
    async with _platform_tenant_scope(db, tenant_id):
        return {
            "tenant_id": str(tenant_id),
            "agreements": await agreement_status(db, tenant_id),
            "retention": await retention_inventory(db, tenant_id),
        }
