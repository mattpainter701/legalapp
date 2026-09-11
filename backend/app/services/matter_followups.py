"""Assigned follow-up tasks for anything a matter is waiting on a client for.

Intake grew the only version of this: a dated requirement raises one task,
keyed so a reconcile pass can run repeatedly without duplicating it, and closes
that task when the paperwork arrives. Nothing past intake could borrow it, so a
signature request sent in month four had no deadline and nothing chased it.

The identity rule is the important part. A task's id is ``uuid5(namespace,
kind)`` -- derived, never random -- so "ensure" is genuinely idempotent from
any number of concurrent passes, and "close" can find the task again from the
same two values without storing a link.
"""

import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from app.models.task import Task
from app.models.user import User
from app.services.matter_access import can_access_matter
from app.services.task_workflow import append_task_event, transition_task


def followup_task_id(namespace: uuid.UUID, kind: str) -> uuid.UUID:
    return uuid.uuid5(namespace, kind)


def _local(due, timezone_name):
    try:
        return due.astimezone(ZoneInfo(timezone_name or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        # A deadline still has to land somewhere sensible when a stored
        # timezone stops resolving; UTC is wrong by hours, absent is wrong
        # by the whole task.
        return due.astimezone(ZoneInfo("UTC"))


async def _assignable(db, *, tenant_id, matter_id, owner_id):
    """The owner, but only if they are still able to open this matter."""
    if owner_id is None:
        return None
    owner = await db.scalar(
        select(User).where(
            User.id == owner_id,
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
        )
    )
    if not owner:
        return None
    allowed = await can_access_matter(
        db,
        tenant_id=tenant_id,
        user_id=owner.id,
        is_admin=owner.role == "admin",
        matter_id=matter_id,
    )
    return owner.id if allowed else None


async def ensure_followup_task(
    db,
    *,
    tenant_id,
    matter_id,
    namespace,
    kind,
    title,
    due,
    timezone_name="UTC",
    owner_id=None,
    created_by=None,
    contact_id=None,
    description=None,
    source="matter",
    external_ref=None,
    priority="high",
):
    """Create the follow-up for ``kind`` if it does not already exist."""
    task_id = followup_task_id(namespace, kind)
    task = await db.scalar(
        select(Task).where(Task.id == task_id, Task.tenant_id == tenant_id)
    )
    if task is not None:
        return task
    local = _local(due, timezone_name)
    task = Task(
        id=task_id,
        tenant_id=tenant_id,
        matter_id=matter_id,
        contact_id=contact_id,
        title=title,
        description=description or f"Follow-up due {due.isoformat()}.",
        task_type="follow_up",
        status="pending",
        priority=priority,
        due_date=local.date(),
        due_time=local.time().replace(tzinfo=None),
        assigned_to_user_id=await _assignable(
            db, tenant_id=tenant_id, matter_id=matter_id, owner_id=owner_id
        ),
        created_by_user_id=created_by,
        source=source,
        external_ref=external_ref or f"{source}:{namespace}:{kind}",
    )
    db.add(task)
    await db.flush()
    append_task_event(
        db,
        task,
        event_type="created",
        actor_user_id=created_by,
        to_status="pending",
        note=title,
    )
    return task


async def close_followup_task(
    db, *, tenant_id, namespace, kind, reason, actor_user_id=None
):
    """Cancel the follow-up for ``kind``. Returns the task when one closed."""
    task = await db.scalar(
        select(Task).where(
            Task.id == followup_task_id(namespace, kind), Task.tenant_id == tenant_id
        )
    )
    if task is None or task.status in ("completed", "cancelled"):
        return None
    transition_task(
        db,
        task,
        to_status="cancelled",
        actor_user_id=actor_user_id,
        reason=reason,
    )
    return task
