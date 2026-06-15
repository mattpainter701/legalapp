"""Router for matter email correspondence — capture, list, download, rules."""

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.communication_log import CommunicationLog
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.schemas.matter_correspondence import (
    CorrespondenceItem,
    CorrespondenceListResponse,
    CorrespondenceRules,
    CorrespondenceScanRequest,
    CorrespondenceScanResponse,
)
from app.services.correspondence_capture import scan_and_capture

settings = get_settings()
router = APIRouter(prefix="/api", tags=["matter-correspondence"])


async def _get_matter_or_404(
    matter_id: str, tenant_id: uuid.UUID, db: AsyncSession
) -> Matter:
    result = await db.execute(
        select(Matter).where(
            Matter.id == matter_id,
            Matter.tenant_id == tenant_id,
        )
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter


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

    matter.correspondence_rules = body.model_dump()
    await db.commit()
    await db.refresh(matter)
    return CorrespondenceRules(**(matter.correspondence_rules or {}))
