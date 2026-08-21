"""Canonical task workflow transitions and internal audit history.

Every code path that changes a task's lifecycle status should use this module so
board state, closure metadata, concurrency, and the task timeline stay aligned.
The caller owns the database transaction and external notification dispatch.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskEvent
from app.schemas.task import OPEN_TASK_STATUSES, TASK_STATUSES


class TaskWorkflowError(ValueError):
    def __init__(self, detail: str, status_code: int = 422):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class TaskVersionConflict(TaskWorkflowError):
    def __init__(self):
        super().__init__(
            "This task changed after the board was loaded. Review the latest task and try again.",
            status_code=409,
        )


async def _is_attorney_capable(db: AsyncSession, user) -> bool:
    from app.services.rbac_service import get_user_capabilities

    return "approve_legal_work" in await get_user_capabilities(db, user.id)


def staged_review_is_approved(task: Task) -> bool:
    """Return whether a staged task has durable attorney approval evidence."""

    if not (
        task.review_policy == "staff_then_attorney"
        and task.review_stage == "approved"
        and task.staff_reviewer_user_id is not None
        and task.attorney_reviewer_user_id is not None
        and task.staff_reviewer_user_id != task.attorney_reviewer_user_id
        and task.attorney_approved_at is not None
        and task.attorney_approved_by_user_id is not None
    ):
        return False
    if (
        task.attorney_override
        and task.attorney_approved_by_user_id == task.attorney_reviewer_user_id
    ):
        return True
    return (
        task.staff_reviewed_at is not None
        and task.staff_reviewed_by_user_id == task.staff_reviewer_user_id
        and task.attorney_approved_by_user_id == task.attorney_reviewer_user_id
    )


async def require_review_actor(
    db: AsyncSession,
    task: Task,
    actor,
    *,
    stage: str,
    override: bool = False,
) -> None:
    """Authorize one exact staged-review decision and fail closed."""

    if task.review_policy != "staff_then_attorney":
        raise TaskWorkflowError("This task does not use staged review", status_code=409)
    if task.status != "review":
        raise TaskWorkflowError("This task is not awaiting review", status_code=409)

    if stage == "staff":
        if task.review_stage != "staff":
            raise TaskWorkflowError(
                "Staff review is not currently required", status_code=409
            )
        if task.staff_reviewer_user_id != actor.id or task.reviewer_user_id != actor.id:
            raise TaskWorkflowError(
                "Only the assigned staff reviewer can review this task",
                status_code=403,
            )
        return

    if stage == "attorney":
        if not await _is_attorney_capable(db, actor):
            raise TaskWorkflowError(
                "Attorney approval capability is required for this review",
                status_code=403,
            )
        if task.attorney_reviewer_user_id != actor.id:
            raise TaskWorkflowError(
                "Only the assigned attorney reviewer can approve this task",
                status_code=403,
            )
        if override:
            if task.review_stage not in {"staff", "attorney_pending"}:
                raise TaskWorkflowError(
                    "This review stage cannot be overridden", status_code=409
                )
            return
        if task.review_stage != "attorney_pending":
            raise TaskWorkflowError(
                "Attorney review is not currently required", status_code=409
            )
        if (
            task.attorney_reviewer_user_id != actor.id
            or task.reviewer_user_id != actor.id
        ):
            raise TaskWorkflowError(
                "Only the assigned attorney reviewer can approve this task",
                status_code=403,
            )
        return

    raise TaskWorkflowError("Unsupported review stage", status_code=422)


async def record_review_decision(
    db: AsyncSession,
    task: Task,
    *,
    actor,
    stage: str,
    decision: str,
    reason: str | None = None,
    override: bool = False,
) -> str:
    """Record a staged decision without executing its pending action."""

    await require_review_actor(db, task, actor, stage=stage, override=override)
    if decision not in {"approve", "request_changes"}:
        raise TaskWorkflowError("Unsupported review decision")
    now = datetime.now(timezone.utc)
    clean_reason = (reason or "").strip() or None
    prior_stage = task.review_stage

    if stage == "staff":
        if decision == "approve":
            task.staff_reviewed_at = now
            task.staff_reviewed_by_user_id = actor.id
            task.review_stage = "attorney_pending"
            task.reviewer_user_id = task.attorney_reviewer_user_id
            event_type = "staff_review_approved"
        else:
            task.review_stage = "staff"
            event_type = "staff_changes_requested"
    else:
        if decision == "approve":
            task.attorney_approved_at = now
            task.attorney_approved_by_user_id = actor.id
            task.attorney_override = bool(override)
            task.review_stage = "approved"
            task.reviewer_user_id = actor.id
            event_type = (
                "attorney_override_approved" if override else "attorney_approved"
            )
        else:
            task.review_stage = "staff"
            task.reviewer_user_id = task.staff_reviewer_user_id
            task.staff_reviewed_at = None
            task.staff_reviewed_by_user_id = None
            task.attorney_approved_at = None
            task.attorney_approved_by_user_id = None
            task.attorney_override = False
            event_type = "attorney_changes_requested"

    increment_task_version(task)
    append_task_event(
        db,
        task,
        event_type=event_type,
        actor_user_id=actor.id,
        from_status=task.status,
        to_status=task.status,
        note=clean_reason,
        metadata={
            "decision_stage": stage,
            "from_review_stage": prior_stage,
            "to_review_stage": task.review_stage,
            "override": bool(override),
            "skipped_staff_reviewer_user_id": (
                str(task.staff_reviewer_user_id) if override else None
            ),
        },
    )
    return event_type


def reset_staged_review_after_edit(task: Task) -> bool:
    """Invalidate every staged-review decision after artifact content changes."""

    if task.review_policy != "staff_then_attorney":
        return False
    task.review_stage = "staff"
    task.reviewer_user_id = task.staff_reviewer_user_id
    task.staff_reviewed_at = None
    task.staff_reviewed_by_user_id = None
    task.attorney_approved_at = None
    task.attorney_approved_by_user_id = None
    task.attorney_override = False
    return True


def increment_task_version(task: Task) -> None:
    task.version = (task.version or 0) + 1


async def require_task_references_for_tenant(
    db: AsyncSession,
    tenant_id,
    values: dict,
) -> None:
    """Reject foreign or missing objects before a Task can reference them.

    The referenced tables use ordinary foreign keys, which prove that an ID
    exists but do not prove that it belongs to the task's tenant. Keep the
    checks explicit even when PostgreSQL RLS is enabled so this invariant also
    holds in maintenance/test contexts and cannot depend on connection state.
    An assignee must also be active; inactive and foreign users receive the
    same response as a missing user.

    Lives here rather than in the router because the chat assistant can also
    propose tasks, and a model-authored id must clear exactly the same gate as
    one typed into the UI.
    """
    # Imported lazily: these models pull in wide relationship graphs, and this
    # module is imported by the request path on every board render.
    from app.models.contact import Contact
    from app.models.plugin import Matter
    from app.models.user import User
    from sqlalchemy import select

    checks = (
        ("matter_id", Matter, "Matter not found", False),
        ("contact_id", Contact, "Contact not found", False),
        ("assigned_to_user_id", User, "Assigned user not found", True),
        ("reviewer_user_id", User, "Reviewer not found", True),
    )
    for field, model, detail, require_active in checks:
        reference_id = values.get(field)
        if reference_id is None:
            continue
        filters = [
            model.id == reference_id,
            model.tenant_id == tenant_id,
        ]
        if require_active:
            filters.append(User.is_active.is_(True))
        found_id = await db.scalar(select(model.id).where(*filters))
        if found_id is None:
            # A single 404 covers both missing and foreign records so this never
            # becomes a cross-tenant ID oracle.
            raise TaskWorkflowError(detail, status_code=404)


def append_task_event(
    db: AsyncSession,
    task: Task,
    *,
    event_type: str,
    actor_user_id=None,
    from_status: str | None = None,
    to_status: str | None = None,
    note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> TaskEvent:
    event = TaskEvent(
        tenant_id=task.tenant_id,
        task_id=task.id,
        event_type=event_type,
        actor_user_id=actor_user_id,
        from_status=from_status,
        to_status=to_status,
        note=(note or "").strip() or None,
        metadata_json=metadata or {},
    )
    db.add(event)
    return event


def transition_task(
    db: AsyncSession,
    task: Task,
    *,
    to_status: str,
    actor_user_id,
    expected_version: int | None = None,
    reason: str | None = None,
    waiting_follow_up_date: date | None = None,
    reviewer_user_id=None,
) -> bool:
    """Apply one validated workflow transition without committing.

    Returns ``True`` when lifecycle or transition metadata changed. A same-state
    request is permitted when it updates Waiting/Review metadata.
    """
    if to_status not in TASK_STATUSES:
        raise TaskWorkflowError(f"Unsupported task status: {to_status}")
    if expected_version is not None and expected_version != task.version:
        raise TaskVersionConflict()

    previous_status = task.status
    clean_reason = (reason or "").strip() or None

    if to_status == "waiting":
        effective_reason = clean_reason or (
            task.waiting_reason if previous_status == "waiting" else None
        )
        if not effective_reason:
            raise TaskWorkflowError("A waiting reason is required")
    if to_status == "cancelled" and previous_status != "cancelled" and not clean_reason:
        raise TaskWorkflowError("A closed_reason is required when cancelling a task")

    now = datetime.now(timezone.utc)
    metadata_changed = False

    if to_status == "waiting":
        effective_reason = clean_reason or task.waiting_reason
        metadata_changed = (
            task.waiting_reason != effective_reason
            or task.waiting_follow_up_date != waiting_follow_up_date
        )
        task.waiting_reason = effective_reason
        task.waiting_follow_up_date = waiting_follow_up_date
    else:
        metadata_changed = metadata_changed or bool(
            task.waiting_reason or task.waiting_follow_up_date
        )
        task.waiting_reason = None
        task.waiting_follow_up_date = None

    if to_status == "review":
        # A missing reviewer argument means "leave the assignment alone" for an
        # outbound action. Otherwise a same-state refresh silently unassigns the
        # only person authorized to approve it.
        effective_reviewer = (
            task.reviewer_user_id
            if task.pending_action and reviewer_user_id is None
            else reviewer_user_id
        )
    else:
        # Keep ownership attached while an unsent/failed action still exists.
        # Clearing it here would let any tenant user reopen Review, assign
        # themselves, and send correspondence they were never asked to review.
        effective_reviewer = task.reviewer_user_id if task.pending_action else None
    metadata_changed = metadata_changed or task.reviewer_user_id != effective_reviewer
    task.reviewer_user_id = effective_reviewer

    status_changed = previous_status != to_status
    if not status_changed and not metadata_changed:
        return False

    if to_status == "completed":
        task.completed_at = task.completed_at or now
        task.closed_by_user_id = actor_user_id
        task.closed_reason = clean_reason
    elif to_status == "cancelled":
        task.completed_at = None
        task.closed_by_user_id = actor_user_id
        task.closed_reason = clean_reason or task.closed_reason
    elif to_status in OPEN_TASK_STATUSES:
        task.completed_at = None
        task.closed_by_user_id = None
        task.closed_reason = None

    task.status = to_status
    task.status_changed_at = now
    increment_task_version(task)

    if (
        previous_status in {"completed", "cancelled"}
        and to_status in OPEN_TASK_STATUSES
    ):
        event_type = "reopened"
    elif to_status in {"completed", "cancelled"}:
        event_type = to_status
    elif status_changed:
        event_type = "status_changed"
    else:
        event_type = "status_updated"

    append_task_event(
        db,
        task,
        event_type=event_type,
        actor_user_id=actor_user_id,
        from_status=previous_status,
        to_status=to_status,
        note=clean_reason if to_status != "waiting" else task.waiting_reason,
        metadata={
            "waiting_follow_up_date": (
                task.waiting_follow_up_date.isoformat()
                if task.waiting_follow_up_date
                else None
            ),
            "reviewer_user_id": (
                str(task.reviewer_user_id) if task.reviewer_user_id else None
            ),
        },
    )
    return True
