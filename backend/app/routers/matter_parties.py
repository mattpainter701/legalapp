"""
Matter Parties router — multi-party support for matters.

  GET    /api/matters/{matter_id}/parties              list parties
  POST   /api/matters/{matter_id}/parties              add party
  PATCH  /api/matters/{matter_id}/parties/{party_id}  update party
  DELETE /api/matters/{matter_id}/parties/{party_id}  remove party
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.contact import Contact
from app.models.matter_party import MatterParty
from app.models.plugin import Matter
from app.schemas.matter_party import (
    MatterPartyCreate,
    MatterPartyListResponse,
    MatterPartyResponse,
    MatterPartyUpdate,
    matter_party_role_definitions,
    normalize_matter_party_role,
)

router = APIRouter(prefix="/api/matters", tags=["matter-parties"])


async def _clear_other_primary_for_role(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    matter_id: uuid.UUID,
    role: str,
    excluding_party_id: uuid.UUID | None = None,
) -> None:
    result = await db.execute(
        select(MatterParty).where(
            MatterParty.tenant_id == tenant_id,
            MatterParty.matter_id == matter_id,
            MatterParty.is_primary.is_(True),
        )
    )
    for existing in result.scalars().all():
        if excluding_party_id is not None and existing.id == excluding_party_id:
            continue
        try:
            existing_role = normalize_matter_party_role(existing.role)
        except ValueError:
            continue
        if existing_role == role:
            existing.is_primary = False


def _party_to_response(p: MatterParty) -> MatterPartyResponse:
    contact_display_name = None
    if p.contact is not None:
        contact_display_name = p.contact.display_name
    return MatterPartyResponse(
        id=p.id,
        tenant_id=p.tenant_id,
        matter_id=p.matter_id,
        contact_id=p.contact_id,
        role=p.role,
        is_primary=p.is_primary,
        notes=p.notes,
        created_at=p.created_at,
        updated_at=p.updated_at,
        contact_display_name=contact_display_name,
    )


@router.get("/{matter_id}/parties", response_model=MatterPartyListResponse)
async def list_matter_parties(
    matter_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    stmt = (
        select(MatterParty)
        .where(
            MatterParty.tenant_id == uuid.UUID(tenant_id),
            MatterParty.matter_id == matter_id,
        )
        .order_by(MatterParty.is_primary.desc(), MatterParty.created_at)
    )
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()
    rows = (await db.execute(stmt)).scalars().all()
    return MatterPartyListResponse(
        items=[_party_to_response(p) for p in rows],
        total=total,
        role_definitions=matter_party_role_definitions(),
    )


@router.post(
    "/{matter_id}/parties", response_model=MatterPartyResponse, status_code=201
)
async def add_matter_party(
    matter_id: uuid.UUID,
    payload: MatterPartyCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    matter_result = await db.execute(
        select(Matter).where(
            Matter.id == matter_id,
            Matter.tenant_id == uuid.UUID(tenant_id),
        )
    )
    if not matter_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Matter not found")
    if payload.matter_id != matter_id:
        raise HTTPException(
            status_code=422,
            detail="matter_id in the request body must match the route",
        )

    contact = await db.scalar(
        select(Contact).where(
            Contact.id == payload.contact_id,
            Contact.tenant_id == uuid.UUID(tenant_id),
        )
    )
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    if payload.is_primary:
        await _clear_other_primary_for_role(
            db,
            tenant_id=uuid.UUID(tenant_id),
            matter_id=matter_id,
            role=payload.role,
        )

    party = MatterParty(
        tenant_id=uuid.UUID(tenant_id),
        matter_id=matter_id,
        contact=contact,
        role=payload.role,
        is_primary=payload.is_primary,
        notes=payload.notes,
    )
    db.add(party)
    await db.commit()
    await db.refresh(party)
    return _party_to_response(party)


@router.patch("/{matter_id}/parties/{party_id}", response_model=MatterPartyResponse)
async def update_matter_party(
    matter_id: uuid.UUID,
    party_id: uuid.UUID,
    payload: MatterPartyUpdate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    stmt = select(MatterParty).where(
        MatterParty.id == party_id,
        MatterParty.matter_id == matter_id,
        MatterParty.tenant_id == uuid.UUID(tenant_id),
    )
    party = (await db.execute(stmt)).scalar_one_or_none()
    if not party:
        raise HTTPException(status_code=404, detail="Party not found")

    update_data = payload.model_dump(exclude_unset=True)
    resulting_role = update_data.get("role", party.role)
    resulting_primary = update_data.get("is_primary", party.is_primary)
    if resulting_primary:
        await _clear_other_primary_for_role(
            db,
            tenant_id=uuid.UUID(tenant_id),
            matter_id=matter_id,
            role=normalize_matter_party_role(resulting_role),
            excluding_party_id=party.id,
        )
    for key, value in update_data.items():
        setattr(party, key, value)

    await db.commit()
    await db.refresh(party)
    return _party_to_response(party)


@router.delete("/{matter_id}/parties/{party_id}", status_code=204)
async def remove_matter_party(
    matter_id: uuid.UUID,
    party_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    stmt = select(MatterParty).where(
        MatterParty.id == party_id,
        MatterParty.matter_id == matter_id,
        MatterParty.tenant_id == uuid.UUID(tenant_id),
    )
    party = (await db.execute(stmt)).scalar_one_or_none()
    if not party:
        raise HTTPException(status_code=404, detail="Party not found")

    await db.delete(party)
    await db.commit()
