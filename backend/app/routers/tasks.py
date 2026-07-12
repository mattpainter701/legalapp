"""
Tasks router — deadline and task management.

  GET  /api/tasks              list with filters
  POST /api/tasks              create
  GET  /api/tasks/overdue      overdue tasks
  GET  /api/tasks/upcoming     tasks due in next N days
  GET  /api/tasks/{id}         detail
  PATCH /api/tasks/{id}        update (status, reassign, reschedule)
  DELETE /api/tasks/{id}       delete/cancel
"""

import uuid
from datetime import date, datetime, timezone
from typing import Optional
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.contact import Contact, Lead
from app.models.task import Task
from app.models.user import User
from app.services.email import email_delivery_http_error, email_service
from app.services.task_history import record_customer_contact, record_task_event
from app.services.task_notifications import (
    notify_task_created,
    notify_task_updated,
    remove_task_from_calendars,
    task_calendar_user_id,
)
from app.schemas.task import (
    IntakeTaskQualifyRequest,
    IntakeTaskQualifyResponse,
    TaskContactedRequest,
    TaskCreate,
    TaskListResponse,
    TaskResponse,
    TaskUpdate,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# Fields that affect the pushed calendar event when changed
_CALENDAR_RELEVANT_FIELDS = {
    "title",
    "description",
    "task_type",
    "due_date",
    "status",
    "assigned_to_user_id",
}


def _lead_id_from_intake_task(task: Task) -> uuid.UUID:
    prefix = "intake-dashboard:lead:"
    suffix = ":follow-up"
    ref = task.external_ref or ""
    if not ref.startswith(prefix) or not ref.endswith(suffix):
        raise HTTPException(
            status_code=422,
            detail="Task is not an intake follow-up task",
        )
    raw = ref[len(prefix) : -len(suffix)]
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Intake task has an invalid lead reference",
        ) from exc


def _append_section(existing: str | None, heading: str, body: str | None) -> str | None:
    text = (body or "").strip()
    if not text:
        return existing
    base = (existing or "").strip()
    section = f"{heading}\n{text}"
    return f"{base}\n\n{section}" if base else section


async def _load_task_or_404(
    db: AsyncSession, task_id: uuid.UUID, tenant_id: uuid.UUID
) -> Task:
    result = await db.execute(
        select(Task).where(
            Task.id == task_id,
            Task.tenant_id == tenant_id,
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/overdue", response_model=TaskListResponse)
async def get_overdue_tasks(
    matter_id: Optional[uuid.UUID] = None,
    assigned_to: Optional[uuid.UUID] = None,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    today = date.today()
    stmt = select(Task).where(
        Task.tenant_id == uuid.UUID(tenant_id),
        Task.due_date < today,
        Task.status.notin_(["completed", "cancelled"]),
    )
    if matter_id:
        stmt = stmt.where(Task.matter_id == matter_id)
    if assigned_to:
        stmt = stmt.where(Task.assigned_to_user_id == assigned_to)

    stmt = stmt.order_by(Task.due_date)
    result = await db.execute(stmt)
    tasks = result.scalars().all()

    count_stmt = select(func.count()).select_from(
        select(Task)
        .where(
            Task.tenant_id == uuid.UUID(tenant_id),
            Task.due_date < today,
            Task.status.notin_(["completed", "cancelled"]),
        )
        .subquery()
    )
    total = (await db.execute(count_stmt)).scalar_one()

    return TaskListResponse(
        items=[TaskResponse.model_validate(t) for t in tasks],
        total=total,
    )


@router.get("/upcoming", response_model=TaskListResponse)
async def get_upcoming_tasks(
    days: int = 7,
    matter_id: Optional[uuid.UUID] = None,
    assigned_to: Optional[uuid.UUID] = None,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from datetime import timedelta

    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    today = date.today()
    end_date = today + timedelta(days=days)

    stmt = select(Task).where(
        Task.tenant_id == uuid.UUID(tenant_id),
        Task.due_date >= today,
        Task.due_date <= end_date,
        Task.status.notin_(["completed", "cancelled"]),
    )
    if matter_id:
        stmt = stmt.where(Task.matter_id == matter_id)
    if assigned_to:
        stmt = stmt.where(Task.assigned_to_user_id == assigned_to)

    stmt = stmt.order_by(Task.due_date, Task.priority.desc())
    result = await db.execute(stmt)
    tasks = result.scalars().all()
    total = len(tasks)

    return TaskListResponse(
        items=[TaskResponse.model_validate(t) for t in tasks],
        total=total,
    )


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    matter_id: Optional[uuid.UUID] = None,
    contact_id: Optional[uuid.UUID] = None,
    assigned_to: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    task_type: Optional[str] = None,
    due_before: Optional[date] = None,
    due_after: Optional[date] = None,
    limit: int = 100,
    offset: int = 0,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    stmt = select(Task).where(Task.tenant_id == uuid.UUID(tenant_id))

    if matter_id:
        stmt = stmt.where(Task.matter_id == matter_id)
    if contact_id:
        stmt = stmt.where(Task.contact_id == contact_id)
    if assigned_to:
        stmt = stmt.where(Task.assigned_to_user_id == assigned_to)
    if status:
        stmt = stmt.where(Task.status == status)
    if priority:
        stmt = stmt.where(Task.priority == priority)
    if task_type:
        stmt = stmt.where(Task.task_type == task_type)
    if due_before:
        stmt = stmt.where(Task.due_date <= due_before)
    if due_after:
        stmt = stmt.where(Task.due_date >= due_after)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = stmt.order_by(Task.due_date.nulls_last(), Task.priority.desc())
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    tasks = result.scalars().all()

    return TaskListResponse(
        items=[TaskResponse.model_validate(t) for t in tasks],
        total=total,
    )


@router.post("", response_model=TaskResponse, status_code=201)
async def create_task(
    payload: TaskCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    data = payload.model_dump(exclude_none=True)
    assignment_note = (data.pop("assignment_note", None) or "").strip() or None

    task = Task(
        tenant_id=uuid.UUID(tenant_id),
        created_by_user_id=current_user.id,
        **data,
    )
    if assignment_note:
        assigner = current_user.full_name or current_user.email
        task.description = _append_section(
            task.description, f"Assignment note ({assigner}):", assignment_note
        )
    db.add(task)
    await db.flush()
    if task.assigned_to_user_id:
        await record_task_event(
            db, task, event="assigned", actor=current_user, note=assignment_note
        )
    await db.commit()
    await db.refresh(task)

    # The task is the durable source of truth. Email is a best-effort alert and
    # its typed result is logged by the sender; an SMTP outage must not discard
    # the receptionist's or attorney's work.
    await notify_task_created(db, task, tenant_id, assignment_note)

    return TaskResponse.model_validate(task)


@router.post("/{task_id}/qualify-intake", response_model=IntakeTaskQualifyResponse)
async def qualify_intake_task(
    task_id: uuid.UUID,
    payload: IntakeTaskQualifyRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Promote a receptionist intake follow-up into an attorney intake task.

    This is the partner decision point: the receptionist's call assignment task is
    completed, the lead moves to qualified, and the assigned attorney receives a
    separate urgent intake task carrying the receptionist and partner notes.
    """

    tenant_id = str(current_user.tenant_id)
    tenant_uuid = uuid.UUID(tenant_id)
    await set_tenant_context(db, tenant_id)

    partner_task = await _load_task_or_404(db, task_id, tenant_uuid)
    lead_id = _lead_id_from_intake_task(partner_task)
    if (
        partner_task.assigned_to_user_id
        and partner_task.assigned_to_user_id != current_user.id
        and current_user.role != "admin"
    ):
        raise HTTPException(
            status_code=403,
            detail="Only the assigned partner or an admin can qualify this intake task",
        )

    attorney = (
        await db.execute(
            select(User).where(
                User.id == payload.assigned_to_user_id,
                User.tenant_id == tenant_uuid,
                User.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not attorney:
        raise HTTPException(status_code=404, detail="Assigned attorney not found")

    lead = (
        await db.execute(
            select(Lead).where(
                Lead.id == lead_id,
                Lead.tenant_id == tenant_uuid,
            )
        )
    ).scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if lead.status == "matter_opened":
        raise HTTPException(status_code=409, detail="Lead already converted to matter")

    contact = (
        await db.execute(
            select(Contact).where(
                Contact.id == lead.contact_id,
                Contact.tenant_id == tenant_uuid,
            )
        )
    ).scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Lead contact not found")

    lead.status = "qualified"
    lead.assigned_to_user_id = attorney.id
    if payload.estimated_value is not None:
        lead.estimated_value = Decimal(str(payload.estimated_value))
    lead.description = _append_section(
        lead.description,
        "Partner qualification notes:",
        payload.partner_notes,
    )
    lead.description = _append_section(
        lead.description,
        "Qualified case description:",
        payload.case_description,
    )

    caller = contact.display_name
    phone = contact.phone or contact.secondary_phone
    attorney_external_ref = f"intake-dashboard:lead:{lead.id}:attorney-intake"
    description_bits = [
        "Qualified intake assigned by partner.",
        f"Client/prospect: {caller}",
        f"Callback number: {phone}" if phone else "",
        f"Practice area: {lead.practice_area}" if lead.practice_area else "",
        "",
        "Receptionist call/task notes:",
        partner_task.description or "",
        "",
        "Partner notes:",
        (payload.partner_notes or "").strip(),
        "",
        "Case description:",
        (payload.case_description or lead.description or "").strip(),
    ]
    attorney_description = "\n".join(bit for bit in description_bits if bit is not None)

    previous_calendar_user_id = None
    attorney_task = (
        await db.execute(
            select(Task).where(
                Task.tenant_id == tenant_uuid,
                Task.external_ref == attorney_external_ref,
            )
        )
    ).scalar_one_or_none()
    created_attorney_task = attorney_task is None
    assignment_changed = False
    if attorney_task is None:
        attorney_task = Task(
            tenant_id=tenant_uuid,
            title=f"Qualified intake: {caller}",
            description=attorney_description,
            task_type="intake",
            status="pending",
            priority="urgent",
            due_date=date.today(),
            contact_id=lead.contact_id,
            assigned_to_user_id=attorney.id,
            created_by_user_id=current_user.id,
            source="intake_dashboard",
            external_ref=attorney_external_ref,
        )
        db.add(attorney_task)
        await db.flush()
    else:
        previous_calendar_user_id = task_calendar_user_id(attorney_task)
        assignment_changed = attorney_task.assigned_to_user_id != attorney.id
        attorney_task.title = f"Qualified intake: {caller}"
        attorney_task.description = attorney_description
        attorney_task.task_type = "intake"
        attorney_task.status = (
            "pending" if attorney_task.status == "cancelled" else attorney_task.status
        )
        attorney_task.priority = "urgent"
        attorney_task.due_date = attorney_task.due_date or date.today()
        attorney_task.contact_id = lead.contact_id
        attorney_task.assigned_to_user_id = attorney.id

    if partner_task.status != "completed":
        partner_task.status = "completed"
        partner_task.completed_at = datetime.now(timezone.utc)
        partner_task.closed_reason = "Lead qualified and handed to attorney intake"
        partner_task.closed_by_user_id = current_user.id

    if created_attorney_task or assignment_changed:
        await record_task_event(
            db,
            attorney_task,
            event="assigned" if created_attorney_task else "reassigned",
            actor=current_user,
            note=payload.partner_notes,
        )

    await db.commit()
    await db.refresh(partner_task)
    await db.refresh(attorney_task)
    await db.refresh(lead)

    if created_attorney_task:
        await notify_task_created(db, attorney_task, tenant_id)
    else:
        await notify_task_updated(
            db,
            attorney_task,
            tenant_id,
            calendar_changed=True,
            assignment_changed=assignment_changed,
            previous_calendar_user_id=previous_calendar_user_id,
        )

    return IntakeTaskQualifyResponse(
        lead_id=lead.id,
        contact_id=lead.contact_id,
        partner_task_id=partner_task.id,
        attorney_task_id=attorney_task.id,
        assigned_to_user_id=attorney.id,
        lead_status=lead.status,
    )


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    task = await _load_task_or_404(db, task_id, uuid.UUID(tenant_id))
    if task.assigned_to_user_id == current_user.id and task.viewed_at is None:
        task.viewed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(task)
    return TaskResponse.model_validate(task)


@router.post("/{task_id}/view", response_model=TaskResponse)
async def mark_task_viewed(
    task_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read receipt: record that the assignee has seen this task (idempotent).

    Only a view by the assigned user counts — a receptionist or admin looking
    at the task list must not mark someone else's task as read.
    """
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    task = await _load_task_or_404(db, task_id, uuid.UUID(tenant_id))
    if task.assigned_to_user_id == current_user.id and task.viewed_at is None:
        task.viewed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(task)
    return TaskResponse.model_validate(task)


@router.post("/{task_id}/contacted", response_model=TaskResponse)
async def mark_customer_contacted(
    task_id: uuid.UUID,
    payload: TaskContactedRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Record that the customer was contacted for this follow-up task."""
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    task = await _load_task_or_404(db, task_id, uuid.UUID(tenant_id))
    if (
        task.assigned_to_user_id
        and task.assigned_to_user_id != current_user.id
        and current_user.role != "admin"
    ):
        raise HTTPException(
            status_code=403,
            detail="Only the assigned user or an admin can log customer contact",
        )

    now = datetime.now(timezone.utc)
    if task.customer_contacted_at is None:
        task.customer_contacted_at = now
    task.customer_contact_method = payload.method
    if task.assigned_to_user_id == current_user.id and task.viewed_at is None:
        task.viewed_at = now
    if task.status == "pending":
        task.status = "in_progress"
    if payload.note and payload.note.strip():
        contacted_by = current_user.full_name or current_user.email
        task.description = _append_section(
            task.description,
            f"Customer contact ({payload.method}, {now.strftime('%Y-%m-%d %H:%M')} UTC, {contacted_by}):",
            payload.note,
        )
    await record_customer_contact(
        db, task, method=payload.method, actor=current_user, note=payload.note
    )
    await db.commit()
    await db.refresh(task)
    return TaskResponse.model_validate(task)


@router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(Task).where(
            Task.id == task_id,
            Task.tenant_id == uuid.UUID(tenant_id),
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    previous_calendar_user_id = task_calendar_user_id(task)
    previous_assignee_id = task.assigned_to_user_id
    previous_status = task.status
    updates = payload.model_dump(exclude_none=True)
    assignment_note = (updates.pop("assignment_note", None) or "").strip() or None
    closed_reason = (updates.pop("closed_reason", None) or "").strip() or None
    calendar_changed = bool(_CALENDAR_RELEVANT_FIELDS & set(updates))
    assignment_changed = "assigned_to_user_id" in updates

    new_status = updates.get("status")
    if (
        new_status == "cancelled"
        and previous_status != "cancelled"
        and not closed_reason
    ):
        raise HTTPException(
            status_code=422,
            detail="A closed_reason is required when cancelling a task",
        )

    # Auto-set completed_at when marking complete
    if new_status == "completed" and not task.completed_at:
        task.completed_at = datetime.now(timezone.utc)
    elif new_status and new_status != "completed":
        task.completed_at = None

    for field, value in updates.items():
        setattr(task, field, value)

    reassigned = assignment_changed and task.assigned_to_user_id != previous_assignee_id
    if reassigned:
        # New assignee has not seen the task; reset the read receipt.
        task.viewed_at = None
        if assignment_note:
            assigner = current_user.full_name or current_user.email
            task.description = _append_section(
                task.description, f"Reassignment note ({assigner}):", assignment_note
            )

    closing = new_status in ("completed", "cancelled") and new_status != previous_status
    if new_status in ("completed", "cancelled"):
        if closed_reason:
            task.closed_reason = closed_reason
        if closing:
            task.closed_by_user_id = current_user.id
    elif new_status:
        # Reopened — the previous closure record no longer applies.
        task.closed_reason = None
        task.closed_by_user_id = None
    elif closed_reason and task.status in ("completed", "cancelled"):
        task.closed_reason = closed_reason

    if reassigned and task.assigned_to_user_id:
        await record_task_event(
            db,
            task,
            event="reassigned" if previous_assignee_id else "assigned",
            actor=current_user,
            note=assignment_note,
        )
    if closing:
        await record_task_event(
            db, task, event=new_status, actor=current_user, note=task.closed_reason
        )

    await db.commit()
    await db.refresh(task)

    await notify_task_updated(
        db,
        task,
        tenant_id,
        calendar_changed=calendar_changed,
        assignment_changed=reassigned,
        previous_calendar_user_id=previous_calendar_user_id,
        assignment_note=assignment_note,
    )

    return TaskResponse.model_validate(task)


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(Task).where(
            Task.id == task_id,
            Task.tenant_id == uuid.UUID(tenant_id),
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task_id_str = str(task.id)
    await db.delete(task)
    await db.commit()
    remove_task_from_calendars(task_id_str, tenant_id, task_calendar_user_id(task))


@router.post("/{task_id}/remind", status_code=202)
async def send_task_reminder(
    task_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually send a reminder email for a specific task."""
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(Task).where(
            Task.id == task_id,
            Task.tenant_id == uuid.UUID(tenant_id),
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if not task.assigned_to_user_id:
        raise HTTPException(
            status_code=422, detail="Task has no assigned user — cannot send reminder"
        )

    user_result = await db.execute(
        select(User).where(User.id == task.assigned_to_user_id)
    )
    assignee = user_result.scalar_one_or_none()
    if not assignee or not assignee.email:
        raise HTTPException(
            status_code=422, detail="Assigned user has no email address"
        )

    due_str = task.due_date.isoformat() if task.due_date else "No due date"
    sent = await email_service.send_task_reminder(
        to_email=assignee.email,
        task_title=task.title,
        due_date=due_str,
        assignee_name=getattr(assignee, "full_name", None),
    )

    if not sent:
        status_code, detail = email_delivery_http_error(
            sent,
            action="Task reminder",
        )
        raise HTTPException(status_code=status_code, detail=detail)

    return {"sent": True, "to": assignee.email}
