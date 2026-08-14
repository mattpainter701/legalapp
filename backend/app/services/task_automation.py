"""Deterministic execution of an approved task's ``pending_action``.

This is the half of the chat action layer with no model in it. The assistant may
draft a client email, but a human approves it on the work board and *this* code
sends it — so an outbound message never depends on model behavior at send time.

Exactly-once is the central guarantee. A `task_automation_runs` row is the claim
on the work, and the unique constraint on ``(task_id, idempotency_key)`` is what
makes a double-clicked Approve, a replayed transition, or two concurrent
requests send one email instead of several. The claim is taken in a single
atomic statement before any send.

A failed run stays as evidence and may be retried; a pending or succeeded run
blocks every later attempt.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker, set_tenant_context
from app.models.task import Task, TaskAutomationRun
from app.schemas.chat_action import EmailClientAction
from app.services.connected_mail import send_client_email
from app.services.email import email_service
from app.services.task_workflow import append_task_event

logger = logging.getLogger(__name__)

# Approval is a specific move, not merely leaving Review. Cancelling a drafted
# client email must never send it — that inverts the attorney's intent — and
# parking it in Waiting or closing the task without acting are not approvals
# either. Only accepting the draft into active work executes it.
APPROVAL_FROM_STATUS = "review"
APPROVAL_TO_STATUSES = frozenset({"in_progress"})

TASK_AUTOMATION_JOB = "task_automation"

# Delivery states an attorney may see. Only "sent" means the client was contacted.
DELIVERY_STATUSES = ("queued", "sending", "sent", "failed")
TERMINAL_DELIVERY_STATUSES = ("sent", "failed")


def transition_is_approval(from_status: str | None, to_status: str | None) -> bool:
    """Whether this status change means "execute the drafted action".

    The single definition both status-changing endpoints consult. Keeping it here
    rather than in a router is the point: the rule was previously expressed at
    the call site, where the transition endpoint sent on cancellation and the
    generic PATCH approved without ever executing.
    """
    return from_status == APPROVAL_FROM_STATUS and to_status in APPROVAL_TO_STATUSES


def automation_idempotency_key(task: Task, from_status: str) -> str:
    """Identify one approval event.

    Keyed on the task version at approval, so approving the same drafted action
    twice collides, while a genuinely new approval after an edit (which bumps
    the version) is allowed to run.
    """
    return f"approve:{from_status}:v{task.version or 0}"


async def enqueue_automation_run(
    db: AsyncSession,
    task: Task,
    *,
    from_status: str,
    actor_user_id=None,
) -> TaskAutomationRun | None:
    """Record the intent to send, inside the caller's transaction.

    This is what makes delivery durable. The queued row commits atomically with
    the status change, so a process that dies between approval and send leaves
    behind a record the worker will pick up — previously that send was simply
    lost, with the attorney told it was approved.

    Does not commit: the caller owns the transaction, which is the whole point.
    """
    action_type = str((task.pending_action or {}).get("type") or "")
    if not action_type:
        return None
    stmt = (
        pg_insert(TaskAutomationRun)
        .values(
            tenant_id=task.tenant_id,
            task_id=task.id,
            action_type=action_type,
            idempotency_key=automation_idempotency_key(task, from_status),
            status="queued",
            triggered_by_user_id=actor_user_id,
        )
        # An existing row already covers this approval — including one still
        # sending or already sent. Never reset it here.
        .on_conflict_do_nothing(constraint="uq_task_automation_runs_task_key")
        .returning(TaskAutomationRun.id)
    )
    await db.execute(stmt)
    return None


async def _claim_run(
    db: AsyncSession,
    task: Task,
    *,
    action_type: str,
    idempotency_key: str,
    actor_user_id,
) -> TaskAutomationRun | None:
    """Atomically move a run from queued/failed into sending, or return None.

    A conditional UPDATE, so concurrency is resolved by Postgres rather than by
    read-then-write in application code. ``sending`` is excluded so a concurrent
    attempt cannot start a second send, and ``sent`` is excluded so nothing can
    resend. Losing this race is the expected exactly-once path, not an error.

    Upserts a row when none exists so a direct call still works without a prior
    enqueue; the unique constraint keeps that safe under concurrency.
    """
    claim = (
        pg_insert(TaskAutomationRun)
        .values(
            tenant_id=task.tenant_id,
            task_id=task.id,
            action_type=action_type,
            idempotency_key=idempotency_key,
            status="sending",
            triggered_by_user_id=actor_user_id,
        )
        .on_conflict_do_update(
            constraint="uq_task_automation_runs_task_key",
            set_={
                "status": "sending",
                "error_message": None,
                "triggered_by_user_id": actor_user_id,
                "completed_at": None,
            },
            where=TaskAutomationRun.status.in_(("queued", "failed")),
        )
        .returning(TaskAutomationRun.id)
    )
    run_id = await db.scalar(claim)
    if run_id is None:
        return None
    await db.commit()
    return await db.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.id == run_id)
    )


# ── Action handlers ─────────────────────────────────────────────────────────


async def _run_email_client(
    db: AsyncSession,
    task: Task,
    payload: dict[str, Any],
    actor_user_id,
) -> tuple[bool, str]:
    """Send the approved client email. Returns (succeeded, detail)."""
    try:
        action = EmailClientAction.model_validate(payload)
    except ValidationError:
        # The payload was written by this system, so an invalid one means a bug
        # or tampering. Never improvise a partial send.
        return False, "The stored email draft is not a valid action payload"

    delivery = await send_client_email(
        db,
        tenant_id=task.tenant_id,
        actor_user_id=actor_user_id,
        to=list(action.to),
        subject=action.subject,
        html_body=_body_to_html(action.body),
        text_body=action.body,
        smtp_service=email_service,
    )
    if delivery.result:
        return True, delivery.detail
    # DISABLED/UNCONFIGURED must not be recorded as a send: the attorney would
    # believe the client was contacted. Leaving the run failed also keeps it
    # retryable once delivery is configured.
    return False, delivery.detail


def _body_to_html(body: str) -> str:
    from html import escape

    paragraphs = [
        f"<p>{escape(block).replace(chr(10), '<br>')}</p>"
        for block in body.split("\n\n")
        if block.strip()
    ]
    return "".join(paragraphs) or f"<p>{escape(body)}</p>"


ACTION_HANDLERS: dict[
    str,
    Callable[[AsyncSession, Task, dict, Any], Awaitable[tuple[bool, str]]],
] = {
    "email_client": _run_email_client,
}


# ── Entry point ─────────────────────────────────────────────────────────────


async def run_task_automation(
    task_id,
    tenant_id,
    *,
    from_status: str,
    to_status: str,
    actor_user_id=None,
) -> None:
    """Execute an approved task's pending action, at most once.

    Runs after the transition has already been committed, on its own session,
    because the request session is gone by then. A failure here records evidence
    and never rolls the task back: the attorney's approval is a real decision
    that happened, and hiding it would be worse than a visible failed run.

    Re-checks the approval rule itself rather than trusting the caller, so a new
    call site cannot reintroduce a send-on-cancel.
    """
    if not transition_is_approval(from_status, to_status):
        return

    async with async_session_maker() as db:
        try:
            await set_tenant_context(db, str(tenant_id))
            task = await db.scalar(
                select(Task).where(
                    Task.id == task_id,
                    Task.tenant_id == tenant_id,
                )
            )
            if task is None or not task.pending_action:
                return

            action_type = str(task.pending_action.get("type") or "")
            handler = ACTION_HANDLERS.get(action_type)
            if handler is None:
                # Fail closed on an unknown action rather than guessing.
                logger.warning(
                    "task_automation_unknown_action task_id=%s action_type=%r",
                    task_id,
                    action_type,
                )
                return

            run = await _claim_run(
                db,
                task,
                action_type=action_type,
                idempotency_key=automation_idempotency_key(task, from_status),
                actor_user_id=actor_user_id,
            )
            if run is None:
                # Already claimed or already done. This is the exactly-once path
                # and is expected under double-click, not an error.
                logger.info(
                    "task_automation_already_claimed task_id=%s action_type=%s",
                    task_id,
                    action_type,
                )
                return

            try:
                succeeded, detail = await handler(
                    db, task, dict(task.pending_action), actor_user_id
                )
            except Exception as exc:
                succeeded, detail = False, f"{type(exc).__name__}: {exc}"[:500]
                logger.warning(
                    "task_automation_handler_raised task_id=%s action_type=%s",
                    task_id,
                    action_type,
                    exc_info=True,
                )

            run.status = "sent" if succeeded else "failed"
            run.error_message = None if succeeded else detail[:500]
            run.completed_at = datetime.now(timezone.utc)
            append_task_event(
                db,
                task,
                event_type=(
                    "automation_succeeded" if succeeded else "automation_failed"
                ),
                actor_user_id=actor_user_id,
                note=None if succeeded else detail[:500],
                metadata={"action_type": action_type, "detail": detail[:200]},
            )
            if succeeded:
                # Clear the draft so a later manual transition of the same task
                # cannot re-enter the automation path at all.
                task.pending_action = None
            await db.commit()
        except Exception:
            # Never let background automation escape and take down the worker.
            logger.warning(
                "task_automation_failed task_id=%s tenant_id=%s",
                task_id,
                tenant_id,
                exc_info=True,
            )
            await db.rollback()


def dispatch_task_automation_if_approved(
    task: Task,
    *,
    from_status: str | None,
    to_status: str | None,
    actor_user_id=None,
) -> bool:
    """Start the automation for an approving transition. Returns whether it did.

    The one entry point every status-changing endpoint uses, so the definition of
    approval cannot drift between them.

    Two mechanisms, deliberately: the durable job guarantees the send eventually
    happens even if this process dies, and the immediate attempt makes it happen
    now rather than on the worker's next drain. Running both is safe precisely
    because execution is exactly-once — whichever gets there second finds the run
    already claimed and no-ops.
    """
    if not transition_is_approval(from_status, to_status):
        return False
    if not task.pending_action:
        return False

    # Imported here to avoid a cycle: task_notifications imports task models.
    from app.services.task_notifications import _fire_and_log

    _fire_and_log(
        run_task_automation(
            task.id,
            task.tenant_id,
            from_status=from_status,
            to_status=to_status,
            actor_user_id=actor_user_id,
        ),
        task_id=str(task.id),
        action="pending_action",
    )
    return True


async def enqueue_durable_automation(
    db: AsyncSession,
    task: Task,
    *,
    from_status: str | None,
    to_status: str | None,
    actor_user_id=None,
) -> bool:
    """Persist the send intent in the caller's transaction. Must precede commit.

    Writes both the queued run (the attorney-visible delivery state) and a
    durable job (the worker's instruction). Both are idempotent and neither
    commits, so they land atomically with the approval or not at all — there is
    no window in which a task is approved but the send is unrecorded.
    """
    if not transition_is_approval(from_status, to_status):
        return False
    if not task.pending_action:
        return False

    from app.services.durable_jobs import enqueue_job

    await enqueue_automation_run(
        db, task, from_status=from_status, actor_user_id=actor_user_id
    )
    await enqueue_job(
        db,
        tenant_id=task.tenant_id,
        kind=TASK_AUTOMATION_JOB,
        # Same key as the run, so a retried approval reuses one job.
        idempotency_key=f"{task.id}:{automation_idempotency_key(task, from_status)}",
        payload={
            "task_id": str(task.id),
            "from_status": from_status,
            "to_status": to_status,
            "actor_user_id": str(actor_user_id) if actor_user_id else None,
        },
    )
    return True


async def run_task_automation_job(row) -> dict[str, Any]:
    """Durable-job entry point.

    Safe to run after an immediate attempt already succeeded: the claim will not
    match a ``sent`` run, so this becomes a no-op rather than a second send. The
    tenant comes from the job row, never from its payload.
    """
    payload = row.payload or {}
    task_id = payload.get("task_id")
    if not task_id:
        return {"ignored": "missing_task_id"}
    await run_task_automation(
        task_id,
        row.tenant_id,
        from_status=str(payload.get("from_status") or ""),
        to_status=str(payload.get("to_status") or ""),
        actor_user_id=payload.get("actor_user_id"),
    )
    return {"task_id": task_id}
