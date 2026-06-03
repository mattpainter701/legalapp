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

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.task import Task
from app.schemas.task import TaskCreate, TaskListResponse, TaskResponse, TaskUpdate

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("/overdue", response_model=TaskListResponse)
async def get_overdue_tasks(
    matter_id: Optional[uuid.UUID] = None,
    assigned_to: Optional[uuid.UUID] = None,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
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

    tenant_id = current_user["tenant_id"]
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
    tenant_id = current_user["tenant_id"]
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
    tenant_id = current_user["tenant_id"]
    await set_tenant_context(db, tenant_id)

    task = Task(
        tenant_id=uuid.UUID(tenant_id),
        created_by_user_id=uuid.UUID(current_user["user_id"]),
        **payload.model_dump(exclude_none=True),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return TaskResponse.model_validate(task)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
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
    tenant_id = current_user["tenant_id"]
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

    updates = payload.model_dump(exclude_none=True)

    # Auto-set completed_at when marking complete
    if updates.get("status") == "completed" and not task.completed_at:
        task.completed_at = datetime.now(timezone.utc)
    elif updates.get("status") and updates["status"] != "completed":
        task.completed_at = None

    for field, value in updates.items():
        setattr(task, field, value)

    await db.commit()
    await db.refresh(task)
    return TaskResponse.model_validate(task)


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
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

    await db.delete(task)
    await db.commit()
