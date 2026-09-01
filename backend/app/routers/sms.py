"""Tenant-admin SMS configuration and signed Twilio webhook endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import require_admin
from app.models.contact import Contact
from app.models.plugin import Matter
from app.models.sms import SmsMessage, SmsProviderConfig, SmsReviewItem
from app.schemas.sms import (
    SmsMessageResponse,
    SmsProviderConfigResponse,
    SmsProviderConfigUpdate,
    SmsReconciliationItemResponse,
    SmsReconciliationRequest,
    SmsReviewDecision,
    SmsReviewResponse,
    SmsSendRequest,
)
from app.services.access_control import require_capability
from app.services.operator_audit import record_operator_audit
from app.services.matter_access import (
    accessible_matter_ids,
    can_access_matter,
    matter_access_predicate,
)
from app.services.sms import (
    SmsError,
    apply_inbound,
    apply_status,
    archive_current_provider_credentials,
    ensure_provider_config_credential,
    lock_provider_config_admission,
    normalize_e164,
    provider_auth_token,
    provider_credentials_for_generation,
    reconcile_sms_message,
    resolve_review_item,
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
    await lock_provider_config_admission(db, tenant_id=tenant_id)
    row = await db.scalar(
        select(SmsProviderConfig)
        .where(
            SmsProviderConfig.tenant_id == tenant_id,
            SmsProviderConfig.provider == "twilio",
        )
        .with_for_update()
    )
    if row is None:
        row = SmsProviderConfig(tenant_id=tenant_id, provider="twilio")
        db.add(row)
    else:
        try:
            retired_generations = await archive_current_provider_credentials(
                db,
                config=row,
                actor_user_id=current_user.id,
            )
        except SmsError as exc:
            raise HTTPException(exc.status_code, exc.api_detail()) from exc
        for retired in retired_generations:
            await record_operator_audit(
                db,
                request,
                action="sms.provider_credential.retired",
                resource_type="sms_provider_credential",
                resource_id=str(retired.id),
                actor_type="tenant_user",
                actor_id=str(current_user.id),
                metadata={
                    "tenant_id": str(tenant_id),
                    "provider": retired.provider,
                    "generation": retired.generation,
                    "reason": retired.retirement_reason,
                },
            )
        row.generation += 1
    row.account_sid = body.account_sid.strip()
    row.encrypted_auth_token = encrypt_token(body.auth_token)
    row.messaging_service_sid = body.messaging_service_sid
    row.from_number = body.from_number
    row.sender_ready = body.sender_ready
    row.is_active = body.is_active
    row.compliance_snapshot = (
        body.compliance_snapshot.model_dump() if body.compliance_snapshot else {}
    )
    row.updated_by_user_id = current_user.id
    await db.flush()
    try:
        await ensure_provider_config_credential(db, config=row)
    except SmsError as exc:
        raise HTTPException(exc.status_code, exc.api_detail()) from exc
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
            "generation": row.generation,
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
    request: Request,
    current_user=Depends(require_capability("manage_matters")),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    if body.matter_id is None or not await can_access_matter(
        db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        is_admin=current_user.role == "admin",
        matter_id=body.matter_id,
    ):
        raise HTTPException(404, "SMS target was not found")

    async def audit_success(row: SmsMessage) -> None:
        await record_operator_audit(
            db,
            request,
            action="sms.send.submitted",
            resource_type="sms_message",
            resource_id=str(row.id),
            actor_type="tenant_user",
            actor_id=str(current_user.id),
            metadata={
                "tenant_id": str(current_user.tenant_id),
                "contact_id": str(body.contact_id),
                "matter_id": str(body.matter_id) if body.matter_id else None,
                "category": body.category,
                "provider": "twilio",
                "provider_status": row.provider_status,
                "provider_config_generation": row.provider_config_generation,
            },
        )

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
            before_success_commit=audit_success,
        )
    except SmsError as exc:
        # Durable message outcomes already carry an OperatorAuditLog in the
        # exact same commit as their state mutation. Only pre-reservation
        # validation failures need a request-layer rejection audit.
        if exc.sms_message_id is None:
            await set_tenant_context(db, str(current_user.tenant_id))
            await record_operator_audit(
                db,
                request,
                action="sms.send.rejected",
                resource_type="sms_request",
                resource_id=body.idempotency_key,
                actor_type="tenant_user",
                actor_id=str(current_user.id),
                metadata={
                    "tenant_id": str(current_user.tenant_id),
                    "contact_id": str(body.contact_id),
                    "matter_id": str(body.matter_id) if body.matter_id else None,
                    "category": body.category,
                    "status_code": exc.status_code,
                    "delivery_certainty": exc.delivery_certainty,
                    "reconciliation_required": exc.reconciliation_required,
                },
            )
            await db.commit()
        else:
            # Replay and reconciliation-required failures can retain the
            # service's FOR UPDATE authorization/message locks. Release that
            # read-only error transaction before the access-log middleware
            # writes through its independent session; otherwise the request
            # can wait on its own still-open FK locks indefinitely.
            await db.rollback()
        raise HTTPException(exc.status_code, exc.api_detail()) from exc
    return row


async def _signed_params(
    request: Request,
    *,
    tenant_id: uuid.UUID,
    db: AsyncSession,
    require_inbound_ownership: bool = False,
):
    form = await request.form()
    params = {str(key): str(value) for key, value in form.items()}
    message = None
    if require_inbound_ownership:
        credential = await db.scalar(
            select(SmsProviderConfig)
            .where(
                SmsProviderConfig.tenant_id == tenant_id,
                SmsProviderConfig.provider == "twilio",
            )
            .with_for_update(read=True)
        )
        if not credential:
            raise HTTPException(503, "SMS webhook is not configured")
        if not credential.is_active or not credential.sender_ready:
            raise HTTPException(503, "Inbound SMS is not active for this workspace")
    else:
        provider_message_id = str(params.get("MessageSid") or "").strip()
        if provider_message_id:
            message = await db.scalar(
                select(SmsMessage).where(
                    SmsMessage.tenant_id == tenant_id,
                    SmsMessage.provider_message_id == provider_message_id,
                )
            )
        if message is not None and message.provider_config_generation is not None:
            try:
                credential = await provider_credentials_for_generation(
                    db,
                    tenant_id=tenant_id,
                    generation=message.provider_config_generation,
                    credential_id=message.provider_credential_id,
                    lock_for_provider_io=True,
                )
            except SmsError as exc:
                raise HTTPException(exc.status_code, exc.api_detail()) from exc
        else:
            credential = await db.scalar(
                select(SmsProviderConfig)
                .where(
                    SmsProviderConfig.tenant_id == tenant_id,
                    SmsProviderConfig.provider == "twilio",
                )
                .with_for_update(read=True)
            )
            if not credential:
                raise HTTPException(503, "SMS webhook is not configured")
    try:
        secret = provider_auth_token(credential)
    except SmsError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    supplied = request.headers.get("X-Twilio-Signature")
    if not verify_twilio_signature(
        auth_token=secret,
        url=str(request.url),
        params=params,
        supplied=supplied,
    ):
        raise HTTPException(401, "Invalid SMS webhook signature")
    supplied_account_sid = str(params.get("AccountSid") or "").strip()
    configured_account_sid = str(credential.account_sid or "").strip()
    if (
        require_inbound_ownership
        and (not supplied_account_sid or supplied_account_sid != configured_account_sid)
    ) or (
        not require_inbound_ownership
        and supplied_account_sid
        and supplied_account_sid != configured_account_sid
    ):
        raise HTTPException(401, "SMS webhook provider account mismatch")
    if (
        message is not None
        and message.provider_account_sid
        and message.provider_account_sid != configured_account_sid
    ):
        raise HTTPException(401, "SMS webhook credential generation mismatch")
    if require_inbound_ownership:
        configured_service = str(credential.messaging_service_sid or "").strip()
        supplied_service = str(params.get("MessagingServiceSid") or "").strip()
        if configured_service:
            destination_matches = supplied_service == configured_service
        else:
            destination_matches = False
            configured_number = str(credential.from_number or "").strip()
            try:
                destination_matches = bool(configured_number) and normalize_e164(
                    params.get("To")
                ) == normalize_e164(configured_number)
            except SmsError:
                destination_matches = False
        if not destination_matches:
            raise HTTPException(401, "SMS webhook destination is not tenant-owned")
    return params


@router.post("/webhooks/{tenant_id}/inbound", response_model=SmsMessageResponse)
async def inbound_sms_webhook(
    tenant_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)
):
    await set_tenant_context(db, str(tenant_id))
    params = await _signed_params(
        request,
        tenant_id=tenant_id,
        db=db,
        require_inbound_ownership=True,
    )
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
    rows = list(
        (
            await db.execute(
                select(SmsReviewItem, SmsMessage)
                .join(
                    SmsMessage,
                    (SmsMessage.tenant_id == SmsReviewItem.tenant_id)
                    & (SmsMessage.id == SmsReviewItem.sms_message_id),
                )
                .where(
                    SmsReviewItem.tenant_id == current_user.tenant_id,
                    SmsReviewItem.status == "pending",
                )
                .order_by(SmsReviewItem.created_at)
            )
        ).all()
    )
    visible_matter_ids = await accessible_matter_ids(
        db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        is_admin=current_user.role == "admin",
    )
    if visible_matter_ids is not None:
        rows = [
            (item, message)
            for item, message in rows
            if item.candidate_matter_ids
            and all(
                uuid.UUID(str(candidate_id)) in visible_matter_ids
                for candidate_id in item.candidate_matter_ids
            )
        ]
    # The phone/body can identify a client across every candidate matter. A
    # reviewer who sees only one candidate must not learn content originating
    # from another matter, so unresolved rows are all-candidate-or-nothing.
    visible_candidates_by_item = {
        item.id: (
            [str(value) for value in (item.candidate_contact_ids or [])],
            [str(value) for value in (item.candidate_matter_ids or [])],
        )
        for item, _message in rows
    }
    contact_ids = {
        uuid.UUID(str(candidate_id))
        for item, _message in rows
        for candidate_id in visible_candidates_by_item[item.id][0]
    }
    matter_ids = {
        uuid.UUID(str(candidate_id))
        for item, _message in rows
        for candidate_id in visible_candidates_by_item[item.id][1]
    }
    contact_labels = {
        str(contact.id): contact.display_name
        for contact in (
            (
                await db.scalars(
                    select(Contact).where(
                        Contact.tenant_id == current_user.tenant_id,
                        Contact.id.in_(contact_ids),
                    )
                )
            ).all()
            if contact_ids
            else []
        )
    }
    matter_labels = {
        str(matter.id): matter.matter_name
        for matter in (
            (
                await db.scalars(
                    select(Matter).where(
                        Matter.tenant_id == current_user.tenant_id,
                        Matter.id.in_(matter_ids),
                    )
                )
            ).all()
            if matter_ids
            else []
        )
    }
    return [
        {
            "id": item.id,
            "sms_message_id": item.sms_message_id,
            "reason": item.reason,
            "status": item.status,
            "candidate_contact_ids": visible_candidates_by_item[item.id][0],
            "candidate_matter_ids": visible_candidates_by_item[item.id][1],
            "candidate_contacts": [
                {
                    "id": candidate_id,
                    "label": contact_labels.get(
                        str(candidate_id), "Unavailable contact"
                    ),
                }
                for candidate_id in visible_candidates_by_item[item.id][0]
            ],
            "candidate_matters": [
                {
                    "id": candidate_id,
                    "label": matter_labels.get(str(candidate_id), "Unavailable matter"),
                }
                for candidate_id in visible_candidates_by_item[item.id][1]
            ],
            "from_number": message.from_number,
            "body": message.body,
            "created_at": message.created_at,
        }
        for item, message in rows
    ]


@router.post("/review/{review_item_id}", response_model=SmsReviewResponse)
async def decide_sms_review_item(
    review_item_id: uuid.UUID,
    body: SmsReviewDecision,
    request: Request,
    current_user=Depends(require_capability("manage_matters")),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    try:
        item = await resolve_review_item(
            db,
            tenant_id=current_user.tenant_id,
            reviewer_user_id=current_user.id,
            review_item_id=review_item_id,
            decision=body.decision,
            contact_id=body.contact_id,
            matter_id=body.matter_id,
        )
        await record_operator_audit(
            db,
            request,
            action=f"sms.inbound_route.{body.decision}",
            resource_type="sms_review_item",
            resource_id=str(item.id),
            actor_type="tenant_user",
            actor_id=str(current_user.id),
            metadata={
                "tenant_id": str(current_user.tenant_id),
                "sms_message_id": str(item.sms_message_id),
                "contact_id": str(body.contact_id) if body.contact_id else None,
                "matter_id": str(body.matter_id) if body.matter_id else None,
            },
        )
        await db.commit()
    except SmsError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except Exception:
        await db.rollback()
        await set_tenant_context(db, str(current_user.tenant_id))
        raise
    return item


@router.post("/messages/{sms_message_id}/reconcile", response_model=SmsMessageResponse)
async def reconcile_uncertain_sms(
    sms_message_id: uuid.UUID,
    body: SmsReconciliationRequest,
    request: Request,
    current_user=Depends(require_capability("manage_matters")),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    visible_message = await db.scalar(
        select(SmsMessage).where(
            SmsMessage.id == sms_message_id,
            SmsMessage.tenant_id == current_user.tenant_id,
            SmsMessage.direction == "outbound",
            matter_access_predicate(
                tenant_id=current_user.tenant_id,
                user_id=current_user.id,
                is_admin=current_user.role == "admin",
                matter_id_column=SmsMessage.matter_id,
            ),
        )
    )
    if visible_message is None:
        raise HTTPException(404, "SMS reconciliation item was not found")
    try:
        message = await reconcile_sms_message(
            db,
            tenant_id=current_user.tenant_id,
            operator_user_id=current_user.id,
            sms_message_id=sms_message_id,
            resolution=body.resolution,
            provider_message_id=body.provider_message_id,
        )
    except SmsError as exc:
        await record_operator_audit(
            db,
            request,
            action="sms.dispatch.reconciliation_rejected",
            resource_type="sms_message",
            resource_id=str(sms_message_id),
            actor_type="tenant_user",
            actor_id=str(current_user.id),
            metadata={
                "tenant_id": str(current_user.tenant_id),
                "resolution": body.resolution,
                "provider_message_id": body.provider_message_id,
                "code": exc.code,
            },
        )
        await db.commit()
        raise HTTPException(exc.status_code, exc.api_detail()) from exc
    await record_operator_audit(
        db,
        request,
        action=(
            "sms.dispatch.operator_attested"
            if body.resolution == "operator_attested_unknown"
            else "sms.dispatch.reconciled"
        ),
        resource_type="sms_message",
        resource_id=str(message.id),
        actor_type="tenant_user",
        actor_id=str(current_user.id),
        metadata={
            "tenant_id": str(current_user.tenant_id),
            "resolution": body.resolution,
            "provider_message_id": body.provider_message_id,
        },
    )
    await db.commit()
    return message


@router.get("/reconciliation", response_model=list[SmsReconciliationItemResponse])
async def list_sms_reconciliation_items(
    current_user=Depends(require_capability("manage_matters")),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    return list(
        (
            await db.scalars(
                select(SmsMessage)
                .where(
                    SmsMessage.tenant_id == current_user.tenant_id,
                    SmsMessage.direction == "outbound",
                    SmsMessage.reconciliation_required_at.is_not(None),
                    SmsMessage.reconciliation_resolved_at.is_(None),
                    matter_access_predicate(
                        tenant_id=current_user.tenant_id,
                        user_id=current_user.id,
                        is_admin=current_user.role == "admin",
                        matter_id_column=SmsMessage.matter_id,
                    ),
                )
                .order_by(
                    SmsMessage.reconciliation_required_at,
                    SmsMessage.created_at,
                )
            )
        ).all()
    )


@router.get(
    "/reconciliation/{sms_message_id}",
    response_model=SmsReconciliationItemResponse,
)
async def get_sms_reconciliation_item(
    sms_message_id: uuid.UUID,
    current_user=Depends(require_capability("manage_matters")),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    message = await db.scalar(
        select(SmsMessage).where(
            SmsMessage.id == sms_message_id,
            SmsMessage.tenant_id == current_user.tenant_id,
            SmsMessage.direction == "outbound",
            SmsMessage.reconciliation_required_at.is_not(None),
            SmsMessage.reconciliation_resolved_at.is_(None),
            matter_access_predicate(
                tenant_id=current_user.tenant_id,
                user_id=current_user.id,
                is_admin=current_user.role == "admin",
                matter_id_column=SmsMessage.matter_id,
            ),
        )
    )
    if message is None:
        raise HTTPException(
            404,
            {
                "code": "sms_reconciliation_not_found",
                "message": "SMS reconciliation item was not found",
            },
        )
    return message
