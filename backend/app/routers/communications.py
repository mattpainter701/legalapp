"""
Communications router — log inbound/outbound communications.

  GET  /api/communications       list with filters
  POST /api/communications       create log entry
  GET  /api/communications/{id}  detail
  PATCH /api/communications/{id} update
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.communication_log import CommunicationLog
from app.schemas.communication_log import (
    CommunicationLogCreate,
    CommunicationLogListResponse,
    CommunicationLogResponse,
    CommunicationLogUpdate,
)

router = APIRouter(prefix="/api/communications", tags=["communications"])


@router.get("", response_model=CommunicationLogListResponse)
async def list_communications(
    matter_id: Optional[uuid.UUID] = None,
    contact_id: Optional[uuid.UUID] = None,
    channel: Optional[str] = None,
    direction: Optional[str] = None,
    occurred_after: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
    await set_tenant_context(db, tenant_id)

    stmt = select(CommunicationLog).where(
        CommunicationLog.tenant_id == uuid.UUID(tenant_id)
    )
    if matter_id:
        stmt = stmt.where(CommunicationLog.matter_id == matter_id)
    if contact_id:
        stmt = stmt.where(CommunicationLog.contact_id == contact_id)
    if channel:
        stmt = stmt.where(CommunicationLog.channel == channel)
    if direction:
        stmt = stmt.where(CommunicationLog.direction == direction)
    if occurred_after:
        stmt = stmt.where(CommunicationLog.occurred_at >= occurred_after)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = stmt.order_by(CommunicationLog.occurred_at.desc())
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    logs = result.scalars().all()

    return CommunicationLogListResponse(
        items=[CommunicationLogResponse.model_validate(entry) for entry in logs],
        total=total,
    )


@router.post("", response_model=CommunicationLogResponse, status_code=201)
async def create_communication_log(
    payload: CommunicationLogCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
    await set_tenant_context(db, tenant_id)

    data = payload.model_dump(exclude_none=True)
    if "occurred_at" not in data:
        data["occurred_at"] = datetime.now(timezone.utc)

    log = CommunicationLog(
        tenant_id=uuid.UUID(tenant_id),
        created_by_user_id=uuid.UUID(current_user["user_id"]),
        **data,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return CommunicationLogResponse.model_validate(log)


@router.get("/{log_id}", response_model=CommunicationLogResponse)
async def get_communication_log(
    log_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(CommunicationLog).where(
            CommunicationLog.id == log_id,
            CommunicationLog.tenant_id == uuid.UUID(tenant_id),
        )
    )
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Communication log not found")
    return CommunicationLogResponse.model_validate(log)


@router.patch("/{log_id}", response_model=CommunicationLogResponse)
async def update_communication_log(
    log_id: uuid.UUID,
    payload: CommunicationLogUpdate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(CommunicationLog).where(
            CommunicationLog.id == log_id,
            CommunicationLog.tenant_id == uuid.UUID(tenant_id),
        )
    )
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Communication log not found")

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(log, field, value)

    await db.commit()
    await db.refresh(log)
    return CommunicationLogResponse.model_validate(log)


@router.delete("/{log_id}", status_code=204)
async def delete_communication_log(
    log_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(CommunicationLog).where(
            CommunicationLog.id == log_id,
            CommunicationLog.tenant_id == uuid.UUID(tenant_id),
        )
    )
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Communication log not found")

    await db.delete(log)
    await db.commit()
