"""Tenant agreement evidence and narrowly scoped retention controls.

Agreement text is counsel-owned and published separately. This API records an
immutable identity for that text plus acceptance evidence. Retention execution
is limited to expired, non-matter chat attachments.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.rate_limit import _client_ip
from app.middleware.tenant import get_current_user, require_admin
from app.models.compliance import (
    RetentionAction,
    RetentionPolicy,
    TenantAgreementAcceptance,
)
from app.models.tenant import Tenant
from app.services.compliance import (
    CHAT_ATTACHMENTS_POLICY_KEY,
    agreement_status,
    authenticated_request_method,
    bounded_user_agent,
    current_agreement_definitions,
    execute_chat_attachment_retention,
    lock_tenant_for_retention,
    reschedule_chat_attachments,
    retention_inventory,
)

router = APIRouter(prefix="/api/compliance", tags=["compliance"])

AUTHORITY_ATTESTATION = (
    "I confirm that I am authorized to bind this organization to the "
    "identified agreement."
)


class AcceptanceRequest(BaseModel):
    expected_version: str = Field(min_length=1, max_length=40)
    expected_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    signer_name: str = Field(min_length=1, max_length=255)
    signer_title: str = Field(min_length=1, max_length=255)
    authority_attested: bool
    esign_provider: str | None = Field(default=None, max_length=80)
    esign_envelope_id: str | None = Field(default=None, max_length=255)
    evidence_reference: str | None = Field(default=None, max_length=2000)

    @field_validator("expected_version", "signer_name", "signer_title")
    @classmethod
    def non_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class RetentionPolicyRequest(BaseModel):
    chat_attachments_days: int = Field(ge=1, le=365)
    legal_hold: bool = False
    legal_hold_reason: str | None = Field(default=None, max_length=2000)

    @field_validator("legal_hold_reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        normalized = (value or "").strip()
        return normalized or None


async def _tenant_user(request: Request, db: AsyncSession):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    return user


@router.get("/agreements")
async def list_agreements(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _tenant_user(request, db)
    return await agreement_status(db, user.tenant_id)


@router.post("/agreements/{kind}/accept")
async def accept_agreement(
    kind: str,
    body: AcceptanceRequest,
    request: Request,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(admin.tenant_id))
    definitions = await current_agreement_definitions(db)
    definition = next((item for item in definitions if item.kind == kind), None)
    if definition is None:
        raise HTTPException(status_code=404, detail="Current agreement not found")
    if (
        definition.version != body.expected_version
        or definition.content_hash != body.expected_content_hash
    ):
        raise HTTPException(
            status_code=409,
            detail="The agreement changed. Review the current version before accepting.",
        )
    if not body.authority_attested:
        raise HTTPException(
            status_code=400,
            detail="An authorized tenant representative must attest authority",
        )

    existing = await db.scalar(
        select(TenantAgreementAcceptance).where(
            TenantAgreementAcceptance.tenant_id == admin.tenant_id,
            TenantAgreementAcceptance.agreement_definition_id == definition.id,
        )
    )
    if existing is not None:
        return await agreement_status(db, admin.tenant_id)

    tenant = await db.scalar(select(Tenant).where(Tenant.id == admin.tenant_id))
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    now = datetime.now(timezone.utc)
    db.add(
        TenantAgreementAcceptance(
            tenant_id=admin.tenant_id,
            agreement_definition_id=definition.id,
            tenant_name=tenant.name,
            document_kind=definition.kind,
            document_version=definition.version,
            document_hash=definition.content_hash,
            document_url=definition.document_url,
            signer_user_id=admin.id,
            signer_name=body.signer_name,
            signer_email=admin.email,
            signer_title=body.signer_title,
            authority_attested=True,
            attestation_text=AUTHORITY_ATTESTATION,
            accepted_at=now,
            ip_address=(_client_ip(request) or "")[:45] or None,
            user_agent=bounded_user_agent(request.headers.get("user-agent")),
            auth_method=authenticated_request_method(request),
            status="accepted",
            effective_at=definition.effective_at,
            expires_at=definition.expires_at,
            esign_provider=(body.esign_provider or "").strip() or None,
            esign_envelope_id=(body.esign_envelope_id or "").strip() or None,
            evidence_reference=(body.evidence_reference or "").strip() or None,
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="This agreement has already been accepted"
        ) from exc
    await set_tenant_context(db, str(admin.tenant_id))
    return await agreement_status(db, admin.tenant_id)


@router.get("/retention")
async def get_retention_inventory(
    admin=Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    await set_tenant_context(db, str(admin.tenant_id))
    return await retention_inventory(db, admin.tenant_id)


@router.put("/retention")
async def update_retention_policy(
    body: RetentionPolicyRequest,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if body.legal_hold and not body.legal_hold_reason:
        raise HTTPException(
            status_code=400, detail="A reason is required to place a legal hold"
        )

    await set_tenant_context(db, str(admin.tenant_id))
    await lock_tenant_for_retention(db, admin.tenant_id)
    policy = await db.scalar(
        select(RetentionPolicy)
        .where(RetentionPolicy.tenant_id == admin.tenant_id)
        .with_for_update()
    )
    before = {
        "legal_hold": bool(policy and policy.legal_hold),
        "legal_hold_reason": policy.legal_hold_reason if policy else None,
        CHAT_ATTACHMENTS_POLICY_KEY: (
            (policy.policy_json or {}).get(CHAT_ATTACHMENTS_POLICY_KEY)
            if policy
            else None
        ),
        "version": policy.version if policy else 0,
    }
    if policy is None:
        policy = RetentionPolicy(tenant_id=admin.tenant_id, version=1)
        db.add(policy)
    else:
        policy.version += 1

    now = datetime.now(timezone.utc)
    policy.legal_hold = body.legal_hold
    policy.legal_hold_reason = body.legal_hold_reason if body.legal_hold else None
    if body.legal_hold and not before["legal_hold"]:
        policy.legal_hold_set_at = now
    elif not body.legal_hold:
        policy.legal_hold_set_at = None
    policy.policy_json = {
        CHAT_ATTACHMENTS_POLICY_KEY: body.chat_attachments_days,
    }
    policy.updated_by_user_id = admin.id
    policy.updated_at = now
    rescheduled = await reschedule_chat_attachments(
        db, admin.tenant_id, days=body.chat_attachments_days
    )
    after = {
        "legal_hold": policy.legal_hold,
        "legal_hold_reason": policy.legal_hold_reason,
        CHAT_ATTACHMENTS_POLICY_KEY: body.chat_attachments_days,
        "version": policy.version,
    }
    db.add(
        RetentionAction(
            tenant_id=admin.tenant_id,
            actor_user_id=admin.id,
            actor_type="user",
            action="policy_updated",
            status="completed",
            dry_run=False,
            legal_hold_at_execution=policy.legal_hold,
            policy_version=policy.version,
            result_json={
                "before": before,
                "after": after,
                "rescheduled_chat_attachments": rescheduled,
            },
        )
    )
    await db.commit()
    await set_tenant_context(db, str(admin.tenant_id))
    return await retention_inventory(db, admin.tenant_id)


@router.post("/retention/execute")
async def execute_retention(
    dry_run: bool = True,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(admin.tenant_id))
    result = await execute_chat_attachment_retention(
        db,
        admin.tenant_id,
        dry_run=dry_run,
        actor_user_id=admin.id,
        actor_type="user",
    )
    if not dry_run and result["protected"] == "legal_hold":
        raise HTTPException(status_code=423, detail="Tenant is under legal hold")
    return result
