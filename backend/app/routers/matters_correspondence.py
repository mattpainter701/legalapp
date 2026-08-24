"""Matter correspondence, opaque inbound aliases, and review queue."""

import hashlib
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import (
    get_db,
    set_inbound_email_route_lookup,
    set_tenant_context,
)
from app.middleware.tenant import get_current_user
from app.models.communication_log import CommunicationLog
from app.models.inbound_email import InboundEmail, InboundEmailAlias
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.schemas.matter_correspondence import (
    CorrespondenceItem,
    CorrespondenceListResponse,
    CorrespondenceRules,
    CorrespondenceScanRequest,
    CorrespondenceScanResponse,
    InboundEmailItem,
    InboundEmailListResponse,
    InboundEmailReviewResponse,
    MatterInboundAlias,
    MatterInboundAliasResponse,
)
from app.services.correspondence_capture import (
    _matter_party_addresses,
    scan_and_capture,
)
from app.services.inbound_email import (
    ALIAS_LOCAL_PART_RE,
    alias_lookup_hash,
    file_inbound_email,
    generate_alias_local_part,
    parse_raw_email,
    quarantine_path,
    remove_quarantined_message,
    verify_delivery_signature,
    write_quarantined_message,
)
from app.services.token_vault import decrypt_token, encrypt_token

settings = get_settings()
router = APIRouter(prefix="/api", tags=["matter-correspondence"])


def _inbound_domain() -> str:
    return settings.INBOUND_EMAIL_DOMAIN.strip().lower().rstrip(".")


def _alias_response(row: InboundEmailAlias | None) -> MatterInboundAliasResponse:
    alias = None
    if row is not None:
        local_part = decrypt_token(row.encrypted_local_part)
        alias = MatterInboundAlias(
            id=row.id,
            address=f"{local_part}@{_inbound_domain()}",
            status=row.status,
            last_received_at=row.last_received_at,
            created_at=row.created_at,
        )
    return MatterInboundAliasResponse(
        enabled=settings.INBOUND_EMAIL_ENABLED,
        domain=_inbound_domain(),
        alias=alias,
    )


async def _active_alias(
    db: AsyncSession, tenant_id: uuid.UUID, matter_id: uuid.UUID
) -> InboundEmailAlias | None:
    result = await db.execute(
        select(InboundEmailAlias).where(
            InboundEmailAlias.tenant_id == tenant_id,
            InboundEmailAlias.matter_id == matter_id,
            InboundEmailAlias.status == "active",
        )
    )
    return result.scalar_one_or_none()


async def _read_raw_body_capped(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > limit:
                raise HTTPException(status_code=413, detail="Email is too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid content length")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail="Email is too large")
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(status_code=400, detail="Email body is empty")
    return b"".join(chunks)


async def _get_matter_or_404(
    matter_id: str,
    tenant_id: uuid.UUID,
    db: AsyncSession,
    *,
    for_update: bool = False,
) -> Matter:
    query = select(Matter).where(
        Matter.id == matter_id,
        Matter.tenant_id == tenant_id,
    )
    if for_update:
        query = query.with_for_update()
    result = await db.execute(query)
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter


@router.post("/inbound-email/cloudflare", status_code=status.HTTP_202_ACCEPTED)
async def receive_cloudflare_inbound_email(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Receive an HMAC-authenticated raw message from the Email Worker."""
    if not settings.INBOUND_EMAIL_ENABLED:
        raise HTTPException(status_code=503, detail="Inbound email is disabled")

    envelope_sender = request.headers.get("x-lawhand-envelope-from", "").strip()
    recipient = request.headers.get("x-lawhand-envelope-to", "").strip().lower()
    timestamp = request.headers.get("x-lawhand-timestamp", "").strip()
    signature = request.headers.get("x-lawhand-signature", "").strip()
    if any("\n" in value or "\r" in value for value in (envelope_sender, recipient)):
        raise HTTPException(status_code=400, detail="Invalid envelope headers")
    if len(envelope_sender) > 320 or len(recipient) > 320:
        raise HTTPException(status_code=400, detail="Invalid envelope headers")

    raw_message = await _read_raw_body_capped(request, settings.INBOUND_EMAIL_MAX_BYTES)
    if not verify_delivery_signature(
        supplied_signature=signature,
        secret=settings.INBOUND_EMAIL_WEBHOOK_SECRET,
        timestamp=timestamp,
        envelope_sender=envelope_sender,
        recipient=recipient,
        raw_message=raw_message,
        tolerance_seconds=settings.INBOUND_EMAIL_SIGNATURE_TOLERANCE_SECONDS,
    ):
        raise HTTPException(status_code=401, detail="Invalid delivery signature")

    try:
        local_part, domain = recipient.rsplit("@", 1)
    except ValueError:
        return {"accepted": True}
    if domain.rstrip(".") != _inbound_domain() or not ALIAS_LOCAL_PART_RE.fullmatch(
        local_part
    ):
        return {"accepted": True}

    # The signed route may select only the alias table before tenant binding.
    await set_inbound_email_route_lookup(db, enabled=True)
    alias_result = await db.execute(
        select(InboundEmailAlias).where(
            InboundEmailAlias.token_hash == alias_lookup_hash(local_part),
            InboundEmailAlias.status == "active",
        )
    )
    alias = alias_result.scalar_one_or_none()
    if alias is None:
        await set_inbound_email_route_lookup(db, enabled=False)
        return {"accepted": True}

    await set_tenant_context(db, str(alias.tenant_id))
    await set_inbound_email_route_lookup(db, enabled=False)
    matter_result = await db.execute(
        select(Matter).where(
            Matter.id == alias.matter_id,
            Matter.tenant_id == alias.tenant_id,
        )
    )
    if matter_result.scalar_one_or_none() is None:
        return {"accepted": True}

    message_sha256 = hashlib.sha256(raw_message).hexdigest()
    duplicate = await db.execute(
        select(InboundEmail.id).where(
            InboundEmail.alias_id == alias.id,
            InboundEmail.message_sha256 == message_sha256,
        )
    )
    if duplicate.scalar_one_or_none() is not None:
        return {"accepted": True}

    parsed = parse_raw_email(raw_message)
    inbound_id = uuid.uuid4()
    path = quarantine_path(alias.tenant_id, inbound_id)
    try:
        write_quarantined_message(path, raw_message)
        item = InboundEmail(
            id=inbound_id,
            tenant_id=alias.tenant_id,
            alias_id=alias.id,
            matter_id=alias.matter_id,
            status="pending",
            envelope_sender=envelope_sender[:320],
            recipient=recipient,
            subject=parsed["subject"],
            body_preview=parsed["body_preview"],
            participants=parsed["participants"],
            authentication_results=parsed["authentication_results"],
            provider_message_id=parsed["message_id"],
            message_sha256=message_sha256,
            raw_storage_path=str(path),
            raw_size=len(raw_message),
            occurred_at=parsed["occurred_at"],
        )
        alias.last_received_at = datetime.now(timezone.utc)
        db.add(item)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return {"accepted": True}
    except Exception:
        await db.rollback()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    return {"accepted": True}


@router.get(
    "/matters/{matter_id}/correspondence",
    response_model=CorrespondenceListResponse,
)
async def list_matter_correspondence(
    matter_id: str,
    request: Request,
    direction: str | None = Query(None),
    occurred_after: str | None = Query(None),
    occurred_before: str | None = Query(None),
    participant: str | None = Query(None),
    thread_ref: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List captured email correspondence for a matter."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_matter_or_404(matter_id, user.tenant_id, db)

    query = select(CommunicationLog).where(
        CommunicationLog.matter_id == matter_id,
        CommunicationLog.tenant_id == user.tenant_id,
        CommunicationLog.channel == "email",
    )
    if direction in ("inbound", "outbound"):
        query = query.where(CommunicationLog.direction == direction)
    if occurred_after:
        query = query.where(CommunicationLog.occurred_at >= occurred_after)
    if occurred_before:
        query = query.where(CommunicationLog.occurred_at <= occurred_before)
    if thread_ref:
        query = query.where(CommunicationLog.thread_ref == thread_ref)

    query = query.order_by(CommunicationLog.occurred_at.desc())
    result = await db.execute(query)
    rows = result.scalars().all()

    items = []
    for row in rows:
        if participant:
            haystack = str(row.participants or "").lower()
            if participant.lower() not in haystack:
                continue
        items.append(
            CorrespondenceItem(
                id=row.id,
                direction=row.direction,
                channel=row.channel,
                status=row.status,
                subject=row.subject,
                body=row.body,
                summary=row.summary,
                occurred_at=row.occurred_at,
                external_ref=row.external_ref,
                thread_ref=row.thread_ref,
                participants=row.participants,
                document_id=row.document_id,
                has_attachment=row.document_id is not None,
            )
        )

    return CorrespondenceListResponse(items=items, total=len(items))


@router.post(
    "/matters/{matter_id}/correspondence/scan",
    response_model=CorrespondenceScanResponse,
)
async def scan_matter_correspondence(
    matter_id: str,
    body: CorrespondenceScanRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Scan the signed-in user's recent mail and capture matches into this matter."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_matter_or_404(matter_id, user.tenant_id, db)

    if body.provider not in ("microsoft", "google"):
        raise HTTPException(
            status_code=400, detail="provider must be 'microsoft' or 'google'"
        )

    try:
        result = await scan_and_capture(
            db,
            tenant_id=str(user.tenant_id),
            user_id=str(user.id),
            provider=body.provider,
            matter_id=matter_id,
            max_emails=body.max_emails,
            mailbox_address=getattr(user, "email", None),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return CorrespondenceScanResponse(provider=body.provider, **result)


@router.get("/matters/{matter_id}/correspondence/{comm_id}/download")
async def download_matter_correspondence(
    matter_id: str,
    comm_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Download the stored .eml for a captured correspondence item."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    comm_result = await db.execute(
        select(CommunicationLog).where(
            CommunicationLog.id == comm_id,
            CommunicationLog.matter_id == matter_id,
            CommunicationLog.tenant_id == user.tenant_id,
        )
    )
    comm = comm_result.scalar_one_or_none()
    if comm is None or comm.document_id is None:
        raise HTTPException(status_code=404, detail="No stored message found")

    doc_result = await db.execute(
        select(MatterDocument).where(
            MatterDocument.id == comm.document_id,
            MatterDocument.tenant_id == user.tenant_id,
        )
    )
    doc = doc_result.scalar_one_or_none()
    if doc is None or not doc.storage_path:
        raise HTTPException(status_code=404, detail="Stored message file not found")

    if doc.storage_path.startswith(("http://", "https://")):
        return RedirectResponse(doc.storage_path)

    if not os.path.exists(doc.storage_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(
        path=doc.storage_path,
        filename=doc.filename,
        media_type=doc.content_type or "message/rfc822",
    )


@router.get(
    "/matters/{matter_id}/correspondence/rules",
    response_model=CorrespondenceRules,
)
async def get_correspondence_rules(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the matter's capture rules (seeded defaults when unset)."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    matter = await _get_matter_or_404(matter_id, user.tenant_id, db)

    rules = dict(matter.correspondence_rules or {})
    # Seed case numbers from the matter's case number when none are configured.
    if not rules.get("case_numbers") and matter.case_number:
        rules["case_numbers"] = [matter.case_number]
    rules["tracked_addresses"] = sorted(
        await _matter_party_addresses(db, user.tenant_id, matter)
    )
    return CorrespondenceRules(**rules)


@router.put(
    "/matters/{matter_id}/correspondence/rules",
    response_model=CorrespondenceRules,
)
async def update_correspondence_rules(
    matter_id: str,
    body: CorrespondenceRules,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Replace the matter's capture rules."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    matter = await _get_matter_or_404(matter_id, user.tenant_id, db)

    # ``tracked_addresses`` is read-only computed context for the UI, never a
    # persisted capture rule.
    matter.correspondence_rules = body.model_dump(exclude={"tracked_addresses"})
    await db.commit()
    await db.refresh(matter)
    rules = dict(matter.correspondence_rules or {})
    rules["tracked_addresses"] = sorted(
        await _matter_party_addresses(db, user.tenant_id, matter)
    )
    return CorrespondenceRules(**rules)


@router.get(
    "/matters/{matter_id}/inbound-email/alias",
    response_model=MatterInboundAliasResponse,
)
async def get_matter_inbound_alias(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    matter = await _get_matter_or_404(matter_id, user.tenant_id, db)
    return _alias_response(await _active_alias(db, user.tenant_id, matter.id))


@router.post(
    "/matters/{matter_id}/inbound-email/alias",
    response_model=MatterInboundAliasResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_matter_inbound_alias(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not settings.INBOUND_EMAIL_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Inbound email must be configured before addresses can be created",
        )
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    matter = await _get_matter_or_404(matter_id, user.tenant_id, db, for_update=True)
    existing = await _active_alias(db, user.tenant_id, matter.id)
    if existing:
        return _alias_response(existing)

    local_part = generate_alias_local_part()
    row = InboundEmailAlias(
        tenant_id=user.tenant_id,
        matter_id=matter.id,
        token_hash=alias_lookup_hash(local_part),
        encrypted_local_part=encrypt_token(local_part),
        status="active",
        created_by_user_id=user.id,
    )
    db.add(row)
    try:
        await db.commit()
        await db.refresh(row)
    except IntegrityError:
        await db.rollback()
        await set_tenant_context(db, str(user.tenant_id))
        row = await _active_alias(db, user.tenant_id, matter.id)
        if row is None:
            raise
    return _alias_response(row)


@router.post(
    "/matters/{matter_id}/inbound-email/alias/rotate",
    response_model=MatterInboundAliasResponse,
)
async def rotate_matter_inbound_alias(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not settings.INBOUND_EMAIL_ENABLED:
        raise HTTPException(status_code=503, detail="Inbound email is disabled")
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    matter = await _get_matter_or_404(matter_id, user.tenant_id, db, for_update=True)
    existing = await _active_alias(db, user.tenant_id, matter.id)
    if existing:
        existing.status = "revoked"
        existing.revoked_at = datetime.now(timezone.utc)
        await db.flush()

    local_part = generate_alias_local_part()
    row = InboundEmailAlias(
        tenant_id=user.tenant_id,
        matter_id=matter.id,
        token_hash=alias_lookup_hash(local_part),
        encrypted_local_part=encrypt_token(local_part),
        status="active",
        created_by_user_id=user.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _alias_response(row)


@router.delete(
    "/matters/{matter_id}/inbound-email/alias",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def disable_matter_inbound_alias(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    matter = await _get_matter_or_404(matter_id, user.tenant_id, db)
    row = await _active_alias(db, user.tenant_id, matter.id)
    if row:
        row.status = "revoked"
        row.revoked_at = datetime.now(timezone.utc)
        await db.commit()


@router.get(
    "/matters/{matter_id}/inbound-email",
    response_model=InboundEmailListResponse,
)
async def list_matter_inbound_email(
    matter_id: str,
    request: Request,
    queue_status: str = Query("pending", alias="status"),
    db: AsyncSession = Depends(get_db),
):
    if queue_status not in {"pending", "accepted", "rejected", "all"}:
        raise HTTPException(status_code=400, detail="Invalid inbound email status")
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    matter = await _get_matter_or_404(matter_id, user.tenant_id, db)
    conditions = [
        InboundEmail.tenant_id == user.tenant_id,
        InboundEmail.matter_id == matter.id,
    ]
    if queue_status != "all":
        conditions.append(InboundEmail.status == queue_status)
    total = (
        await db.execute(
            select(func.count()).select_from(InboundEmail).where(*conditions)
        )
    ).scalar_one()
    result = await db.execute(
        select(InboundEmail)
        .where(*conditions)
        .order_by(InboundEmail.created_at.desc())
        .limit(100)
    )
    return InboundEmailListResponse(
        items=[InboundEmailItem.model_validate(row) for row in result.scalars().all()],
        total=total,
    )


async def _pending_inbound_or_404(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    matter_id: uuid.UUID,
    inbound_id: uuid.UUID,
) -> InboundEmail:
    result = await db.execute(
        select(InboundEmail)
        .where(
            InboundEmail.id == inbound_id,
            InboundEmail.tenant_id == tenant_id,
            InboundEmail.matter_id == matter_id,
        )
        .with_for_update()
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Inbound email not found")
    if item.status != "pending":
        raise HTTPException(
            status_code=409, detail="Inbound email was already reviewed"
        )
    return item


@router.post(
    "/matters/{matter_id}/inbound-email/{inbound_id}/accept",
    response_model=InboundEmailReviewResponse,
)
async def accept_matter_inbound_email(
    matter_id: uuid.UUID,
    inbound_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    matter = await _get_matter_or_404(str(matter_id), user.tenant_id, db)
    item = await _pending_inbound_or_404(
        db,
        tenant_id=user.tenant_id,
        matter_id=matter.id,
        inbound_id=inbound_id,
    )
    try:
        communication = await file_inbound_email(
            db,
            item=item,
            matter=matter,
            reviewed_by_user_id=user.id,
        )
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return InboundEmailReviewResponse(
        id=item.id,
        status="accepted",
        communication_log_id=communication.id,
    )


@router.post(
    "/matters/{matter_id}/inbound-email/{inbound_id}/reject",
    response_model=InboundEmailReviewResponse,
)
async def reject_matter_inbound_email(
    matter_id: uuid.UUID,
    inbound_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    matter = await _get_matter_or_404(str(matter_id), user.tenant_id, db)
    item = await _pending_inbound_or_404(
        db,
        tenant_id=user.tenant_id,
        matter_id=matter.id,
        inbound_id=inbound_id,
    )
    try:
        remove_quarantined_message(item)
    except (PermissionError, OSError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not securely discard inbound email: {exc}"
        )
    item.raw_storage_path = None
    item.status = "rejected"
    item.reviewed_by_user_id = user.id
    item.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    return InboundEmailReviewResponse(id=item.id, status="rejected")
