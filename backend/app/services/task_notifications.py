"""Task notification helpers shared by task and intake flows."""

from __future__ import annotations

import asyncio
import logging
from typing import Coroutine

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task
from app.models.user import User
from app.services import google_calendar, microsoft_calendar
from app.services.email import email_service

logger = logging.getLogger(__name__)


def _fire_and_log(coro: Coroutine, *, task_id: str, action: str) -> None:
    """Run notification work in the background and log failures."""

    async def _run() -> None:
        try:
            await coro
        except Exception as exc:
            logger.warning(
                "Task notification failed for task %s (action=%s): %s",
                task_id,
                action,
                exc,
            )

    asyncio.create_task(_run())


def task_calendar_user_id(task: Task) -> str | None:
    user_id = task.assigned_to_user_id or task.created_by_user_id
    return str(user_id) if user_id else None


def push_task_to_calendars(task: Task, tenant_id: str) -> None:
    """Fire-and-forget upsert of a task's event to Google and Microsoft."""
    if not task.due_date:
        return
    task_id = str(task.id)
    is_completed = task.status == "completed"
    user_id = task_calendar_user_id(task)
    kwargs = dict(
        tenant_id=tenant_id,
        task_id=task_id,
        title=task.title or task.task_type or "",
        due_date=task.due_date.isoformat(),
        description=task.description or "",
        is_completed=is_completed,
        user_id=user_id,
    )
    _fire_and_log(
        google_calendar.upsert_task_event(**kwargs),
        task_id=task_id,
        action="google-calendar-upsert",
    )
    _fire_and_log(
        microsoft_calendar.upsert_task_event(**kwargs),
        task_id=task_id,
        action="microsoft-calendar-upsert",
    )


def remove_task_from_calendars(
    task_id: str, tenant_id: str, user_id: str | None = None
) -> None:
    """Fire-and-forget removal of a task's event from Google and Microsoft."""
    _fire_and_log(
        google_calendar.delete_task_event(tenant_id=tenant_id, task_id=task_id, user_id=user_id),
        task_id=task_id,
        action="google-calendar-delete",
    )
    _fire_and_log(
        microsoft_calendar.delete_task_event(
            tenant_id=tenant_id, task_id=task_id, user_id=user_id
        ),
        task_id=task_id,
        action="microsoft-calendar-delete",
    )


async def send_task_assignment_alert(db: AsyncSession, task: Task) -> bool:
    """Send an immediate email alert when a task is assigned to a user."""
    if not task.assigned_to_user_id:
        return False
    assignee = (
        await db.execute(select(User).where(User.id == task.assigned_to_user_id))
    ).scalar_one_or_none()
    if not assignee or not assignee.email:
        logger.info("Task %s assignment alert skipped: assignee has no email", task.id)
        return False

    return await email_service.send_task_assignment_alert(
        to_email=assignee.email,
        task_title=task.title,
        due_date=task.due_date.isoformat() if task.due_date else "No due date",
        priority=task.priority,
        task_type=task.task_type,
        description=task.description,
        assignee_name=assignee.full_name or assignee.email,
    )


async def notify_task_created(db: AsyncSession, task: Task, tenant_id: str) -> bool:
    """Notify external systems and assignee after a new task is created."""
    push_task_to_calendars(task, tenant_id)
    if task.assigned_to_user_id:
        return await send_task_assignment_alert(db, task)
    return False


async def notify_task_updated(
    db: AsyncSession,
    task: Task,
    tenant_id: str,
    *,
    calendar_changed: bool,
    assignment_changed: bool,
    previous_calendar_user_id: str | None = None,
) -> None:
    """Notify external systems after a task update."""
    if calendar_changed:
        if assignment_changed and previous_calendar_user_id:
            remove_task_from_calendars(str(task.id), tenant_id, previous_calendar_user_id)
        if task.status == "cancelled" or not task.due_date:
            remove_task_from_calendars(str(task.id), tenant_id, task_calendar_user_id(task))
        else:
            push_task_to_calendars(task, tenant_id)
    if assignment_changed and task.assigned_to_user_id:
        return await send_task_assignment_alert(db, task)
    return False
