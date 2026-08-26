"""Lead-scoped fee-agreement packet preparation endpoints.

These endpoints stop at an approved artifact. They do not create a matter or
invoke outbound email/e-signature providers.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.schemas.engagement_packet import (
    PacketApprove,
    PacketApprovalResponse,
    PacketCreate,
    PacketResponse,
    PacketUpdate,
)
from app.services.engagement_packets import (
    approve_packet,
    create_packet,
    get_packet,
    render_packet_preview,
    unresolved_fields,
    require_packet_access,
    update_packet,
)
from app.services.assistant_feature_flags import require_engagement_packets

router = APIRouter(
    prefix="/api/intake/leads/{lead_id}/engagement-packets",
    tags=["assistant"],
    dependencies=[Depends(require_engagement_packets)],
)


def _response(packet, *, preview: str | None = None):
    fields = dict(packet.inputs or {})
    lead_id = fields.pop("_lead_id", None)
    provenance = dict(fields.pop("provenance", {}) or {})
    fields.pop("preview_fingerprint", None)
    fields.pop("idempotency_key", None)
    if preview is None and isinstance(packet.prepared_content, dict):
        preview = packet.prepared_content.get("rendered")
    return {
        "id": packet.id,
        "lead_id": lead_id,
        "prospect_id": packet.prospect_id,
        "status": packet.status,
        "template_id": uuid.UUID(str(fields["template_id"])),
        "fields": fields,
        "provenance": provenance,
        "unresolved_fields": unresolved_fields(fields),
        "preview": preview,
        "version": packet.version,
    }


@router.post("", response_model=PacketResponse, status_code=201)
async def create_engagement_packet(
    lead_id: uuid.UUID,
    payload: PacketCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    packet = await create_packet(
        db, current_user.tenant_id, lead_id, current_user.id, payload
    )
    await db.commit()
    return _response(packet)


@router.get("", response_model=PacketResponse)
async def get_engagement_packet(
    lead_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    await require_packet_access(db, current_user.tenant_id, lead_id, current_user.id)
    packet = await get_packet(db, current_user.tenant_id, lead_id)
    if not packet:
        raise HTTPException(status_code=404, detail="Fee-agreement packet not found")
    return _response(packet)


@router.patch("", response_model=PacketResponse)
async def patch_engagement_packet(
    lead_id: uuid.UUID,
    payload: PacketUpdate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    await require_packet_access(db, current_user.tenant_id, lead_id, current_user.id)
    packet = await update_packet(
        db, current_user.tenant_id, lead_id, current_user.id, payload
    )
    await db.commit()
    return _response(packet)


@router.post("/render-preview", response_model=PacketResponse)
async def preview_engagement_packet(
    lead_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    packet, rendered = await render_packet_preview(
        db, current_user.tenant_id, lead_id, current_user.id
    )
    await db.commit()
    return _response(packet, preview=rendered)


@router.post("/approve", response_model=PacketApprovalResponse)
async def approve_engagement_packet(
    lead_id: uuid.UUID,
    payload: PacketApprove,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    packet, approved_at = await approve_packet(
        db,
        current_user.tenant_id,
        lead_id,
        current_user.id,
        payload.expected_version,
    )
    await db.commit()
    response = _response(packet)
    response["approved_at"] = approved_at.isoformat()
    return response
