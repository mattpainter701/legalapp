"""Fee-agreement packet preparation with explicit human approval boundaries.

This service deliberately creates a lead/workflow artifact only. It never creates
a matter, sends an email, or starts an e-signature request.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_template import DocumentTemplate
from app.models.prospect_follow_through import EngagementPacket, ProspectFollowThrough
from app.config import get_settings
from app.routers.document_templates import render_template
from app.services.template_logic import TemplateLogicError
from app.schemas.engagement_packet import PacketCreate, PacketUpdate


PACKET_KIND = "fee_agreement_packet"
settings = get_settings()
MATERIAL_FIELDS = (
    "template_id",
    "fee_structure",
    "scope_bullets",
    "client.name",
    "client.email",
    "attorney.name",
    "signers",
)


def _payload(data: PacketCreate | PacketUpdate) -> dict[str, Any]:
    value = data.model_dump(mode="json", exclude_unset=True)
    value.pop("expected_version", None)
    return value


def _provenance(fields: dict[str, Any], actor: UUID) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in fields.items():
        if key in {"scope_wording", "cover_email"}:
            result[key] = {"source": "optional_ai_or_user", "confirmed": False}
        elif value not in (None, "", [], {}):
            result[key] = {
                "source": "user_confirmed",
                "confirmed": True,
                "actor_id": str(actor),
            }
    return result


def unresolved_fields(fields: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not fields.get("template_id"):
        missing.append("template_id")
    for name in ("fee_amount", "fee_structure", "scope_bullets"):
        if name not in fields or fields.get(name) in (None, "", [], {}):
            missing.append(name)
    for path in (("client", "name"), ("client", "email"), ("attorney", "name")):
        node: Any = fields.get(path[0]) or {}
        if not node.get(path[1]):
            missing.append(".".join(path))
    if not fields.get("signers"):
        missing.append("signers")
    else:
        for index, signer in enumerate(fields["signers"]):
            if not signer.get("name") or not signer.get("email"):
                missing.append(f"signers[{index}]")
    return missing


def _fingerprint(fields: dict[str, Any]) -> str:
    encoded = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


async def _get_template(
    db: AsyncSession, tenant_id: UUID, template_id: UUID
) -> DocumentTemplate:
    template = await db.scalar(
        select(DocumentTemplate).where(
            DocumentTemplate.id == template_id,
            DocumentTemplate.tenant_id == tenant_id,
            DocumentTemplate.is_active.is_(True),
            DocumentTemplate.category.in_(("engagement_letter", "retainer")),
            or_(
                DocumentTemplate.status.in_(("approved", "active")),
                DocumentTemplate.approved_at.is_not(None),
            ),
        )
    )
    if not template:
        raise HTTPException(
            status_code=404, detail="Active fee-agreement template not found"
        )
    return template


async def _get_prospect(
    db: AsyncSession, tenant_id: UUID, lead_id: UUID, *, lock: bool = False
):
    query = select(ProspectFollowThrough).where(
        ProspectFollowThrough.tenant_id == tenant_id,
        ProspectFollowThrough.lead_id == lead_id,
    )
    if lock:
        query = query.with_for_update()
    return await db.scalar(query)


def _require_enabled(prospect, actor_id: UUID | None = None) -> None:
    if not (
        settings.VIRTUAL_ASSISTANT_ENABLED
        and settings.AFTER_CALL_CONCIERGE_ENABLED
        and settings.ENGAGEMENT_PACKETS_ENABLED
    ):
        raise HTTPException(status_code=404, detail="Engagement packets are disabled")
    if not prospect or not prospect.assigned_attorney_user_id:
        raise HTTPException(
            status_code=409, detail="Assign an attorney before preparing a packet"
        )
    if actor_id is not None and prospect.assigned_attorney_user_id != actor_id:
        raise HTTPException(
            status_code=403,
            detail="Only the assigned attorney may prepare or approve this packet",
        )


async def get_packet(db: AsyncSession, tenant_id: UUID, lead_id: UUID):
    prospect = await _get_prospect(db, tenant_id, lead_id)
    if not prospect:
        return None
    return await db.scalar(
        select(EngagementPacket).where(
            EngagementPacket.tenant_id == tenant_id,
            EngagementPacket.prospect_id == prospect.id,
            EngagementPacket.packet_type == "fee_agreement",
        )
    )


async def require_packet_access(
    db: AsyncSession, tenant_id: UUID, lead_id: UUID, actor_id: UUID
):
    prospect = await _get_prospect(db, tenant_id, lead_id)
    _require_enabled(prospect, actor_id)
    return prospect


async def create_packet(
    db: AsyncSession,
    tenant_id: UUID,
    lead_id: UUID,
    actor_id: UUID,
    request: PacketCreate,
):
    await _get_template(db, tenant_id, request.template_id)
    prospect = await _get_prospect(db, tenant_id, lead_id, lock=True)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect follow-through not found")
    _require_enabled(prospect, actor_id)
    if prospect.status != "pursuing":
        raise HTTPException(
            status_code=409,
            detail="Choose Pursue before preparing a fee-agreement packet",
        )
    existing = await get_packet(db, tenant_id, lead_id)
    fields = _payload(request)
    fields["template_id"] = str(request.template_id)
    fields["_lead_id"] = str(lead_id)
    provenance = _provenance(fields, actor_id)
    if existing:
        if existing.idempotency_key != request.idempotency_key:
            raise HTTPException(
                status_code=409,
                detail="A fee-agreement packet already exists for this prospect",
            )
        stored = dict(existing.inputs or {})
        stored.pop("provenance", None)
        stored.pop("preview_fingerprint", None)
        if stored != fields:
            raise HTTPException(
                status_code=409,
                detail="Idempotency key was already used with different packet inputs",
            )
        return existing
    packet = EngagementPacket(
        tenant_id=tenant_id,
        prospect_id=prospect.id,
        packet_type="fee_agreement",
        status="draft",
        template_id=request.template_id,
        inputs={**fields, "provenance": provenance},
        idempotency_key=request.idempotency_key,
        created_by_user_id=actor_id,
    )
    db.add(packet)
    await db.flush()
    return packet


async def update_packet(
    db: AsyncSession,
    tenant_id: UUID,
    lead_id: UUID,
    actor_id: UUID,
    request: PacketUpdate,
):
    _require_enabled(await _get_prospect(db, tenant_id, lead_id), actor_id)
    packet = await get_packet(db, tenant_id, lead_id)
    if not packet:
        raise HTTPException(status_code=404, detail="Fee-agreement packet not found")
    if packet.status == "approved":
        raise HTTPException(
            status_code=409, detail="Approved packet is immutable; create a new draft"
        )
    if packet.version != request.expected_version:
        raise HTTPException(
            status_code=409, detail="Packet changed; refresh before updating"
        )
    fields = dict(packet.inputs or {})
    fields.pop("provenance", None)
    fields.pop("preview_fingerprint", None)
    fields.update(_payload(request))
    if fields.get("template_id"):
        await _get_template(db, tenant_id, UUID(str(fields["template_id"])))
    next_inputs = {
        **fields,
        "provenance": {
            **(packet.inputs or {}).get("provenance", {}),
            **_provenance(_payload(request), actor_id),
        },
    }
    next_template_id = (
        UUID(str(fields["template_id"])) if fields.get("template_id") else None
    )
    result = await db.execute(
        sa_update(EngagementPacket)
        .where(
            EngagementPacket.id == packet.id,
            EngagementPacket.tenant_id == tenant_id,
            EngagementPacket.version == request.expected_version,
            EngagementPacket.status != "approved",
        )
        .values(
            template_id=next_template_id,
            inputs=next_inputs,
            version=request.expected_version + 1,
            status="draft",
            prepared_content=None,
            updated_at=datetime.now(timezone.utc),
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise HTTPException(
            status_code=409, detail="Packet changed; refresh before updating"
        )
    await db.refresh(packet)
    return packet


async def render_packet_preview(
    db: AsyncSession, tenant_id: UUID, lead_id: UUID, actor_id: UUID
):
    _require_enabled(await _get_prospect(db, tenant_id, lead_id), actor_id)
    packet = await get_packet(db, tenant_id, lead_id)
    if not packet:
        raise HTTPException(status_code=404, detail="Fee-agreement packet not found")
    if packet.status == "approved":
        raise HTTPException(status_code=409, detail="Approved packet is immutable")
    fields = dict(packet.inputs or {})
    fields.pop("provenance", None)
    fields.pop("preview_fingerprint", None)
    template = await _get_template(
        db, tenant_id, UUID(str(packet.template_id or fields.get("template_id")))
    )
    variables = {
        "fee_amount": str(
            fields.get("fee_amount") if fields.get("fee_amount") is not None else ""
        ),
        "fee_structure": str(fields.get("fee_structure") or ""),
        "scope": "\n".join(f"• {item}" for item in fields.get("scope_bullets") or []),
        "exclusions": "\n".join(f"• {item}" for item in fields.get("exclusions") or []),
        "client_name": str((fields.get("client") or {}).get("name") or ""),
        "client_email": str((fields.get("client") or {}).get("email") or ""),
        "attorney_name": str((fields.get("attorney") or {}).get("name") or ""),
    }
    try:
        rendered = render_template(template.body, variables)
    except TemplateLogicError as exc:
        # Unbalanced or malformed logic in the packet template is a template
        # authoring problem, not a server fault.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    packet.status = "previewed"
    packet.version += 1
    packet.inputs = {
        **fields,
        "provenance": (packet.inputs or {}).get("provenance", {}),
        "preview_fingerprint": _fingerprint(fields),
    }
    packet.prepared_content = {
        "rendered": rendered,
        "fingerprint": _fingerprint(fields),
    }
    packet.prepared_content["version"] = packet.version
    await db.flush()
    return packet, rendered


async def approve_packet(
    db: AsyncSession,
    tenant_id: UUID,
    lead_id: UUID,
    actor_id: UUID,
    expected_version: int,
):
    _require_enabled(await _get_prospect(db, tenant_id, lead_id), actor_id)
    packet = await get_packet(db, tenant_id, lead_id)
    if not packet:
        raise HTTPException(status_code=404, detail="Fee-agreement packet not found")
    if packet.version != expected_version:
        raise HTTPException(
            status_code=409, detail="Packet changed; refresh before approval"
        )
    if packet.status != "previewed" or not packet.prepared_content:
        raise HTTPException(
            status_code=409, detail="Render the current packet preview before approval"
        )
    fields = dict(packet.inputs or {})
    fields.pop("provenance", None)
    fields.pop("preview_fingerprint", None)
    missing = unresolved_fields(fields)
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Material fields require confirmation",
                "unresolved_fields": missing,
            },
        )
    current_fingerprint = _fingerprint(fields)
    if (
        packet.prepared_content.get("fingerprint") != current_fingerprint
        or packet.prepared_content.get("version") != packet.version
    ):
        raise HTTPException(
            status_code=409, detail="Packet changed since preview; render a new preview"
        )
    approved_at = datetime.now(timezone.utc)
    next_content = {
        **(packet.prepared_content or {}),
        "approval": {
            "source": "user_confirmed",
            "actor_id": str(actor_id),
            "confirmed_at": approved_at.isoformat(),
        },
    }
    result = await db.execute(
        sa_update(EngagementPacket)
        .where(
            EngagementPacket.id == packet.id,
            EngagementPacket.tenant_id == tenant_id,
            EngagementPacket.version == expected_version,
            EngagementPacket.status == "previewed",
        )
        .values(
            status="approved",
            prepared_content=next_content,
            version=expected_version + 1,
            updated_at=approved_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise HTTPException(
            status_code=409, detail="Packet changed; refresh before approval"
        )
    await db.refresh(packet)
    return packet, approved_at
