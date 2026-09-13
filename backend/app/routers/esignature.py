"""Native e-signature router (Epic 2).

Firm side (``router``, ``/api/matters/{matter_id}/signatures``): create/list/
view/send/void signature requests on a matter document, inspect where the
signature fields landed, and accept or reject a signed copy the client
uploaded.

Client side (``portal_router``, ``/api/portal/client/signatures``): the client
fills and signs the document inside the portal via the matter-scoped portal
token, or uploads a signed copy for the firm to review.
"""

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.models.signature import SignatureRequest, SignatureSigner
from app.routers.client_portal import ClientPortalContext, get_client_portal_context
from app.schemas.signature import (
    PortalDeclineRequest,
    PortalSignRequest,
    SignatureFieldsResponse,
    SignatureRequestCreate,
    SignatureRequestResponse,
    SignatureRequestVoid,
    SignerResponse,
    SubmissionRejectRequest,
)
from app.services.esign import (
    accept_submission,
    after_completion,
    awaiting_review,
    complete_request_if_done,
    completion_pending,
    decline_event,
    get_provider,
    mark_request_expired_if_needed,
    record_portal_decline,
    record_portal_signature,
    record_uploaded_copy,
    reject_submission,
    signer_can_act_now,
)
from app.services.esign.followups import (
    close_signature_followup,
    ensure_signature_followup,
)
from app.services.esign.notifications import (
    mark_signer_viewed,
    notify_actionable_signers,
    notify_actionable_signers_sms,
    notify_requester_signed,
    notify_signer,
)
from app.services.esign.placement import PlacementError
from app.services.esign.plan import (
    FieldValueError,
    build_plan,
    manifest,
    plan_request_placements,
    saved_values_from_signers,
    signer_refs,
    validate_field_values,
)
from app.services.matter_document_organization import (
    SYSTEM_FOLDER_CLIENT_UPLOADS,
    ensure_system_folder,
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
from app.services.pdf_templates import TemplatePdfError, _open_pdf
from app.services.provider_http import (
    ProviderAuthError,
    ProviderError,
    ProviderNotFound,
)
from app.services.upload_guard import reject_oversized_request

router = APIRouter(prefix="/api/matters", tags=["esignature"])
portal_router = APIRouter(prefix="/api/portal/client", tags=["esignature-portal"])
logger = logging.getLogger(__name__)
matter_file_store = MatterFileStore()

ONLY_INTERNAL_PROVIDER = "Only the LawHand portal signing provider is available"


# ── Serialization ───────────────────────────────────────────────────────────


def _plan_summary(req: SignatureRequest) -> dict:
    stored = req.signing_plan if isinstance(req.signing_plan, dict) else {}
    positioned = [
        item
        for item in (req.positioned_fields or [])
        if isinstance(item, dict)
        and item.get("field_type") in {"signature", "initials", "date"}
    ]
    filename = (req.source_document_filename or "").lower()
    return {
        "fill_supported": bool(
            stored.get("fill_supported", not filename or filename.endswith(".pdf"))
        ),
        "signature_fields_count": int(
            stored.get("signature_fields_count", len(positioned))
        ),
        "placement_source": stored.get(
            "placement_source", "placed" if positioned else None
        ),
    }


async def _to_response(
    db: AsyncSession, req: SignatureRequest, *, portal: bool = False
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
    summary = _plan_summary(req)
    return SignatureRequestResponse(
        id=str(req.id),
        matter_id=str(req.matter_id),
        document_id=str(req.document_id) if req.document_id else None,
        document_name=document_name,
        status=req.status,
        provider=req.provider,
        sent_at=req.sent_at,
        completed_at=req.completed_at,
        due_at=req.due_at,
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
        positioned_fields=req.positioned_fields or [],
        executed_document_id=(
            str(req.executed_document_id) if req.executed_document_id else None
        ),
        submitted_document_id=(
            str(req.submitted_document_id) if req.submitted_document_id else None
        ),
        submitted_at=req.submitted_at,
        completion_pending=completion_pending(req),
        # The storage error names the firm's provider; it is for staff only.
        completion_error=None if portal else req.completion_error,
        fill_supported=summary["fill_supported"],
        signature_fields_count=summary["signature_fields_count"],
        placement_source=summary["placement_source"],
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
                method=s.method or (s.audit or {}).get("method"),
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


async def _verified_source_bytes(
    db: AsyncSession, req: SignatureRequest
) -> bytes | None:
    """The bound source bytes, or None if unavailable or changed since sending."""
    if not req.document_id or not req.source_document_sha256:
        return None
    doc = await db.get(MatterDocument, req.document_id)
    if (
        doc is None
        or str(doc.tenant_id) != str(req.tenant_id)
        or str(doc.matter_id) != str(req.matter_id)
    ):
        return None
    try:
        return await matter_file_store.read_matter_file_bytes(
            db=db,
            tenant_id=str(req.tenant_id),
            document=doc,
            expected_sha256=req.source_document_sha256,
            expected_size=req.source_document_size,
        )
    except (MatterFileReadError, ProviderError) as exc:
        logger.warning(
            "E-sign source validation failed for request %s: %s",
            req.id,
            type(exc).__name__,
        )
        return None


async def _source_document_is_unchanged(
    db: AsyncSession, req: SignatureRequest
) -> bool:
    return await _verified_source_bytes(db, req) is not None


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


def _signed_portal_signer(
    req: SignatureRequest,
    ctx: ClientPortalContext,
    signer_id: str | None,
) -> SignatureSigner | None:
    """A signer for this portal identity that already signed.

    Used to retry completion after a downstream storage outage without asking
    the client to sign a second time (their typed consent is unchanged).
    """
    candidates = [
        s
        for s in req.signers
        if s.status == "signed"
        and _portal_signer_matches_context(s, ctx)
        and (signer_id is None or str(s.id) == signer_id)
    ]
    candidates.sort(key=lambda s: s.sign_order)
    return candidates[0] if candidates else None


def _signing_manifest(
    req: SignatureRequest,
    source: bytes | None,
    *,
    acting_signer: SignatureSigner | None,
    include_mine: bool,
) -> SignatureFieldsResponse:
    """The in-document field manifest, rebuilt from the bound source bytes."""
    refs = signer_refs(req.signers)
    if source is None:
        return SignatureFieldsResponse(
            request_id=str(req.id),
            document_id=str(req.document_id) if req.document_id else None,
            source_sha256=req.source_document_sha256,
            pages=[],
            signer_id=str(acting_signer.id) if acting_signer else None,
            signer_role=acting_signer.role if acting_signer else None,
            fields=[],
            fill_supported=False,
        )
    plan = build_plan(source, signers=refs, positioned_fields=req.positioned_fields)
    return SignatureFieldsResponse(
        request_id=str(req.id),
        document_id=str(req.document_id) if req.document_id else None,
        source_sha256=req.source_document_sha256,
        pages=plan.pages,
        signer_id=str(acting_signer.id) if acting_signer else None,
        signer_role=(acting_signer.role or "signer") if acting_signer else None,
        fields=manifest(
            plan,
            signers=refs,
            acting_signer_id=str(acting_signer.id) if acting_signer else None,
            saved_values=saved_values_from_signers(req.signers),
            include_mine=include_mine,
        ),
        fill_supported=plan.fill_supported,
    )


# ── Firm side ───────────────────────────────────────────────────────────────


async def _get_matter(db: AsyncSession, matter_id: str, tenant_id) -> Matter:
    result = await db.execute(
        select(Matter).where(Matter.id == matter_id, Matter.tenant_id == tenant_id)
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter


async def _read_source_for_create(db: AsyncSession, tenant_id, doc: MatterDocument):
    try:
        return await matter_file_store.read_matter_file_bytes(
            db=db,
            tenant_id=str(tenant_id),
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
    provider_name = (body.provider or "internal").strip().lower()
    if provider_name != "internal":
        raise HTTPException(status_code=422, detail=ONLY_INTERNAL_PROVIDER)

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

    placements = body.positioned_fields or doc.positioned_fields or []
    if doc.signing_placement_required and not placements:
        raise HTTPException(
            status_code=422,
            detail="Review signing field positions on the final generated PDF before sending.",
        )
    source_bytes = await _read_source_for_create(db, user.tenant_id, doc)

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
        due_at=body.due_at,
        expires_at=body.expires_at,
        reminders=_build_reminders(body),
        enforce_signing_order=bool(body.enforce_signing_order),
    )
    try:
        plan_request_placements(
            req,
            source_bytes,
            signers=body.signers,
            placements=placements,
            required_roles=doc.signing_roles or [],
        )
    except PlacementError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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


@router.get(
    "/{matter_id}/signatures/{request_id}/fields",
    response_model=SignatureFieldsResponse,
)
async def get_signature_request_fields(
    matter_id: str,
    request_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Staff view of the signing manifest: every role's fields, no ``mine``."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    req = await _load_request(db, request_id, matter_id, user.tenant_id)
    source = await _verified_source_bytes(db, req)
    return _signing_manifest(req, source, acting_signer=None, include_mine=False)


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
        await close_signature_followup(db, req, "Signature request expired")
        await db.commit()
        raise HTTPException(
            status_code=409,
            detail="Cannot send an expired signature request",
        )
    provider = get_provider(req.provider)
    envelope_id = await provider.send(req)
    if envelope_id:
        req.provider_envelope_id = envelope_id
    req.status = "sent"
    req.sent_at = datetime.now(timezone.utc)
    await notify_actionable_signers(db, req)
    await notify_actionable_signers_sms(db, req)
    await ensure_signature_followup(db, req)
    await db.commit()
    req = await _load_request(db, request_id, matter_id, user.tenant_id)
    return await _to_response(db, req)


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
    await notify_actionable_signers(db, req)
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
    await close_signature_followup(db, req, "Signature request voided")
    req.voided_at = datetime.now(timezone.utc)
    reason = body.reason if body else None
    req.void_reason = reason.strip() if reason and reason.strip() else None
    await db.commit()
    req = await _load_request(db, request_id, matter_id, user.tenant_id)
    return await _to_response(db, req)


@router.post(
    "/{matter_id}/signatures/{request_id}/accept-submission",
    response_model=SignatureRequestResponse,
)
async def accept_signature_submission(
    matter_id: str,
    request_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Accept the client's uploaded signed copy as the executed document."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    result = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(
            SignatureRequest.id == request_id,
            SignatureRequest.matter_id == matter_id,
            SignatureRequest.tenant_id == user.tenant_id,
        )
        .with_for_update()
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Signature request not found")
    if req.status not in ("sent", "partially_signed"):
        raise HTTPException(
            status_code=409, detail="This request is not open for review"
        )
    if not req.submitted_document_id:
        raise HTTPException(
            status_code=409, detail="No uploaded signed copy is awaiting review"
        )
    accept_submission(req, user_id=user.id)
    matter = await db.get(Matter, req.matter_id)
    await complete_request_if_done(db, req, matter)
    if req.status == "completed":
        await after_completion(db, req)
    else:
        await notify_actionable_signers(db, req)
    await db.commit()
    req = await _load_request(db, request_id, matter_id, user.tenant_id)
    return await _to_response(db, req)


@router.post(
    "/{matter_id}/signatures/{request_id}/reject-submission",
    response_model=SignatureRequestResponse,
)
async def reject_signature_submission(
    matter_id: str,
    request_id: str,
    body: SubmissionRejectRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the uploaded signed copy; the client is asked to sign again."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    result = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(
            SignatureRequest.id == request_id,
            SignatureRequest.matter_id == matter_id,
            SignatureRequest.tenant_id == user.tenant_id,
        )
        .with_for_update()
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Signature request not found")
    if req.status not in ("sent", "partially_signed"):
        raise HTTPException(
            status_code=409, detail="This request is not open for review"
        )
    if not req.submitted_document_id:
        raise HTTPException(
            status_code=409, detail="No uploaded signed copy is awaiting review"
        )
    matter = await db.get(Matter, req.matter_id)
    reopened, event = reject_submission(
        req, matter, reason=body.reason, user_id=user.id
    )
    db.add(event)
    for signer in reopened:
        if signer_can_act_now(req, signer):
            await notify_signer(db, signer, req, kind="resubmit")
    from app.services import matter_intake

    await db.flush()
    packet = await matter_intake.get_packet(db, req.tenant_id, req.matter_id, lock=True)
    if packet is not None:
        await matter_intake.reconcile(db, packet)
    await db.commit()
    req = await _load_request(db, request_id, matter_id, user.tenant_id)
    return await _to_response(db, req)


# ── Client portal side ──────────────────────────────────────────────────────


async def _portal_open_request(
    db: AsyncSession, request_id: str, ctx: ClientPortalContext, *, action: str
) -> SignatureRequest:
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
            status_code=409, detail=f"This request is not open for {action}"
        )
    return req


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
    if getattr(ctx, "paperwork_only", False):
        all_requests = [
            item for item in all_requests if str(item.id) in ctx.paperwork_signature_ids
        ]
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
                # A signer whose upload awaits review, or whose filing is
                # pending, still sees the request and its status.
                or (
                    s.status == "signed"
                    and (awaiting_review(r) or completion_pending(r))
                )
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
    return [await _to_response(db, r, portal=True) for r in requests]


@portal_router.get(
    "/signatures/{request_id}/fields", response_model=SignatureFieldsResponse
)
async def portal_signature_fields(
    request_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """The in-document form for the acting signer: fields, rects, ownership."""
    ctx = await get_client_portal_context(request, db)
    req = await _portal_open_request(db, request_id, ctx, action="signing")
    signer = _matching_portal_signer(req, ctx, None) or _signed_portal_signer(
        req, ctx, None
    )
    if signer is None:
        raise HTTPException(
            status_code=403,
            detail="No signer is available for this portal session",
        )
    source = await _verified_source_bytes(db, req)
    return _signing_manifest(
        req,
        source,
        acting_signer=signer if signer.status == "pending" else None,
        include_mine=True,
    )


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
    req = await _portal_open_request(db, request_id, ctx, action="signing")
    source = await _verified_source_bytes(db, req)
    if source is None:
        raise HTTPException(
            status_code=409,
            detail="The source document no longer matches the version sent for acknowledgment",
        )

    # Resolve the signer: explicit signer_id, else the next pending in order.
    # A portal identity that already signed may retry: completion can fail on a
    # downstream storage outage after the signature is durable, and the client
    # must not have to sign again to finish.
    signer = _matching_portal_signer(req, ctx, body.signer_id)
    already_signed = False
    if signer is None:
        signer = _signed_portal_signer(req, ctx, body.signer_id)
        already_signed = signer is not None
    if signer is None:
        raise HTTPException(
            status_code=403,
            detail="No pending signer is currently available for this portal session",
        )
    if not already_signed:
        if signer.status != "pending":
            raise HTTPException(status_code=409, detail="Signer already actioned")
        if not signer_can_act_now(req, signer):
            raise HTTPException(
                status_code=409, detail="An earlier signer must complete first"
            )
        plan = build_plan(
            source,
            signers=signer_refs(req.signers),
            positioned_fields=req.positioned_fields,
        )
        field_values: dict[str, str] = {}
        if plan.fill_supported:
            try:
                field_values = validate_field_values(
                    plan,
                    dict(body.field_values or {}),
                    acting_signer_id=str(signer.id),
                    saved_values=saved_values_from_signers(req.signers),
                )
            except FieldValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "Complete the required fields before signing",
                        "problems": exc.problems,
                    },
                ) from exc
        ip = request.client.host if request.client else None
        await record_portal_signature(
            signer,
            typed_signature=body.typed_signature.strip(),
            ip=ip,
            consent_text_version=body.consent_text_version,
            user_agent=request.headers.get("user-agent"),
            field_values=field_values,
        )

    matter = await db.get(Matter, req.matter_id)
    # A storage outage while filing is recorded on the request, never raised:
    # the client's signature is durable either way and the scheduler retries.
    await complete_request_if_done(db, req, matter)
    if req.status == "completed":
        await after_completion(db, req)
    elif not completion_pending(req):
        await notify_actionable_signers(db, req)
    if not already_signed and (req.status == "completed" or completion_pending(req)):
        # The sender hears about the last signature now, not once filing
        # succeeds: a storage outage must not also hide that the client acted.
        try:
            await notify_requester_signed(db, req)
        except Exception:  # noqa: BLE001 - the signature is recorded regardless
            logger.exception("Signed notice for request %s could not be sent", req.id)
    await db.commit()

    result = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(SignatureRequest.id == request_id)
    )
    req = result.scalar_one()
    return await _to_response(db, req, portal=True)


@portal_router.post(
    "/signatures/{request_id}/upload", response_model=SignatureRequestResponse
)
async def portal_upload_signed_copy(
    request_id: str,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Receive a signed copy completed offline; staff accept or reject it."""
    ctx = await get_client_portal_context(request, db)
    settings = get_settings()
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    reject_oversized_request(request, max_bytes, settings.MAX_FILE_SIZE_MB)
    file_bytes = await file.read(max_bytes + 1)
    if not file_bytes:
        raise HTTPException(status_code=400, detail="That file is empty")
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {settings.MAX_FILE_SIZE_MB}MB",
        )
    if not file_bytes.startswith(b"%PDF-"):
        raise HTTPException(status_code=422, detail="Upload the signed copy as a PDF")
    try:
        _open_pdf(file_bytes)
    except TemplatePdfError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    req = await _portal_open_request(db, request_id, ctx, action="signing")
    signer = _matching_portal_signer(req, ctx, None)
    if signer is None:
        raise HTTPException(
            status_code=403,
            detail="No pending signer is currently available for this portal session",
        )
    if not signer_can_act_now(req, signer):
        raise HTTPException(
            status_code=409, detail="An earlier signer must complete first"
        )
    matter = await db.get(Matter, req.matter_id)
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    base = (req.source_document_filename or "document.pdf").rsplit(".", 1)[0]
    safe_base = "".join(ch if ch.isalnum() or ch in "._- " else "-" for ch in base)
    safe_filename = f"{safe_base.strip() or 'document'}-signed-by-client.pdf"
    doc_id = uuid.uuid4()
    # Client uploads are filed where every other portal upload lands, so the
    # firm finds the signed copy in the same folder as the client's records.
    folder = await ensure_system_folder(
        db,
        tenant_id=uuid.UUID(str(ctx.tenant_id)),
        matter_id=uuid.UUID(str(ctx.matter_id)),
        system_key=SYSTEM_FOLDER_CLIENT_UPLOADS,
    )
    storage_result = await matter_file_store.store_matter_file_result(
        db=db,
        tenant_id=str(ctx.tenant_id),
        matter_slug=matter.slug,
        category="client_uploads",
        filename=f"{doc_id.hex}_{safe_filename}",
        content=file_bytes,
        content_type="application/pdf",
        matter_cloud_folder=matter.cloud_folder,
    )
    if storage_result.error:
        raise HTTPException(503, "Document storage failed. Please retry this file.")
    document = MatterDocument(
        id=doc_id,
        tenant_id=uuid.UUID(str(ctx.tenant_id)),
        matter_id=uuid.UUID(str(ctx.matter_id)),
        uploaded_by_user_id=None,
        folder_id=folder.id if folder is not None else None,
        filename=safe_filename,
        content_type="application/pdf",
        file_size=len(file_bytes),
        storage_path=storage_result.storage_path,
        storage_provider=storage_result.provider,
        storage_backend=storage_result.backend,
        provider_object_id=storage_result.provider_item_id,
        provider_drive_id=storage_result.drive_id,
        provider_parent_id=storage_result.parent_id,
        storage_error=storage_result.error,
        description="Signed copy uploaded by the client; awaiting review",
        document_category="client_uploads",
        document_sha256=hashlib.sha256(file_bytes).hexdigest(),
        portal_visible=True,
    )
    db.add(document)
    await db.flush()
    await record_uploaded_copy(
        req,
        signer,
        document=document,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    from app.services import matter_intake

    packet = await matter_intake.get_packet(db, req.tenant_id, req.matter_id, lock=True)
    if packet is not None:
        await matter_intake.reconcile(db, packet)
    await db.commit()

    result = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(SignatureRequest.id == request_id)
    )
    req = result.scalar_one()
    return await _to_response(db, req, portal=True)


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
    req = await _portal_open_request(db, request_id, ctx, action="decline")

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
    # A decline is terminal, so the firm must see it on the matter and stop
    # chasing the signature. Mirror the completed path: close the follow-up
    # task, put the reason on the matter timeline, and reconcile the intake
    # packet so the paperwork drawer reflects the declined request.
    matter = await db.get(Matter, req.matter_id)
    if matter is not None:
        db.add(decline_event(req, matter))
    await close_signature_followup(db, req, "Signer declined")
    from app.services import matter_intake

    await db.flush()
    packet = await matter_intake.get_packet(db, req.tenant_id, req.matter_id, lock=True)
    if packet is not None:
        await matter_intake.reconcile(db, packet)
    await db.commit()

    result = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(SignatureRequest.id == request_id)
    )
    req = result.scalar_one()
    return await _to_response(db, req, portal=True)
