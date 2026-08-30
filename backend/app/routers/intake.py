"""
Intake / Lead pipeline router.

  GET  /api/intake               list leads
  POST /api/intake               create lead (+ optional inline contact create)
  GET  /api/intake/{id}          detail
  PATCH /api/intake/{id}         update status/notes
  POST /api/intake/{id}/convert  convert lead → matter
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.contact import Contact, Lead
from app.models.intake_dashboard import IntakeCallDraft
from app.models.plugin import Matter
from app.models.conversion_loop import LeadFunnelEvent
from app.schemas.contact import (
    LeadConvertRequest,
    LeadCreate,
    LeadResponse,
    LeadUpdate,
)
from app.schemas.intake_dashboard import (
    IntakeCallDraftResponse,
    IntakeCallDraftUpsertRequest,
)

router = APIRouter(prefix="/api/intake", tags=["intake"])

VALID_LEAD_STATUSES = {
    "new",
    "contacted",
    "qualified",
    "conflict_checked",
    "engaged",
    "matter_opened",
    "declined",
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/drafts", response_model=list[IntakeCallDraftResponse])
async def list_current_user_intake_drafts(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(IntakeCallDraft)
        .where(
            IntakeCallDraft.tenant_id == current_user.tenant_id,
            IntakeCallDraft.created_by_user_id == current_user.id,
        )
        .order_by(IntakeCallDraft.updated_at.desc())
    )
    return result.scalars().all()


@router.put("/drafts/{draft_id}", response_model=IntakeCallDraftResponse)
async def upsert_intake_draft(
    draft_id: uuid.UUID,
    body: IntakeCallDraftUpsertRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Keep plain scalar identifiers across commits/rollbacks. SQLAlchemy expires
    # ORM instances on transaction boundaries, and touching current_user after
    # an IntegrityError rollback can otherwise trigger an async lazy load.
    tenant_uuid = current_user.tenant_id
    tenant_id = str(tenant_uuid)
    user_id = current_user.id
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(IntakeCallDraft).where(
            IntakeCallDraft.id == draft_id,
            IntakeCallDraft.tenant_id == tenant_uuid,
            IntakeCallDraft.created_by_user_id == user_id,
        )
    )
    draft = result.scalar_one_or_none()

    now = _now_utc()

    if draft:
        draft.payload = body.payload
        draft.updated_at = now
        await db.commit()
    else:
        draft = IntakeCallDraft(
            id=draft_id,
            tenant_id=tenant_uuid,
            created_by_user_id=user_id,
            payload=body.payload,
            created_at=now,
            updated_at=now,
        )
        db.add(draft)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            await set_tenant_context(db, tenant_id)
            existing = await db.execute(
                select(IntakeCallDraft).where(
                    IntakeCallDraft.id == draft_id,
                    IntakeCallDraft.tenant_id == tenant_uuid,
                    IntakeCallDraft.created_by_user_id == user_id,
                )
            )
            draft = existing.scalar_one_or_none()
            if not draft:
                raise HTTPException(status_code=404, detail="Draft not found") from None
            draft.payload = body.payload
            draft.updated_at = now
            await db.commit()
    return draft


@router.delete("/drafts/{draft_id}", status_code=204)
async def delete_intake_draft(
    draft_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_uuid = current_user.tenant_id
    tenant_id = str(tenant_uuid)
    user_id = current_user.id
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(IntakeCallDraft).where(
            IntakeCallDraft.id == draft_id,
            IntakeCallDraft.tenant_id == tenant_uuid,
            IntakeCallDraft.created_by_user_id == user_id,
        )
    )
    draft = result.scalar_one_or_none()
    if not draft:
        # A uniform 404 neither reveals whether the identifier belongs to a
        # peer/another tenant nor lets callers mistake an unauthorized delete
        # for a successful one.
        raise HTTPException(status_code=404, detail="Draft not found")

    await db.delete(draft)
    await db.commit()


def _make_slug(matter_name: str, tenant_id: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", matter_name.lower()).strip("-")[:80]
    suffix = str(uuid.uuid4())[:8]
    return f"{base}-{suffix}"


def _matter_type(value: str | None) -> str:
    matter_type = (value or "").strip()
    return matter_type or "general"


async def _load_lead(db: AsyncSession, lead_id: uuid.UUID, tenant_id: str) -> Lead:
    result = await db.execute(
        select(Lead).where(
            Lead.id == lead_id,
            Lead.tenant_id == uuid.UUID(tenant_id),
        )
    )
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


async def _load_contact(
    db: AsyncSession, contact_id: uuid.UUID, tenant_id: str
) -> Contact:
    result = await db.execute(
        select(Contact).where(
            Contact.id == contact_id,
            Contact.tenant_id == uuid.UUID(tenant_id),
        )
    )
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


async def _lead_to_response(db: AsyncSession, lead: Lead, tenant_id: str) -> dict:
    from app.schemas.contact import ContactResponse

    contact = await _load_contact(db, lead.contact_id, tenant_id)
    contact_data = {
        col.name: getattr(contact, col.name) for col in contact.__table__.columns
    }
    contact_data["display_name"] = contact.display_name
    lead_data = {col.name: getattr(lead, col.name) for col in lead.__table__.columns}
    lead_data["contact"] = ContactResponse(**contact_data)
    return LeadResponse(**lead_data)


@router.get("", response_model=list[LeadResponse])
async def list_leads(
    status: Optional[str] = None,
    assigned_to: Optional[uuid.UUID] = None,
    practice_area: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    stmt = select(Lead).where(Lead.tenant_id == uuid.UUID(tenant_id))
    if status:
        stmt = stmt.where(Lead.status == status)
    if assigned_to:
        stmt = stmt.where(Lead.assigned_to_user_id == assigned_to)
    if practice_area:
        stmt = stmt.where(Lead.practice_area == practice_area)

    stmt = stmt.order_by(Lead.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    leads = result.scalars().all()

    return [await _lead_to_response(db, lead_item, tenant_id) for lead_item in leads]


@router.post("", response_model=LeadResponse, status_code=201)
async def create_lead(
    payload: LeadCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)
    uid = uuid.UUID(tenant_id)
    user_id = current_user.id

    contact_id = payload.contact_id
    if not contact_id:
        if not payload.contact:
            raise HTTPException(
                status_code=422,
                detail="Either contact_id or contact must be provided",
            )
        contact_data = payload.contact.model_dump(exclude_none=True)
        if "contact_type" not in payload.contact.model_fields_set:
            contact_data["contact_type"] = "prospect"
        contact = Contact(
            tenant_id=uid,
            created_by_user_id=user_id,
            **contact_data,
        )
        db.add(contact)
        await db.flush()
        contact_id = contact.id

    lead = Lead(
        tenant_id=uid,
        contact_id=contact_id,
        created_by_user_id=user_id,
        assigned_to_user_id=payload.assigned_to_user_id,
        source=payload.source,
        practice_area=payload.practice_area,
        description=payload.description,
        estimated_value=payload.estimated_value,
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)
    return await _lead_to_response(db, lead, tenant_id)


@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)
    lead = await _load_lead(db, lead_id, tenant_id)

    return await _lead_to_response(db, lead, tenant_id)


@router.patch("/{lead_id}", response_model=LeadResponse)
async def update_lead(
    lead_id: uuid.UUID,
    payload: LeadUpdate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)
    lead = await _load_lead(db, lead_id, tenant_id)

    updates = payload.model_dump(exclude_none=True)
    if "status" in updates and updates["status"] not in VALID_LEAD_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status. Must be one of: {sorted(VALID_LEAD_STATUSES)}",
        )

    for field, value in updates.items():
        setattr(lead, field, value)

    await db.commit()
    await db.refresh(lead)
    return await _lead_to_response(db, lead, tenant_id)


@router.post("/{lead_id}/convert")
async def convert_lead_to_matter(
    lead_id: uuid.UUID,
    payload: LeadConvertRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)
    uid = uuid.UUID(tenant_id)
    user_id = current_user.id

    lead = await _load_lead(db, lead_id, tenant_id)

    # Publicly acquired leads must pass the explicit review gate. Legacy
    # receptionist imports retain their established conversion behavior while
    # firms migrate those records into the new triage lifecycle.
    if lead.source == "website" and lead.conflict_check_status != "cleared":
        raise HTTPException(
            status_code=409,
            detail="A lead must have an explicitly cleared conflict review before conversion",
        )

    if lead.status == "matter_opened" and lead.matter_id:
        raise HTTPException(
            status_code=409,
            detail="Lead has already been converted to a matter",
        )

    matter = Matter(
        tenant_id=uid,
        user_id=user_id,
        slug=_make_slug(payload.matter_name, tenant_id),
        matter_name=payload.matter_name,
        matter_type=_matter_type(payload.matter_type),
        role=payload.role,
        jurisdiction=payload.jurisdiction,
        counterparty=payload.counterparty,
        client_contact_id=lead.contact_id,
        attorney_of_record_id=payload.attorney_of_record_id,
        status=payload.status or "active",
        description=payload.description,
        budget_amount=payload.budget_amount,
        billing_method=payload.billing_method or "hourly",
        hourly_rate=payload.hourly_rate,
    )
    db.add(matter)
    await db.flush()

    contact = await _load_contact(db, lead.contact_id, tenant_id)
    if contact.contact_type == "prospect":
        contact.contact_type = "client"
    lead.status = "matter_opened"
    lead.matter_id = matter.id
    db.add(
        LeadFunnelEvent(
            tenant_id=uid,
            lead_id=lead.id,
            event_type="matter_opened",
            source=lead.source,
            metadata_json={"matter_id": str(matter.id)},
        )
    )

    await db.commit()
    await db.refresh(matter)
    await db.refresh(lead)

    return {
        "matter_id": str(matter.id),
        "matter_name": matter.matter_name,
        "lead_id": str(lead.id),
        "status": "converted",
    }
