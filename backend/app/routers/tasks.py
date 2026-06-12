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

import asyncio
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Coroutine, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.task import Task
from app.models.user import User
from app.services import google_calendar, microsoft_calendar
from app.services.email import email_service
from app.schemas.task import TaskCreate, TaskListResponse, TaskResponse, TaskUpdate

logger = logging.getLogger(__name__)

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


def _fire_calendar_sync(coro: Coroutine, *, task_id: str, provider: str) -> None:
    """Fire-and-forget a calendar push, logging (not dropping) failures."""

    async def _run() -> None:
        try:
            await coro
        except Exception as exc:
            logger.warning(
                "Calendar sync failed for task %s (provider=%s): %s",
                task_id,
                provider,
                exc,
            )

    asyncio.create_task(_run())


def _task_calendar_user_id(task: Task) -> str | None:
    user_id = task.assigned_to_user_id or task.created_by_user_id
    return str(user_id) if user_id else None


def _push_task_to_calendars(task: Task, tenant_id: str) -> None:
    """Fire-and-forget upsert of a task's event to Google and Microsoft."""
    if not task.due_date:
        return
    task_id = str(task.id)
    is_completed = task.status == "completed"
    user_id = _task_calendar_user_id(task)
    kwargs = dict(
        tenant_id=tenant_id,
        task_id=task_id,
        title=task.task_type or task.title or "",
        due_date=task.due_date.isoformat(),
        description=task.description or "",
        is_completed=is_completed,
        user_id=user_id,
    )
    _fire_calendar_sync(
        google_calendar.upsert_task_event(**kwargs), task_id=task_id, provider="google"
    )
    _fire_calendar_sync(
        microsoft_calendar.upsert_task_event(**kwargs),
        task_id=task_id,
        provider="microsoft",
    )


def _remove_task_from_calendars(
    task_id: str, tenant_id: str, user_id: str | None = None
) -> None:
    """Fire-and-forget removal of a task's event from Google and Microsoft."""
    _fire_calendar_sync(
        google_calendar.delete_task_event(
            tenant_id=tenant_id, task_id=task_id, user_id=user_id
        ),
        task_id=task_id,
        provider="google",
    )
    _fire_calendar_sync(
        microsoft_calendar.delete_task_event(
            tenant_id=tenant_id, task_id=task_id, user_id=user_id
        ),
        task_id=task_id,
        provider="microsoft",
    )


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

    task = Task(
        tenant_id=uuid.UUID(tenant_id),
        created_by_user_id=current_user.id,
        **payload.model_dump(exclude_none=True),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # Fire-and-forget: push to Google + Microsoft calendars if due_date is set
    _push_task_to_calendars(task, tenant_id)

    return TaskResponse.model_validate(task)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
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

    previous_calendar_user_id = _task_calendar_user_id(task)
    updates = payload.model_dump(exclude_none=True)
    calendar_changed = bool(_CALENDAR_RELEVANT_FIELDS & set(updates))
    assignment_changed = "assigned_to_user_id" in updates

    # Auto-set completed_at when marking complete
    if updates.get("status") == "completed" and not task.completed_at:
        task.completed_at = datetime.now(timezone.utc)
    elif updates.get("status") and updates["status"] != "completed":
        task.completed_at = None

    for field, value in updates.items():
        setattr(task, field, value)

    await db.commit()
    await db.refresh(task)

    if calendar_changed:
        if assignment_changed and previous_calendar_user_id:
            _remove_task_from_calendars(
                str(task.id), tenant_id, previous_calendar_user_id
            )
        if task.status == "cancelled" or not task.due_date:
            _remove_task_from_calendars(
                str(task.id), tenant_id, _task_calendar_user_id(task)
            )
        else:
            _push_task_to_calendars(task, tenant_id)

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
    _remove_task_from_calendars(
        task_id_str, tenant_id, _task_calendar_user_id(task)
    )


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
        raise HTTPException(status_code=502, detail="Failed to send reminder email")

    return {"sent": True, "to": assignee.email}
