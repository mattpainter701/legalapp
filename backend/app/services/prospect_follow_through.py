"""Transactional rules for the after-call concierge."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.communication_log import CommunicationLog
from app.models.contact import Contact, Lead
from app.models.prospect_follow_through import (
    ProspectFollowThrough,
    ProspectFollowThroughEvent,
)
from app.models.task import Task
from app.models.user import User

ATTORNEY_REVIEW_DAYS = 1
FOLLOW_UP_DAYS = 2
TRANSITIONS = {
    "attorney_review": {
        "pursue": "pursuing",
        "needs_information": "needs_information",
        "decline": "declined",
        "reassign": "reassigned",
    },
    "pursuing": {
        "pursue": "pursuing",
        "needs_information": "needs_information",
        "decline": "declined",
        "reassign": "reassigned",
    },
    "needs_information": {
        "pursue": "pursuing",
        "needs_information": "needs_information",
        "decline": "declined",
        "reassign": "reassigned",
    },
    "reassigned": {
        "pursue": "pursuing",
        "needs_information": "needs_information",
        "decline": "declined",
        "reassign": "reassigned",
    },
    "declined": {},
}


def canonical_task_external_ref(
    *, lead_id: uuid.UUID | None, communication_id: uuid.UUID | None
) -> str | None:
    """Return only the intake task identity that this workflow may adopt."""
    if lead_id:
        return f"intake-dashboard:lead:{lead_id}:follow-up"
    if communication_id:
        return f"intake-dashboard:call:{communication_id}:general-task"
    return None


def decision_defaults(
    transition: str, now: datetime | None = None
) -> tuple[str | None, datetime | None]:
    """Keep every live prospect actionable while making decline terminal."""
    now = now or datetime.now(timezone.utc)
    if transition == "pursue":
        return "Follow up with prospect", now + timedelta(days=FOLLOW_UP_DAYS)
    if transition == "needs_information":
        return "Request missing prospect information", now + timedelta(
            days=ATTORNEY_REVIEW_DAYS
        )
    if transition == "reassign":
        return "Review reassigned prospect", now + timedelta(days=ATTORNEY_REVIEW_DAYS)
    if transition == "decline":
        return None, None
    raise HTTPException(status_code=422, detail="Unknown prospect transition")


async def _validate_attorney(
    db: AsyncSession, tenant_id: uuid.UUID, attorney_id: uuid.UUID | None
) -> None:
    if attorney_id is None:
        return
    attorney = await db.scalar(
        select(User).where(
            User.id == attorney_id,
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
        )
    )
    if attorney is None:
        raise HTTPException(
            status_code=422,
            detail="Assigned attorney must be an active user in this tenant",
        )


async def _find_canonical_task(
    db: AsyncSession, tenant_id: uuid.UUID, *, lead_id, communication_id
):
    external_ref = canonical_task_external_ref(
        lead_id=lead_id, communication_id=communication_id
    )
    if not external_ref:
        return None
    return await db.scalar(
        select(Task)
        .where(Task.tenant_id == tenant_id, Task.external_ref == external_ref)
        .order_by(Task.created_at.asc())
    )


async def _ensure_primary_task(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    row: ProspectFollowThrough,
    *,
    assigned_attorney_user_id,
):
    if row.primary_task_id:
        return await db.scalar(
            select(Task).where(
                Task.id == row.primary_task_id, Task.tenant_id == tenant_id
            )
        )
    task = await _find_canonical_task(
        db, tenant_id, lead_id=row.lead_id, communication_id=row.intake_communication_id
    )
    if task is None:
        external_ref = canonical_task_external_ref(
            lead_id=row.lead_id, communication_id=row.intake_communication_id
        )
        if external_ref:
            task = Task(
                tenant_id=tenant_id,
                contact_id=row.contact_id,
                assigned_to_user_id=assigned_attorney_user_id,
                created_by_user_id=user_id,
                title="Prospect follow-through",
                description="Follow up with prospect and record the outcome.",
                task_type="follow_up",
                status="pending",
                priority="medium",
                source="assistant",
                external_ref=external_ref,
                due_date=(
                    datetime.now(timezone.utc) + timedelta(days=ATTORNEY_REVIEW_DAYS)
                ).date(),
            )
            db.add(task)
            await db.flush()
    if task is not None:
        row.primary_task_id = task.id
    return task


async def get_prospect(
    db: AsyncSession, tenant_id: uuid.UUID, prospect_id: uuid.UUID
) -> ProspectFollowThrough:
    row = await db.scalar(
        select(ProspectFollowThrough).where(
            ProspectFollowThrough.id == prospect_id,
            ProspectFollowThrough.tenant_id == tenant_id,
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="Prospect follow-through not found")
    return row


async def adopt_prospect(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    lead_id,
    contact_id,
    intake_communication_id,
    assigned_attorney_user_id,
    idempotency_key,
):
    if not lead_id and not contact_id:
        raise HTTPException(status_code=422, detail="lead_id or contact_id is required")
    await _validate_attorney(db, tenant_id, assigned_attorney_user_id)
    by_key = await db.scalar(
        select(ProspectFollowThrough).where(
            ProspectFollowThrough.tenant_id == tenant_id,
            ProspectFollowThrough.idempotency_key == idempotency_key,
        )
    )
    if by_key:
        if (lead_id and by_key.lead_id != lead_id) or (
            not lead_id and contact_id and by_key.contact_id != contact_id
        ):
            raise HTTPException(
                status_code=409,
                detail="Idempotency key belongs to a different prospect",
            )
        needed_primary_task = by_key.primary_task_id is None
        task = await _ensure_primary_task(
            db,
            tenant_id,
            user_id,
            by_key,
            assigned_attorney_user_id=by_key.assigned_attorney_user_id,
        )
        if task is not None and needed_primary_task:
            await db.commit()
        return by_key, False, task
    if lead_id:
        lead = await db.scalar(
            select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
        )
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        if contact_id and contact_id != lead.contact_id:
            raise HTTPException(status_code=422, detail="Lead and contact do not match")
        contact_id = contact_id or lead.contact_id
        existing = await db.scalar(
            select(ProspectFollowThrough).where(
                ProspectFollowThrough.tenant_id == tenant_id,
                ProspectFollowThrough.lead_id == lead_id,
            )
        )
    else:
        contact = await db.scalar(
            select(Contact).where(
                Contact.id == contact_id,
                Contact.tenant_id == tenant_id,
                Contact.is_active.is_(True),
            )
        )
        if not contact:
            raise HTTPException(status_code=404, detail="Contact not found")
        existing = await db.scalar(
            select(ProspectFollowThrough).where(
                ProspectFollowThrough.tenant_id == tenant_id,
                ProspectFollowThrough.contact_id == contact_id,
            )
        )
    if intake_communication_id:
        communication = await db.scalar(
            select(CommunicationLog).where(
                CommunicationLog.id == intake_communication_id,
                CommunicationLog.tenant_id == tenant_id,
                CommunicationLog.contact_id == contact_id,
            )
        )
        if communication is None:
            raise HTTPException(
                status_code=404,
                detail="Intake communication not found for this prospect",
            )
    if existing:
        needed_primary_task = existing.primary_task_id is None
        task = await _ensure_primary_task(
            db,
            tenant_id,
            user_id,
            existing,
            assigned_attorney_user_id=existing.assigned_attorney_user_id,
        )
        if task is not None and needed_primary_task:
            await db.commit()
        return existing, False, task
    now = datetime.now(timezone.utc)
    row = ProspectFollowThrough(
        tenant_id=tenant_id,
        lead_id=lead_id,
        contact_id=contact_id,
        intake_communication_id=intake_communication_id,
        idempotency_key=idempotency_key,
        assigned_attorney_user_id=assigned_attorney_user_id,
        status="attorney_review",
        next_action="Review new prospect",
        next_action_due_at=now + timedelta(days=ATTORNEY_REVIEW_DAYS),
        created_by_user_id=user_id,
    )
    db.add(row)
    try:
        await db.flush()
        adopted_task = await _ensure_primary_task(
            db,
            tenant_id,
            user_id,
            row,
            assigned_attorney_user_id=assigned_attorney_user_id,
        )
        db.add(
            ProspectFollowThroughEvent(
                tenant_id=tenant_id,
                prospect_id=row.id,
                event_type="adopted",
                to_status=row.status,
                actor_user_id=user_id,
                metadata_json={
                    "idempotency_key": idempotency_key,
                    "primary_task_id": str(adopted_task.id) if adopted_task else None,
                },
            )
        )
        await db.commit()
        await db.refresh(row)
        return row, True, adopted_task
    except IntegrityError:
        # A concurrent request may have won the natural-key insert. Roll back
        # the failed transaction, then return the committed tenant-scoped row.
        await db.rollback()
        existing = await db.scalar(
            select(ProspectFollowThrough).where(
                ProspectFollowThrough.tenant_id == tenant_id,
                ProspectFollowThrough.idempotency_key == idempotency_key,
            )
        )
        if existing is None and lead_id:
            existing = await db.scalar(
                select(ProspectFollowThrough).where(
                    ProspectFollowThrough.tenant_id == tenant_id,
                    ProspectFollowThrough.lead_id == lead_id,
                )
            )
        if existing is None and contact_id:
            existing = await db.scalar(
                select(ProspectFollowThrough).where(
                    ProspectFollowThrough.tenant_id == tenant_id,
                    ProspectFollowThrough.contact_id == contact_id,
                )
            )
        if existing is None:
            raise
        task = await _ensure_primary_task(
            db,
            tenant_id,
            user_id,
            existing,
            assigned_attorney_user_id=existing.assigned_attorney_user_id,
        )
        await db.commit()
        return existing, False, task


async def transition_prospect(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    prospect: ProspectFollowThrough,
    *,
    transition,
    expected_version,
    assigned_attorney_user_id=None,
    next_action=None,
    next_action_due_at=None,
    note=None,
):
    if prospect.version != expected_version:
        raise HTTPException(
            status_code=409, detail="Prospect changed; refresh before updating"
        )
    if prospect.assigned_attorney_user_id != user_id:
        raise HTTPException(
            status_code=403,
            detail="Only the assigned attorney may decide this prospect",
        )
    next_status = TRANSITIONS.get(prospect.status, {}).get(transition)
    if not next_status:
        raise HTTPException(
            status_code=409, detail=f"Cannot {transition} a {prospect.status} prospect"
        )
    if transition == "reassign" and not assigned_attorney_user_id:
        raise HTTPException(
            status_code=422, detail="assigned_attorney_user_id is required to reassign"
        )
    if assigned_attorney_user_id is not None:
        await _validate_attorney(db, tenant_id, assigned_attorney_user_id)
    default_action, default_due = decision_defaults(transition)
    if transition == "decline":
        next_action, next_action_due_at = None, None
    else:
        next_action = next_action or default_action
        next_action_due_at = next_action_due_at or default_due
    if transition != "decline" and (not next_action or not next_action_due_at):
        raise HTTPException(
            status_code=422, detail="Live prospects require a next action and due date"
        )
    values = {
        "status": next_status,
        "version": prospect.version + 1,
        "next_action": next_action,
        "next_action_due_at": next_action_due_at,
    }
    if assigned_attorney_user_id is not None:
        values["assigned_attorney_user_id"] = assigned_attorney_user_id
    if next_action is not None:
        values["next_action"] = next_action
    if next_action_due_at is not None:
        values["next_action_due_at"] = next_action_due_at
    if transition == "decline":
        values["next_action"] = None
        values["next_action_due_at"] = None
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
    if prospect.primary_task_id:
        task_values = {
            "title": (next_action or "Prospect declined")[:500],
            "status": "cancelled" if transition == "decline" else "pending",
            "due_date": next_action_due_at.date() if next_action_due_at else None,
        }
        if assigned_attorney_user_id is not None:
            task_values["assigned_to_user_id"] = assigned_attorney_user_id
        await db.execute(
            update(Task)
            .where(Task.id == prospect.primary_task_id, Task.tenant_id == tenant_id)
            .values(**task_values)
        )
    db.add(
        ProspectFollowThroughEvent(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            event_type="transition",
            from_status=prospect.status,
            to_status=next_status,
            actor_user_id=user_id,
            note=note,
            metadata_json={
                "transition": transition,
                "expected_version": expected_version,
                "assigned_attorney_user_id": str(assigned_attorney_user_id)
                if assigned_attorney_user_id
                else None,
            },
        )
    )
    await db.commit()
    return await get_prospect(db, tenant_id, prospect.id)
