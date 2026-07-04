"""Document task lifecycle events in the contact's communication history.

Assignment, reassignment, customer contact, and closure of a contact-linked
task each append a ``CommunicationLog`` row, so the caller's history shows the
full follow-up trail (who was assigned, whether the customer was reached, and
why the task was closed) — not just the calls themselves.

Tasks with no ``contact_id`` are skipped: the communication log is the
customer's history, not an internal audit trail.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.communication_log import CommunicationLog
from app.models.task import Task
from app.models.user import User

# Customer-contact methods that map 1:1 onto CommunicationLog channels.
_CONTACT_CHANNELS = {"call", "email", "sms", "meeting"}

_EVENT_SUBJECTS = {
    "assigned": "Task assigned",
    "reassigned": "Task reassigned",
    "completed": "Task completed",
    "cancelled": "Task cancelled",
}


async def _user_label(db: AsyncSession, task: Task, user_id) -> str | None:
    if not user_id:
        return None
    user = (
        await db.execute(
            select(User).where(User.id == user_id, User.tenant_id == task.tenant_id)
        )
    ).scalar_one_or_none()
    if not user:
        return None
    return user.full_name or user.email


async def record_task_event(
    db: AsyncSession,
    task: Task,
    *,
    event: str,
    actor=None,
    actor_user_id=None,
    note: str | None = None,
) -> CommunicationLog | None:
    """Append a task lifecycle event to the linked contact's history.

    ``event`` is one of ``assigned``/``reassigned``/``completed``/``cancelled``.
    ``actor`` is the acting User when the caller has one; ``actor_user_id`` is
    the fallback for flows that only carry an id. Adds the row to the session;
    the caller commits.
    """
    if not task.contact_id or event not in _EVENT_SUBJECTS:
        return None

    actor_label = None
    if actor is not None:
        actor_label = getattr(actor, "full_name", None) or getattr(actor, "email", None)
    elif actor_user_id:
        actor_label = await _user_label(db, task, actor_user_id)
    assignee_label = await _user_label(db, task, task.assigned_to_user_id)

    lines = []
    if event in ("assigned", "reassigned") and assignee_label:
        lines.append(f"Assigned to: {assignee_label}")
    if actor_label:
        by = "Closed by" if event in ("completed", "cancelled") else "Assigned by"
        lines.append(f"{by}: {actor_label}")
    if note and note.strip():
        label = "Reason" if event in ("completed", "cancelled") else "Note"
        lines.append(f"{label}: {note.strip()}")

    log = CommunicationLog(
        tenant_id=task.tenant_id,
        direction="outbound",
        channel="other",
        status="logged",
        subject=f"{_EVENT_SUBJECTS[event]}: {task.title}",
        body="\n".join(lines) or None,
        contact_id=task.contact_id,
        matter_id=task.matter_id,
        created_by_user_id=getattr(actor, "id", None) or actor_user_id,
        external_ref=f"task:{task.id}:{event}",
    )
    db.add(log)
    return log


async def record_customer_contact(
    db: AsyncSession,
    task: Task,
    *,
    method: str,
    actor,
    note: str | None = None,
) -> CommunicationLog | None:
    """Append a customer-contact event (Log Contact) to the contact's history."""
    if not task.contact_id:
        return None

    channel = method if method in _CONTACT_CHANNELS else "other"
    actor_label = None
    if actor is not None:
        actor_label = getattr(actor, "full_name", None) or getattr(actor, "email", None)

    lines = []
    if actor_label:
        lines.append(f"Contacted by: {actor_label}")
    if note and note.strip():
        lines.append(note.strip())

    log = CommunicationLog(
        tenant_id=task.tenant_id,
        direction="outbound",
        channel=channel,
        status="logged",
        subject=f"Customer contacted ({method}): {task.title}",
        body="\n".join(lines) or None,
        contact_id=task.contact_id,
        matter_id=task.matter_id,
        created_by_user_id=getattr(actor, "id", None),
        external_ref=f"task:{task.id}:contacted",
    )
    db.add(log)
    return log
