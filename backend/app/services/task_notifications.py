"""Task notification helpers shared by task and intake flows."""

from __future__ import annotations

import asyncio
import logging
from typing import Coroutine

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.contact import Contact
from app.models.plugin import Matter
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.user import User
from app.services import google_calendar, microsoft_calendar
from app.services.email import EmailDeliveryResult, email_service
from app.services.task_visibility import task_contains_sms

logger = logging.getLogger(__name__)
settings = get_settings()


async def _demo_notifications_disabled(db: AsyncSession, tenant_id: str) -> bool:
    """Keep task-created calendar/email notifications inside demo workspaces."""
    billing_tier = await db.scalar(
        select(Tenant.billing_tier).where(Tenant.id == tenant_id)
    )
    return billing_tier == "demo"


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


def _user_label(user: User | None) -> str | None:
    if not user:
        return None
    return user.full_name or user.email


def _format_task_created_at(task: Task) -> str:
    if not task.created_at:
        return "Unknown"
    return task.created_at.strftime("%B %d, %Y %H:%M UTC")


def _format_task_due(task: Task) -> str:
    if not task.due_date:
        return "No due date"
    due = task.due_date.isoformat()
    if task.due_time:
        due = f"{due} {task.due_time.strftime('%H:%M')}"
    return due


def _task_url(task: Task) -> str:
    return f"{settings.FRONTEND_URL.rstrip('/')}/tasks/{task.id}"


def _calendar_description(
    task: Task,
    *,
    creator_name: str | None = None,
    customer_name: str | None = None,
    task_url: str | None = None,
) -> str:
    lines = [task.description or ""]
    metadata = [
        f"Created by: {creator_name}" if creator_name else "",
        f"Customer: {customer_name}" if customer_name else "",
        f"Task link: {task_url}" if task_url else "",
    ]
    metadata = [line for line in metadata if line]
    if metadata:
        if lines[0]:
            lines.append("")
        lines.extend(metadata)
    return "\n".join(line for line in lines if line)


async def _load_task_context(
    db: AsyncSession, task: Task
) -> tuple[User | None, Contact | None, Matter | None]:
    creator = None
    if task.created_by_user_id:
        creator = (
            await db.execute(
                select(User).where(
                    User.id == task.created_by_user_id,
                    User.tenant_id == task.tenant_id,
                )
            )
        ).scalar_one_or_none()
    contact = None
    if task.contact_id:
        contact = (
            await db.execute(
                select(Contact).where(
                    Contact.id == task.contact_id,
                    Contact.tenant_id == task.tenant_id,
                )
            )
        ).scalar_one_or_none()
    matter = None
    if task.matter_id:
        matter = (
            await db.execute(
                select(Matter).where(
                    Matter.id == task.matter_id,
                    Matter.tenant_id == task.tenant_id,
                )
            )
        ).scalar_one_or_none()
    return creator, contact, matter


def push_task_to_calendars(
    task: Task,
    tenant_id: str,
    *,
    creator_name: str | None = None,
    customer_name: str | None = None,
    task_url: str | None = None,
) -> None:
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
        description=_calendar_description(
            task,
            creator_name=creator_name,
            customer_name=customer_name,
            task_url=task_url,
        ),
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
        google_calendar.delete_task_event(
            tenant_id=tenant_id, task_id=task_id, user_id=user_id
        ),
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


async def remove_task_from_calendars_now(
    task_id: str, tenant_id: str, user_id: str | None = None
) -> tuple[object, object]:
    """Synchronously remove calendar copies before revoking task access."""
    results = await asyncio.gather(
        google_calendar.delete_task_event(
            tenant_id=tenant_id,
            task_id=task_id,
            user_id=user_id,
            require_exact_user=True,
        ),
        microsoft_calendar.delete_task_event(
            tenant_id=tenant_id,
            task_id=task_id,
            user_id=user_id,
            require_exact_user=True,
        ),
        return_exceptions=True,
    )
    failures: list[Exception] = []
    for provider, result in zip(("google", "microsoft"), results, strict=True):
        if isinstance(result, Exception) or result is not True:
            failure = (
                result
                if isinstance(result, Exception)
                else RuntimeError("Calendar cleanup was not verified")
            )
            failures.append(failure)
            logger.warning(
                "%s calendar cleanup failed for SMS task %s (%s)",
                provider,
                task_id,
                type(failure).__name__,
            )
    if failures:
        raise RuntimeError("External calendar cleanup did not complete") from failures[
            0
        ]
    return results[0], results[1]


async def send_task_assignment_alert(
    db: AsyncSession, task: Task, assignment_note: str | None = None
) -> EmailDeliveryResult | bool:
    """Send an immediate email alert when a task is assigned to a user."""
    if await _demo_notifications_disabled(db, str(task.tenant_id)):
        return EmailDeliveryResult.NOT_REQUIRED
    if not task.assigned_to_user_id:
        return EmailDeliveryResult.NOT_REQUIRED
    if await task_contains_sms(db, task):
        logger.info(
            "Task %s assignment alert skipped: SMS review content stays in LawHand",
            task.id,
        )
        return EmailDeliveryResult.NOT_REQUIRED
    assignee = (
        await db.execute(
            select(User).where(
                User.id == task.assigned_to_user_id,
                User.tenant_id == task.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not assignee or not assignee.email:
        logger.info("Task %s assignment alert skipped: assignee has no email", task.id)
        return EmailDeliveryResult.INVALID_RECIPIENT
    creator = None
    if task.created_by_user_id:
        creator = (
            await db.execute(
                select(User).where(
                    User.id == task.created_by_user_id,
                    User.tenant_id == task.tenant_id,
                )
            )
        ).scalar_one_or_none()
    contact = None
    if task.contact_id:
        contact = (
            await db.execute(
                select(Contact).where(
                    Contact.id == task.contact_id,
                    Contact.tenant_id == task.tenant_id,
                )
            )
        ).scalar_one_or_none()
    matter = None
    if task.matter_id:
        matter = (
            await db.execute(
                select(Matter).where(
                    Matter.id == task.matter_id,
                    Matter.tenant_id == task.tenant_id,
                )
            )
        ).scalar_one_or_none()

    return await email_service.send_task_assignment_alert(
        to_email=assignee.email,
        task_title=task.title,
        due_date=_format_task_due(task),
        priority=task.priority,
        task_type=task.task_type,
        description=task.description,
        assignee_name=assignee.full_name or assignee.email,
        created_by_name=_user_label(creator),
        created_at=_format_task_created_at(task),
        customer_name=contact.display_name if contact else None,
        matter_name=matter.matter_name if matter else None,
        source=task.source,
        assigner_note=assignment_note,
        task_url=_task_url(task),
    )


async def notify_task_created(
    db: AsyncSession,
    task: Task,
    tenant_id: str,
    assignment_note: str | None = None,
) -> EmailDeliveryResult | bool:
    """Notify external systems and assignee after a new task is created."""
    if await _demo_notifications_disabled(db, tenant_id):
        return EmailDeliveryResult.NOT_REQUIRED
    if await task_contains_sms(db, task):
        # SMS proposals contain phone/body and are intentionally never copied to
        # assignment email or third-party calendars. Review stays in LawHand.
        return EmailDeliveryResult.NOT_REQUIRED
    creator, contact, _matter = await _load_task_context(db, task)
    push_task_to_calendars(
        task,
        tenant_id,
        creator_name=_user_label(creator),
        customer_name=contact.display_name if contact else None,
        task_url=_task_url(task),
    )
    if task.assigned_to_user_id:
        return await send_task_assignment_alert(db, task, assignment_note)
    return EmailDeliveryResult.NOT_REQUIRED


async def notify_task_updated(
    db: AsyncSession,
    task: Task,
    tenant_id: str,
    *,
    calendar_changed: bool,
    assignment_changed: bool,
    previous_calendar_user_id: str | None = None,
    assignment_note: str | None = None,
) -> EmailDeliveryResult | bool:
    """Notify external systems after a task update."""
    if await _demo_notifications_disabled(db, tenant_id):
        return EmailDeliveryResult.NOT_REQUIRED
    if await task_contains_sms(db, task):
        # New SMS proposals never create calendar copies. Ordinary task updates
        # must not return a false 500 after their state/run commit because a
        # legacy provider cleanup failed; access revocation owns the explicit,
        # fail-closed legacy cleanup boundary.
        return EmailDeliveryResult.NOT_REQUIRED
    if calendar_changed:
        if assignment_changed and previous_calendar_user_id:
            remove_task_from_calendars(
                str(task.id), tenant_id, previous_calendar_user_id
            )
        if task.status == "cancelled" or not task.due_date:
            remove_task_from_calendars(
                str(task.id), tenant_id, task_calendar_user_id(task)
            )
        else:
            push_task_to_calendars(task, tenant_id)
    if assignment_changed and task.assigned_to_user_id:
        return await send_task_assignment_alert(db, task, assignment_note)
    return EmailDeliveryResult.NOT_REQUIRED
