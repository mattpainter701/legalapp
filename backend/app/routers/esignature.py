"""Native e-signature router (Epic 2).

Firm side (``router``, ``/api/matters/{matter_id}/signatures``): create/list/
view/send/void signature requests on a matter document.

Client side (``portal_router``, ``/api/portal/client/signatures``): the client
signs in the portal via the matter-scoped portal token.
"""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError

from app.database import get_db, set_tenant_context
from app.config import get_settings
from app.middleware.tenant import get_current_user
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.models.signature import SignatureRequest, SignatureSigner
from app.models.esign_webhook_event import ESignWebhookEvent
from app.routers.client_portal import ClientPortalContext, get_client_portal_context
from app.schemas.signature import (
    PortalDeclineRequest,
    PortalSignRequest,
    SignatureRequestCreate,
    SignatureRequestResponse,
    SignatureRequestVoid,
    SignerResponse,
)
from app.services.esign import (
    complete_request_if_done,
    get_provider,
    mark_request_expired_if_needed,
    record_portal_decline,
    record_portal_signature,
    signer_can_act_now,
)
from app.services.esign.notifications import (
    mark_signer_viewed,
    notify_actionable_signers,
)
from app.services.matter_file_store import (
    MatterFileAccessError,
    MatterFileIntegrityError,
    MatterFileMetadataError,
    MatterFileNotFound,
    MatterFileReadError,
    MatterFileStore,
    MatterFileTooLarge,
)
from app.services.matter_document_revisions import (
    DocumentRevisionServiceError,
    assert_no_legacy_assistant_derivative_release,
)
from app.services.provider_http import (
    ProviderAuthError,
    ProviderError,
    ProviderNotFound,
)

router = APIRouter(prefix="/api/matters", tags=["esignature"])
portal_router = APIRouter(prefix="/api/portal/client", tags=["esignature-portal"])
logger = logging.getLogger(__name__)
matter_file_store = MatterFileStore()


# ── Serialization ───────────────────────────────────────────────────────────


async def _to_response(
    db: AsyncSession, req: SignatureRequest
) -> SignatureRequestResponse:
    document_name = None
    if req.document_id:
        doc = await db.get(MatterDocument, req.document_id)
        if doc is not None:
            document_name = doc.filename
    signed_document_id = (
        req.provider_envelope_id
        if req.provider == "internal" and req.status == "completed"
        else None
    )
    return SignatureRequestResponse(
        id=str(req.id),
        matter_id=str(req.matter_id),
        document_id=str(req.document_id) if req.document_id else None,
        document_name=document_name,
        status=req.status,
        provider=req.provider,
        sent_at=req.sent_at,
        completed_at=req.completed_at,
        expires_at=req.expires_at,
        reminders=req.reminders,
        enforce_signing_order=bool(req.enforce_signing_order),
        declined_at=req.declined_at,
        decline_reason=req.decline_reason,
        voided_at=req.voided_at,
        void_reason=req.void_reason,
        created_at=req.created_at,
        signed_document_id=signed_document_id,
        completion_artifact_id=signed_document_id,
        source_document_sha256=req.source_document_sha256,
        completion_artifact_sha256=req.completion_artifact_sha256,
        evidence_sha256=req.evidence_sha256,
        signers=[
            SignerResponse(
                id=str(s.id),
                name=s.name,
                email=s.email,
                role=s.role or "signer",
                sign_order=s.sign_order,
                status=s.status,
                signed_at=s.signed_at,
                declined_at=s.declined_at,
                decline_reason=s.decline_reason,
                invitation_delivery_status=(s.audit or {}).get(
                    "invitation_delivery_status"
                ),
                invitation_sent_at=(s.audit or {}).get("invitation_sent_at"),
                reminder_delivery_status=(s.audit or {}).get(
                    "reminder_delivery_status"
                ),
                last_reminder_at=(s.audit or {}).get("reminder_sent_at"),
                viewed_at=(s.audit or {}).get("viewed_at"),
            )
            for s in sorted(req.signers, key=lambda s: s.sign_order)
        ],
    )


async def _load_request(
    db: AsyncSession, request_id: str, matter_id: str, tenant_id
) -> SignatureRequest:
    result = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(
            SignatureRequest.id == request_id,
            SignatureRequest.matter_id == matter_id,
            SignatureRequest.tenant_id == tenant_id,
        )
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Signature request not found")
    return req


async def _source_document_is_unchanged(
    db: AsyncSession, req: SignatureRequest
) -> bool:
    if not req.document_id or not req.source_document_sha256:
        return False
    doc = await db.get(MatterDocument, req.document_id)
    if (
        doc is None
        or str(doc.tenant_id) != str(req.tenant_id)
        or str(doc.matter_id) != str(req.matter_id)
    ):
        return False
    try:
        await matter_file_store.read_matter_file_bytes(
            db=db,
            tenant_id=str(req.tenant_id),
            document=doc,
            expected_sha256=req.source_document_sha256,
            expected_size=req.source_document_size,
        )
        return True
    except (MatterFileReadError, ProviderError) as exc:
        logger.warning(
            "E-sign source validation failed for request %s: %s",
            req.id,
            type(exc).__name__,
        )
        return False


def _portal_signer_matches_context(
    signer: SignatureSigner, ctx: ClientPortalContext
) -> bool:
    if (
        ctx.contact_id
        and signer.contact_id
        and str(signer.contact_id) == str(ctx.contact_id)
    ):
        return True
    if ctx.email and signer.email:
        return signer.email.strip().lower() == ctx.email.strip().lower()
    return False


async def _expire_and_commit_if_needed(db: AsyncSession, req: SignatureRequest) -> bool:
    expired = mark_request_expired_if_needed(req)
    if expired:
        await db.commit()
    return expired


def _build_reminders(body: SignatureRequestCreate) -> dict | None:
    reminders = dict(body.reminders or {})
    reminder_days = sorted(
        {
            int(day)
            for day in body.reminder_days
            if isinstance(day, int) or str(day).strip().isdigit()
        }
    )
    if reminder_days:
        reminders["days_before_expiration"] = reminder_days
    return reminders or None


def _expires_in_past(value: datetime | None) -> bool:
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= datetime.now(timezone.utc)


def _matching_portal_signer(
    req: SignatureRequest,
    ctx: ClientPortalContext,
    signer_id: str | None,
) -> SignatureSigner | None:
    if signer_id:
        signer = next((s for s in req.signers if str(s.id) == signer_id), None)
        if signer and _portal_signer_matches_context(signer, ctx):
            return signer
        return None
    pending = sorted(
        (
            s
            for s in req.signers
            if _portal_signer_matches_context(s, ctx) and signer_can_act_now(req, s)
        ),
        key=lambda s: s.sign_order,
    )
    return pending[0] if pending else None


# ── Firm side ───────────────────────────────────────────────────────────────


async def _get_matter(db: AsyncSession, matter_id: str, tenant_id) -> Matter:
    result = await db.execute(
        select(Matter).where(Matter.id == matter_id, Matter.tenant_id == tenant_id)
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter


@router.post(
    "/{matter_id}/signatures",
    response_model=SignatureRequestResponse,
    status_code=201,
)
async def create_signature_request(
    matter_id: str,
    body: SignatureRequestCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_matter(db, matter_id, user.tenant_id)

    if not body.signers:
        raise HTTPException(status_code=400, detail="At least one signer is required")
    if _expires_in_past(body.expires_at):
        raise HTTPException(
            status_code=422,
            detail="Signature request expiration must be in the future",
        )

    # Validate the document belongs to this matter.
    try:
        document_id = uuid.UUID(body.document_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Document ID is invalid") from None
    doc = await db.get(MatterDocument, document_id)
    if (
        doc is None
        or str(doc.matter_id) != str(matter_id)
        or str(doc.tenant_id) != str(user.tenant_id)
    ):
        raise HTTPException(status_code=404, detail="Document not found on this matter")
    try:
        await assert_no_legacy_assistant_derivative_release(
            db,
            tenant_id=user.tenant_id,
            matter_id=uuid.UUID(matter_id),
            document_id=doc.id,
        )
    except DocumentRevisionServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    provider_name = (body.provider or "internal").strip().lower()
    external_ready = provider_name == "dropbox_sign" and bool(
        get_settings().DROPBOX_SIGN_API_KEY and get_settings().ESIGN_WEBHOOK_SECRET
    )
    if provider_name != "internal" and not external_ready:
        raise HTTPException(
            status_code=422,
            detail=(
                f"E-signature provider '{provider_name}' is not configured. "
                "Configure the certified provider credentials and webhook secret, or use the internal provider."
            ),
        )
    try:
        source_bytes = await matter_file_store.read_matter_file_bytes(
            db=db,
            tenant_id=str(user.tenant_id),
            document=doc,
            expected_size=doc.file_size,
        )
    except MatterFileTooLarge as exc:
        raise HTTPException(
            status_code=413,
            detail="The source document exceeds the maximum signing size",
        ) from exc
    except ProviderAuthError as exc:
        raise HTTPException(
            status_code=503,
            detail="The document storage connection needs to be reconnected",
        ) from exc
    except ProviderNotFound as exc:
        raise HTTPException(
            status_code=409,
            detail="The source document is no longer available in connected storage",
        ) from exc
    except (
        MatterFileAccessError,
        MatterFileIntegrityError,
        MatterFileMetadataError,
        MatterFileNotFound,
    ) as exc:
        raise HTTPException(
            status_code=409,
            detail="The source document cannot be safely bound for signing",
        ) from exc
    except ProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail="The connected document provider could not return the source document",
        ) from exc

    req = SignatureRequest(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        matter_id=uuid.UUID(matter_id),
        document_id=doc.id,
        status="draft",
        provider=provider_name,
        source_document_sha256=hashlib.sha256(source_bytes).hexdigest(),
        source_document_size=len(source_bytes),
        source_document_filename=doc.filename,
        created_by_user_id=user.id,
        expires_at=body.expires_at,
        reminders=_build_reminders(body),
        enforce_signing_order=bool(body.enforce_signing_order),
    )
    db.add(req)
    for i, s in enumerate(body.signers):
        role = (s.role or "signer").strip() or "signer"
        db.add(
            SignatureSigner(
                id=uuid.uuid4(),
                tenant_id=user.tenant_id,
                request_id=req.id,
                contact_id=uuid.UUID(s.contact_id) if s.contact_id else None,
                name=s.name,
                email=s.email,
                role=role[:100],
                sign_order=s.sign_order if s.sign_order is not None else i,
                status="pending",
            )
        )
    await db.commit()
    req = await _load_request(db, str(req.id), matter_id, user.tenant_id)
    return await _to_response(db, req)


@router.get("/{matter_id}/signatures", response_model=List[SignatureRequestResponse])
async def list_signature_requests(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    result = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(
            SignatureRequest.matter_id == matter_id,
            SignatureRequest.tenant_id == user.tenant_id,
        )
        .order_by(SignatureRequest.created_at.desc())
    )
    requests = result.scalars().all()
    if any(mark_request_expired_if_needed(r) for r in requests):
        await db.commit()
    return [await _to_response(db, r) for r in requests]


@router.get(
    "/{matter_id}/signatures/{request_id}",
    response_model=SignatureRequestResponse,
)
async def get_signature_request(
    matter_id: str,
    request_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    req = await _load_request(db, request_id, matter_id, user.tenant_id)
    await _expire_and_commit_if_needed(db, req)
    return await _to_response(db, req)


@router.post(
    "/{matter_id}/signatures/{request_id}/send",
    response_model=SignatureRequestResponse,
)
async def send_signature_request(
    matter_id: str,
    request_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    req = await _load_request(db, request_id, matter_id, user.tenant_id)
    await _expire_and_commit_if_needed(db, req)
    if req.status != "draft":
        raise HTTPException(
            status_code=409, detail=f"Cannot send from status '{req.status}'"
        )
    if not await _source_document_is_unchanged(db, req):
        raise HTTPException(
            status_code=409,
            detail="The source document is unavailable or changed after this request was created",
        )
    if _expires_in_past(req.expires_at):
        req.status = "expired"
        await db.commit()
        raise HTTPException(
            status_code=409,
            detail="Cannot send an expired signature request",
        )
    provider = get_provider(req.provider)
    # External providers need the exact bytes that were hash-bound at create
    # time. Keep them transient; the authoritative source remains firm storage.
    try:
        source_doc = (
            await db.get(MatterDocument, req.document_id) if req.document_id else None
        )
        if source_doc is not None:
            req.source_document_bytes = await matter_file_store.read_matter_file_bytes(
                db=db,
                tenant_id=str(user.tenant_id),
                document=source_doc,
                expected_size=req.source_document_size,
            )
    except Exception as exc:
        raise HTTPException(
            status_code=409, detail="The signing source document is unavailable"
        ) from exc
    envelope_id = await provider.send(req)
    if envelope_id:
        req.provider_envelope_id = envelope_id
    req.status = "sent"
    req.sent_at = datetime.now(timezone.utc)
    await notify_actionable_signers(req)
    await db.commit()
    req = await _load_request(db, request_id, matter_id, user.tenant_id)
    return await _to_response(db, req)


@router.post("/esign/webhooks/{provider}", status_code=204)
async def esign_webhook(  # pragma: no cover - exercised by provider integration
    provider: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Authenticate and reconcile a Dropbox Sign webhook exactly once."""
    if provider != "dropbox_sign":
        raise HTTPException(status_code=404, detail="Unknown e-sign provider")
    secret = get_settings().ESIGN_WEBHOOK_SECRET
    if not secret:
        raise HTTPException(status_code=503, detail="E-sign webhook is not configured")
    raw = await request.body()
    supplied = (
        request.headers.get("X-Dropbox-Signature")
        or request.headers.get("Content-Signature", "").removeprefix("HMAC_SHA256=")
        or request.headers.get("X-Hub-Signature-256", "").removeprefix("sha256=")
    )
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid e-sign webhook signature")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        form_payload = parse_qs(raw.decode("utf-8", errors="strict"))
        try:
            payload = json.loads(form_payload["json"][0])
        except (KeyError, IndexError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(
                status_code=400, detail="Invalid e-sign webhook payload"
            ) from exc
    event = payload.get("event", {})
    event_id = str(event.get("event_id") or event.get("event_time") or "")
    sr = payload.get("signature_request", {})
    envelope_id = sr.get("signature_request_id")
    if not event_id or not envelope_id:
        raise HTTPException(
            status_code=400, detail="E-sign webhook is missing event identity"
        )
    receipt = ESignWebhookEvent(
        provider=provider, event_id=event_id, envelope_id=envelope_id
    )
    db.add(receipt)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return Response(status_code=204)
    tenant_id = (sr.get("metadata") or {}).get("tenant_id")
    if not tenant_id:
        await db.rollback()
        raise HTTPException(
            status_code=400, detail="E-sign webhook has no tenant binding"
        )
    await set_tenant_context(db, str(tenant_id))
    req = await db.scalar(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(
            SignatureRequest.tenant_id == tenant_id,
            SignatureRequest.provider == provider,
            SignatureRequest.provider_envelope_id == envelope_id,
        )
        .with_for_update()
    )
    if req is None:
        await db.rollback()
        raise HTTPException(status_code=404, detail="E-signature request not found")
    event_type = str(event.get("event_type") or "")
    if event_type.endswith("declined"):
        req.status = "declined"
        req.declined_at = datetime.now(timezone.utc)
    else:
        signatures = sr.get("signatures") or []
        by_email = {
            str(item.get("signer_email_address", "")).lower(): item
            for item in signatures
        }
        for signer in req.signers:
            remote = by_email.get(signer.email.lower())
            if remote and str(remote.get("status", "")).lower() in {
                "signed",
                "completed",
            }:
                signer.status = "signed"
                signer.signed_at = signer.signed_at or datetime.now(timezone.utc)
                signer.audit = {
                    **(signer.audit or {}),
                    "method": "dropbox_sign_webhook",
                    "event_id": event_id,
                }
        matter = await db.get(Matter, req.matter_id)
        await complete_request_if_done(db, req, matter)
    await db.commit()
    return Response(status_code=204)


@router.post(
    "/{matter_id}/signatures/{request_id}/resend",
    response_model=SignatureRequestResponse,
)
async def resend_signature_request(
    matter_id: str,
    request_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    req = await _load_request(db, request_id, matter_id, user.tenant_id)
    await _expire_and_commit_if_needed(db, req)
    if req.status not in ("sent", "partially_signed"):
        raise HTTPException(
            status_code=409, detail="Only an open signature request can be resent"
        )
    await notify_actionable_signers(req)
    await db.commit()
    req = await _load_request(db, request_id, matter_id, user.tenant_id)
    return await _to_response(db, req)


@router.post(
    "/{matter_id}/signatures/{request_id}/void",
    response_model=SignatureRequestResponse,
)
async def void_signature_request(
    matter_id: str,
    request_id: str,
    request: Request,
    body: SignatureRequestVoid | None = None,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    req = await _load_request(db, request_id, matter_id, user.tenant_id)
    await _expire_and_commit_if_needed(db, req)
    if req.status in ("completed", "voided"):
        raise HTTPException(
            status_code=409, detail=f"Cannot void from status '{req.status}'"
        )
    req.status = "voided"
    req.voided_at = datetime.now(timezone.utc)
    reason = body.reason if body else None
    req.void_reason = reason.strip() if reason and reason.strip() else None
    await db.commit()
    req = await _load_request(db, request_id, matter_id, user.tenant_id)
    return await _to_response(db, req)


# ── Client portal side ──────────────────────────────────────────────────────


@portal_router.get("/signatures", response_model=List[SignatureRequestResponse])
async def portal_list_signatures(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ctx = await get_client_portal_context(request, db)
    result = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(
            SignatureRequest.matter_id == ctx.matter_id,
            SignatureRequest.tenant_id == ctx.tenant_id,
            SignatureRequest.status.in_(
                ("sent", "partially_signed", "declined", "expired", "voided")
            ),
        )
        .order_by(SignatureRequest.created_at.desc())
    )
    all_requests = result.scalars().all()
    if any(mark_request_expired_if_needed(r) for r in all_requests):
        await db.commit()
    requests = [
        r
        for r in all_requests
        if any(
            _portal_signer_matches_context(s, ctx)
            and (
                r.status in ("declined", "expired", "voided")
                or signer_can_act_now(r, s)
            )
            for s in r.signers
        )
    ]
    for signature_request in requests:
        for signer in signature_request.signers:
            if (
                _portal_signer_matches_context(signer, ctx)
                and signer.status == "pending"
            ):
                mark_signer_viewed(signer)
    if requests:
        await db.commit()
    return [await _to_response(db, r) for r in requests]


@portal_router.post(
    "/signatures/{request_id}/sign", response_model=SignatureRequestResponse
)
async def portal_sign(
    request_id: str,
    body: PortalSignRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ctx = await get_client_portal_context(request, db)
    if not body.typed_signature or not body.typed_signature.strip():
        raise HTTPException(status_code=400, detail="A typed signature is required")
    if body.consent_to_electronic_signature is not True:
        raise HTTPException(
            status_code=422,
            detail="Explicit consent to use an electronic signature is required",
        )
    result = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(
            SignatureRequest.id == request_id,
            SignatureRequest.matter_id == ctx.matter_id,
            SignatureRequest.tenant_id == ctx.tenant_id,
        )
        .with_for_update()
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Signature request not found")
    await _expire_and_commit_if_needed(db, req)
    if req.status not in ("sent", "partially_signed"):
        raise HTTPException(
            status_code=409, detail="This request is not open for signing"
        )
    if not await _source_document_is_unchanged(db, req):
        raise HTTPException(
            status_code=409,
            detail="The source document no longer matches the version sent for acknowledgment",
        )

    # Resolve the signer: explicit signer_id, else the next pending in order.
    signer = _matching_portal_signer(req, ctx, body.signer_id)
    if signer is None:
        raise HTTPException(
            status_code=403,
            detail="No pending signer is currently available for this portal session",
        )
    if signer.status != "pending":
        raise HTTPException(status_code=409, detail="Signer already actioned")
    if not signer_can_act_now(req, signer):
        raise HTTPException(
            status_code=409, detail="An earlier signer must complete first"
        )

    ip = request.client.host if request.client else None
    await record_portal_signature(
        signer,
        typed_signature=body.typed_signature.strip(),
        ip=ip,
        consent_text_version=body.consent_text_version,
        user_agent=request.headers.get("user-agent"),
    )

    matter = await db.get(Matter, req.matter_id)
    await complete_request_if_done(db, req, matter)
    if req.status == "partially_signed":
        await notify_actionable_signers(req)
    await db.commit()

    result = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(SignatureRequest.id == request_id)
    )
    req = result.scalar_one()
    return await _to_response(db, req)


@portal_router.post(
    "/signatures/{request_id}/decline", response_model=SignatureRequestResponse
)
async def portal_decline(
    request_id: str,
    body: PortalDeclineRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ctx = await get_client_portal_context(request, db)

    result = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(
            SignatureRequest.id == request_id,
            SignatureRequest.matter_id == ctx.matter_id,
            SignatureRequest.tenant_id == ctx.tenant_id,
        )
        .with_for_update()
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Signature request not found")
    await _expire_and_commit_if_needed(db, req)
    if req.status not in ("sent", "partially_signed"):
        raise HTTPException(
            status_code=409, detail="This request is not open for decline"
        )

    signer = _matching_portal_signer(req, ctx, body.signer_id)
    if signer is None:
        raise HTTPException(
            status_code=403,
            detail="No pending signer is currently available for this portal session",
        )
    if not signer_can_act_now(req, signer):
        raise HTTPException(
            status_code=409, detail="An earlier signer must complete first"
        )

    ip = request.client.host if request.client else None
    await record_portal_decline(req, signer, reason=body.reason, ip=ip)
    await db.commit()

    result = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(SignatureRequest.id == request_id)
    )
    req = result.scalar_one()
    return await _to_response(db, req)
