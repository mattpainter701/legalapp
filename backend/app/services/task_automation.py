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
from app.services.email import email_service
from app.services.task_workflow import append_task_event

logger = logging.getLogger(__name__)

# Only a transition out of Review counts as approval of the drafted action.
APPROVAL_FROM_STATUS = "review"


def automation_idempotency_key(task: Task, from_status: str) -> str:
    """Identify one approval event.

    Keyed on the task version at approval, so approving the same drafted action
    twice collides, while a genuinely new approval after an edit (which bumps
    the version) is allowed to run.
    """
    return f"approve:{from_status}:v{task.version or 0}"


async def _claim_run(
    db: AsyncSession,
    task: Task,
    *,
    action_type: str,
    idempotency_key: str,
    actor_user_id,
) -> TaskAutomationRun | None:
    """Atomically claim the right to execute, or return None.

    One statement, so concurrency is resolved by Postgres rather than by
    read-then-write in application code. ``DO UPDATE ... WHERE status='failed'``
    means a previous failure can be retried while a pending or succeeded run
    blocks: the update matches no row, nothing is returned, and the caller
    no-ops instead of sending a second copy.
    """
    stmt = (
        pg_insert(TaskAutomationRun)
        .values(
            tenant_id=task.tenant_id,
            task_id=task.id,
            action_type=action_type,
            idempotency_key=idempotency_key,
            status="pending",
            triggered_by_user_id=actor_user_id,
        )
        .on_conflict_do_update(
            constraint="uq_task_automation_runs_task_key",
            set_={
                "status": "pending",
                "error_message": None,
                "triggered_by_user_id": actor_user_id,
                "completed_at": None,
            },
            where=TaskAutomationRun.status == "failed",
        )
        .returning(TaskAutomationRun.id)
    )
    run_id = await db.scalar(stmt)
    if run_id is None:
        return None
    await db.commit()
    return await db.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.id == run_id)
    )


# ── Action handlers ─────────────────────────────────────────────────────────


async def _run_email_client(task: Task, payload: dict[str, Any]) -> tuple[bool, str]:
    """Send the approved client email. Returns (succeeded, detail)."""
    try:
        action = EmailClientAction.model_validate(payload)
    except ValidationError:
        # The payload was written by this system, so an invalid one means a bug
        # or tampering. Never improvise a partial send.
        return False, "The stored email draft is not a valid action payload"

    result = await email_service.send_email(
        to=list(action.to),
        subject=action.subject,
        html_body=_body_to_html(action.body),
        text_body=action.body,
    )
    if result:
        return True, result.value
    # DISABLED/UNCONFIGURED must not be recorded as a send: the attorney would
    # believe the client was contacted. Leaving the run failed also keeps it
    # retryable once delivery is configured.
    return False, f"Email delivery did not complete ({result.value})"


def _body_to_html(body: str) -> str:
    from html import escape

    paragraphs = [
        f"<p>{escape(block).replace(chr(10), '<br>')}</p>"
        for block in body.split("\n\n")
        if block.strip()
    ]
    return "".join(paragraphs) or f"<p>{escape(body)}</p>"


ACTION_HANDLERS: dict[str, Callable[[Task, dict], Awaitable[tuple[bool, str]]]] = {
    "email_client": _run_email_client,
}


# ── Entry point ─────────────────────────────────────────────────────────────


async def run_task_automation(
    task_id,
    tenant_id,
    *,
    from_status: str,
    actor_user_id=None,
) -> None:
    """Execute an approved task's pending action, at most once.

    Runs after the transition has already been committed, on its own session,
    because the request session is gone by then. A failure here records evidence
    and never rolls the task back: the attorney's approval is a real decision
    that happened, and hiding it would be worse than a visible failed run.
    """
    if from_status != APPROVAL_FROM_STATUS:
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
                succeeded, detail = await handler(task, dict(task.pending_action))
            except Exception as exc:
                succeeded, detail = False, f"{type(exc).__name__}: {exc}"[:500]
                logger.warning(
                    "task_automation_handler_raised task_id=%s action_type=%s",
                    task_id,
                    action_type,
                    exc_info=True,
                )

            run.status = "succeeded" if succeeded else "failed"
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
