"""Tenant-admin SMS configuration and signed Twilio webhook endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import require_admin
from app.models.sms import SmsProviderConfig, SmsReviewItem
from app.schemas.sms import (
    SmsMessageResponse,
    SmsProviderConfigResponse,
    SmsProviderConfigUpdate,
    SmsReviewResponse,
    SmsSendRequest,
)
from app.services.access_control import require_capability
from app.services.operator_audit import record_operator_audit
from app.services.sms import (
    SmsError,
    apply_inbound,
    apply_status,
    send_sms,
    verify_twilio_signature,
)
from app.services.token_vault import encrypt_token

router = APIRouter(prefix="/api/sms", tags=["sms"])


@router.put("/config", response_model=SmsProviderConfigResponse)
async def update_sms_config(
    body: SmsProviderConfigUpdate,
    request: Request,
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    row = await db.scalar(
        select(SmsProviderConfig).where(
            SmsProviderConfig.tenant_id == tenant_id,
            SmsProviderConfig.provider == "twilio",
        )
    )
    if row is None:
        row = SmsProviderConfig(tenant_id=tenant_id, provider="twilio")
        db.add(row)
    row.account_sid = body.account_sid.strip()
    row.encrypted_auth_token = encrypt_token(body.auth_token)
    row.encrypted_webhook_secret = encrypt_token(body.webhook_secret)
    row.messaging_service_sid = body.messaging_service_sid
    row.from_number = body.from_number
    row.sender_ready = body.sender_ready
    row.is_active = body.is_active
    row.compliance_snapshot = body.compliance_snapshot
    row.updated_by_user_id = current_user.id
    await db.flush()
    await record_operator_audit(
        db,
        request,
        action="sms.provider_config.updated",
        resource_type="sms_provider_config",
        resource_id=str(row.id),
        actor_type="tenant_user",
        actor_id=str(current_user.id),
        metadata={
            "tenant_id": str(tenant_id),
            "provider": row.provider,
            "sender_ready": row.sender_ready,
            "is_active": row.is_active,
            "ownership_model": row.compliance_snapshot.get("ownership_model"),
        },
    )
    await db.commit()
    await db.refresh(row)
    return row


@router.get("/config", response_model=SmsProviderConfigResponse)
async def get_sms_config(
    current_user=Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    await set_tenant_context(db, str(current_user.tenant_id))
    row = await db.scalar(
        select(SmsProviderConfig).where(
            SmsProviderConfig.tenant_id == current_user.tenant_id,
            SmsProviderConfig.provider == "twilio",
        )
    )
    if row is None:
        raise HTTPException(404, "SMS provider is not configured")
    return row


@router.post("/send", response_model=SmsMessageResponse)
async def send_sms_message(
    body: SmsSendRequest,
    current_user=Depends(require_capability("manage_matters")),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    try:
        row = await send_sms(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            contact_id=body.contact_id,
            matter_id=body.matter_id,
            body=body.body,
            category=body.category,
            idempotency_key=body.idempotency_key,
        )
    except SmsError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return row


async def _signed_params(request: Request, *, tenant_id: uuid.UUID, db: AsyncSession):
    config = await db.scalar(
        select(SmsProviderConfig).where(
            SmsProviderConfig.tenant_id == tenant_id,
            SmsProviderConfig.provider == "twilio",
        )
    )
    if not config or not config.encrypted_webhook_secret:
        raise HTTPException(503, "SMS webhook is not configured")
    from app.services.token_vault import decrypt_token

    try:
        secret = decrypt_token(config.encrypted_webhook_secret)
    except Exception as exc:
        raise HTTPException(503, "SMS webhook secret is unavailable") from exc
    form = await request.form()
    params = {str(key): str(value) for key, value in form.items()}
    supplied = request.headers.get("X-Twilio-Signature")
    if not verify_twilio_signature(
        auth_token=secret,
        url=str(request.url),
        params=params,
        supplied=supplied,
    ):
        raise HTTPException(401, "Invalid SMS webhook signature")
    return params


@router.post("/webhooks/{tenant_id}/inbound", response_model=SmsMessageResponse)
async def inbound_sms_webhook(
    tenant_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)
):
    await set_tenant_context(db, str(tenant_id))
    params = await _signed_params(request, tenant_id=tenant_id, db=db)
    provider_message_id = params.get("MessageSid")
    if not provider_message_id:
        raise HTTPException(422, "Provider message id is required")
    try:
        return await apply_inbound(
            db,
            tenant_id=tenant_id,
            params=params,
            provider_message_id=provider_message_id,
        )
    except SmsError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/webhooks/{tenant_id}/status", response_model=SmsMessageResponse)
async def status_sms_webhook(
    tenant_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)
):
    await set_tenant_context(db, str(tenant_id))
    params = await _signed_params(request, tenant_id=tenant_id, db=db)
    try:
        return await apply_status(db, tenant_id=tenant_id, params=params)
    except SmsError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/review", response_model=list[SmsReviewResponse])
async def list_sms_review_items(
    current_user=Depends(require_capability("manage_matters")),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    return list(
        (
            await db.scalars(
                select(SmsReviewItem)
                .where(
                    SmsReviewItem.tenant_id == current_user.tenant_id,
                    SmsReviewItem.status == "pending",
                )
                .order_by(SmsReviewItem.created_at)
            )
        ).all()
    )
