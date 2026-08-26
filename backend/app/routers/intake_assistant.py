"""Lead-native API for the After-call Concierge product surface."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.communication_log import CommunicationLog
from app.models.contact import Lead
from app.models.prospect_follow_through import (
    ProspectFollowThrough,
    ProspectFollowThroughEvent,
)
from app.services.assistant_feature_flags import require_after_call_concierge
from app.services.after_call_preparation import prepare_after_call_handoff
from app.services.prospect_follow_through import (
    adopt_prospect,
    transition_prospect,
)


router = APIRouter(prefix="/api/intake/leads", tags=["assistant"])

STATUS_DECISIONS = {
    "pursuing": "pursue",
    "needs_information": "needs_information",
    "declined": "decline",
    "reassigned": "reassign",
}


class FollowThroughPrepareRequest(BaseModel):
    communication_id: uuid.UUID | None = None
    force: bool = False


class FollowThroughUpdate(BaseModel):
    decision: Literal["pursue", "needs_information", "decline", "reassign"] | None = (
        None
    )
    expected_version: int = Field(ge=1)
    assigned_attorney_user_id: uuid.UUID | None = None
    next_action: str | None = Field(default=None, max_length=2000)
    next_action_date: date | None = None
    note: str | None = Field(default=None, max_length=4000)


async def _lead(db: AsyncSession, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> Lead:
    lead = await db.scalar(
        select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
    )
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


async def _prospect(
    db: AsyncSession, tenant_id: uuid.UUID, lead_id: uuid.UUID
) -> ProspectFollowThrough:
    prospect = await db.scalar(
        select(ProspectFollowThrough).where(
            ProspectFollowThrough.tenant_id == tenant_id,
            ProspectFollowThrough.lead_id == lead_id,
        )
    )
    if prospect is None:
        raise HTTPException(
            status_code=404,
            detail="Open the After-call Concierge to prepare this lead first",
        )
    return prospect


def _due_datetime(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time(hour=17), tzinfo=timezone.utc)


def _response(prospect: ProspectFollowThrough) -> dict:
    metadata = dict(prospect.metadata_json or {})
    preparation = metadata.get("assistant_preparation")
    if not isinstance(preparation, dict):
        preparation = {}
    due = prospect.next_action_due_at
    return {
        "id": prospect.id,
        "lead_id": prospect.lead_id,
        "contact_id": prospect.contact_id,
        "intake_communication_id": prospect.intake_communication_id,
        "primary_task_id": prospect.primary_task_id,
        "assigned_attorney_user_id": prospect.assigned_attorney_user_id,
        "status": prospect.status,
        "decision": STATUS_DECISIONS.get(prospect.status),
        "version": prospect.version,
        "next_action": prospect.next_action,
        "next_action_date": due.date().isoformat() if due else None,
        "suggestion": preparation.get("suggestion") or {},
        "inference_available": preparation.get("inference_available"),
        "inference_error": preparation.get("inference_error"),
        "provenance": preparation.get("provenance") or {},
        "prepared_at": preparation.get("prepared_at"),
    }


@router.get(
    "/{lead_id}/follow-through",
    dependencies=[Depends(require_after_call_concierge)],
)
async def get_lead_follow_through(
    lead_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = uuid.UUID(str(current_user.tenant_id))
    await set_tenant_context(db, str(tenant_id))
    await _lead(db, tenant_id, lead_id)
    return _response(await _prospect(db, tenant_id, lead_id))


@router.post(
    "/{lead_id}/follow-through",
    dependencies=[Depends(require_after_call_concierge)],
)
async def prepare_lead_follow_through(
    lead_id: uuid.UUID,
    body: FollowThroughPrepareRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = uuid.UUID(str(current_user.tenant_id))
    await set_tenant_context(db, str(tenant_id))
    lead = await _lead(db, tenant_id, lead_id)
    if lead.assigned_to_user_id is None:
        raise HTTPException(
            status_code=409,
            detail="Assign an attorney before preparing prospect follow-through",
        )
    if body.communication_id:
        communication = await db.scalar(
            select(CommunicationLog).where(
                CommunicationLog.id == body.communication_id,
                CommunicationLog.tenant_id == tenant_id,
                CommunicationLog.contact_id == lead.contact_id,
            )
        )
        if communication is None:
            raise HTTPException(
                status_code=422,
                detail="Intake communication must belong to this lead's contact",
            )
    prospect, _, _ = await adopt_prospect(
        db,
        tenant_id,
        current_user.id,
        lead_id=lead.id,
        contact_id=lead.contact_id,
        intake_communication_id=body.communication_id,
        assigned_attorney_user_id=lead.assigned_to_user_id,
        idempotency_key=f"after-call:lead:{lead.id}",
    )
    if body.communication_id and prospect.intake_communication_id is None:
        prospect.intake_communication_id = body.communication_id
        await db.commit()
    try:
        await prepare_after_call_handoff(
            db,
            tenant_id=tenant_id,
            actor_user_id=current_user.id,
            prospect=prospect,
            force=body.force,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _response(prospect)


@router.patch(
    "/{lead_id}/follow-through",
    dependencies=[Depends(require_after_call_concierge)],
)
async def update_lead_follow_through(
    lead_id: uuid.UUID,
    body: FollowThroughUpdate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = uuid.UUID(str(current_user.tenant_id))
    await set_tenant_context(db, str(tenant_id))
    prospect = await _prospect(db, tenant_id, lead_id)
    expected_version = body.expected_version
    due_at = _due_datetime(body.next_action_date)
    if body.decision:
        prospect = await transition_prospect(
            db,
            tenant_id,
            current_user.id,
            prospect,
            transition=body.decision,
            expected_version=expected_version,
            assigned_attorney_user_id=body.assigned_attorney_user_id,
            next_action=body.next_action,
            next_action_due_at=due_at,
            note=body.note,
        )
        return _response(prospect)

    if prospect.assigned_attorney_user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Only the assigned attorney may change prospect follow-through",
        )
    values: dict = {"version": prospect.version + 1}
    if body.next_action is not None:
        values["next_action"] = body.next_action.strip() or None
    if body.next_action_date is not None:
        values["next_action_due_at"] = due_at
    if len(values) == 1:
        return _response(prospect)
    if prospect.status != "declined" and (
        values.get("next_action", prospect.next_action) is None
        or values.get("next_action_due_at", prospect.next_action_due_at) is None
    ):
        raise HTTPException(
            status_code=422,
            detail="Live prospects require a next action and follow-up date",
        )
    result = await db.execute(
        update(ProspectFollowThrough)
        .where(
            ProspectFollowThrough.id == prospect.id,
            ProspectFollowThrough.tenant_id == tenant_id,
            ProspectFollowThrough.version == expected_version,
        )
        .values(**values)
    )
    if result.rowcount != 1:
        raise HTTPException(
            status_code=409, detail="Prospect changed; refresh before updating"
        )
    db.add(
        ProspectFollowThroughEvent(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            event_type="details_updated",
            from_status=prospect.status,
            to_status=prospect.status,
            actor_user_id=current_user.id,
            note=body.note,
            metadata_json={"fields": sorted(values)},
        )
    )
    await db.commit()
    return _response(await _prospect(db, tenant_id, lead_id))
