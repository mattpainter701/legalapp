"""
Contacts router — CRUD + conflict check.

  GET  /api/contacts               list with search/filter
  POST /api/contacts               create
  GET  /api/contacts/{id}          detail
  PATCH /api/contacts/{id}         update
  DELETE /api/contacts/{id}        soft-delete
  GET  /api/contacts/{id}/matters  matters linked to this contact
  GET  /api/contacts/{id}/communications  communication history
  POST /api/contacts/conflict-check  name/email fuzzy match
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.communication_log import CommunicationLog
from app.models.contact import Contact
from app.models.plugin import Matter
from app.schemas.contact import (
    ConflictCheckRequest,
    ConflictCheckResult,
    ConflictMatch,
    ContactCreate,
    ContactListResponse,
    ContactResponse,
    ContactUpdate,
)
from app.schemas.communication_log import CommunicationLogListResponse
from app.services.conflict_check import run_conflict_check

router = APIRouter(prefix="/api/contacts", tags=["contacts"])


def _contact_to_response(c: Contact) -> ContactResponse:
    data = {col.name: getattr(c, col.name) for col in c.__table__.columns}
    data["display_name"] = c.display_name
    return ContactResponse(**data)


@router.get("", response_model=ContactListResponse)
async def list_contacts(
    q: Optional[str] = None,
    contact_type: Optional[str] = None,
    entity_type: Optional[str] = None,
    active_only: bool = True,
    limit: int = 50,
    offset: int = 0,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    stmt = select(Contact).where(Contact.tenant_id == uuid.UUID(tenant_id))

    if active_only:
        stmt = stmt.where(Contact.is_active.is_(True))
    if contact_type:
        stmt = stmt.where(Contact.contact_type == contact_type)
    if entity_type:
        stmt = stmt.where(Contact.entity_type == entity_type)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                Contact.first_name.ilike(pattern),
                Contact.last_name.ilike(pattern),
                Contact.organization_name.ilike(pattern),
                Contact.email.ilike(pattern),
            )
        )

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = stmt.order_by(
        Contact.last_name, Contact.first_name, Contact.organization_name
    )
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    contacts = result.scalars().all()

    return ContactListResponse(
        items=[_contact_to_response(c) for c in contacts],
        total=total,
    )


@router.post("", response_model=ContactResponse, status_code=201)
async def create_contact(
    payload: ContactCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    contact = Contact(
        tenant_id=uuid.UUID(tenant_id),
        created_by_user_id=current_user.id,
        **payload.model_dump(exclude_none=True),
    )
    db.add(contact)
    await db.commit()
    await db.refresh(contact)
    return _contact_to_response(contact)


@router.post("/conflict-check", response_model=ConflictCheckResult)
async def conflict_check(
    payload: ConflictCheckRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)
    tid = uuid.UUID(tenant_id)

    result = await run_conflict_check(
        db=db,
        tenant_id=tid,
        names=payload.names,
        emails=payload.emails,
        organization_names=payload.organization_names,
    )

    matches = [ConflictMatch(**m) for m in result["matches"]]
    return ConflictCheckResult(
        clear=result["clear"],
        matches=matches,
        checked_names=payload.names,
        checked_emails=payload.emails,
    )


@router.get("/{contact_id}", response_model=ContactResponse)
async def get_contact(
    contact_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(Contact).where(
            Contact.id == contact_id,
            Contact.tenant_id == uuid.UUID(tenant_id),
        )
    )
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return _contact_to_response(contact)


@router.patch("/{contact_id}", response_model=ContactResponse)
async def update_contact(
    contact_id: uuid.UUID,
    payload: ContactUpdate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(Contact).where(
            Contact.id == contact_id,
            Contact.tenant_id == uuid.UUID(tenant_id),
        )
    )
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(contact, field, value)

    await db.commit()
    await db.refresh(contact)
    return _contact_to_response(contact)


@router.delete("/{contact_id}", status_code=204)
async def delete_contact(
    contact_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(Contact).where(
            Contact.id == contact_id,
            Contact.tenant_id == uuid.UUID(tenant_id),
        )
    )
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    contact.is_active = False
    await db.commit()


@router.get("/{contact_id}/matters")
async def contact_matters(
    contact_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(Matter).where(
            Matter.tenant_id == uuid.UUID(tenant_id),
            Matter.client_contact_id == contact_id,
        )
    )
    matters = result.scalars().all()
    return [
        {
            "id": str(m.id),
            "matter_name": m.matter_name,
            "matter_type": m.matter_type,
            "status": m.status,
            "jurisdiction": m.jurisdiction,
            "created_at": m.created_at.isoformat(),
        }
        for m in matters
    ]


@router.get("/{contact_id}/communications", response_model=CommunicationLogListResponse)
async def contact_communications(
    contact_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.schemas.communication_log import CommunicationLogResponse

    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    stmt = (
        select(CommunicationLog)
        .where(
            CommunicationLog.tenant_id == uuid.UUID(tenant_id),
            CommunicationLog.contact_id == contact_id,
        )
        .order_by(CommunicationLog.occurred_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()

    count_stmt = select(func.count()).where(
        CommunicationLog.tenant_id == uuid.UUID(tenant_id),
        CommunicationLog.contact_id == contact_id,
    )
    total = (await db.execute(count_stmt)).scalar_one()

    return CommunicationLogListResponse(
        items=[
            CommunicationLogResponse.model_validate(log_entry) for log_entry in logs
        ],
        total=total,
    )
