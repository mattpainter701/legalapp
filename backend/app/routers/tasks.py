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

import hashlib
import logging
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.contact import Contact, Lead
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.models.task import Task, TaskAutomationRun, TaskEvent
from app.models.tenant import TenantSettings
from app.models.user import User
from app.schemas.chat_action import MatterDocumentDraftAction
from app.services.cloud_artifact_materialization import (
    CloudArtifactMaterializationError,
    cloud_artifact_materializer,
)
from app.services.cloud_docx_snapshot import (
    CloudDocxSnapshotError,
    inspect_cloud_docx_snapshot,
)
from app.services.document_accountability import append_document_integrity_event
from app.services.email import email_delivery_http_error, email_service
from app.services.generated_artifacts import (
    GeneratedArtifactError,
    create_generated_artifact_revision,
)
from app.services.matter_file_store import MatterFileReadError, MatterFileTooLarge
from app.services.provider_http import ProviderError
from app.services.task_history import record_customer_contact, record_task_event
from app.services.task_notifications import (
    notify_task_created,
    notify_task_updated,
    remove_task_from_calendars,
    task_calendar_user_id,
)
from app.services.task_automation import (
    ActionApprovalConflict,
    enqueue_durable_automation,
)
from app.services.task_workflow import (
    TaskVersionConflict,
    TaskWorkflowError,
    append_task_event,
    increment_task_version,
    require_task_references_for_tenant,
    record_review_decision,
    reset_staged_review_after_edit,
    staged_review_is_approved,
    transition_task,
)
from app.schemas.task import (
    BOARD_STATUS_LABELS,
    BOARD_TASK_STATUSES,
    OPEN_TASK_STATUSES,
    IntakeTaskQualifyRequest,
    IntakeTaskQualifyResponse,
    TaskBoardCard,
    TaskBoardColumn,
    TaskBoardConfig,
    TaskBoardResponse,
    TaskBoardRiskCounts,
    TaskBoardTelemetryRequest,
    TaskCardMatter,
    TaskCardPerson,
    TaskCloudSyncResponse,
    PendingActionCloudSync,
    TaskContactedRequest,
    TaskCreate,
    TaskDeliveryState,
    TaskEventListResponse,
    TaskEventResponse,
    TaskListResponse,
    TaskResponse,
    TaskTransitionRequest,
    TaskReviewDecisionRequest,
    AttorneyOverrideRequest,
    PendingActionEdit,
    TaskUpdate,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
logger = logging.getLogger(__name__)

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


async def _task_board_enabled(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """Default on for existing tenants; an administrator can disable rollout."""
    enabled = await db.scalar(
        select(TenantSettings.enable_task_board).where(
            TenantSettings.tenant_id == tenant_id
        )
    )
    return enabled is not False


async def _require_task_references_for_tenant(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    values: dict,
) -> None:
    """HTTP adaptor over the shared tenant-reference gate.

    The rule itself lives in ``task_workflow`` so the chat assistant's proposed
    tasks clear exactly the same gate as ones typed into the UI. This wrapper
    only translates the service error into the 404 that callers already expect.
    """
    try:
        await require_task_references_for_tenant(db, tenant_id, values)
    except TaskWorkflowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def _require_action_reviewer_or_approver(
    db: AsyncSession, task: Task, user
) -> None:
    """Restrict an outbound draft to its reviewer or a legal approver.

    Role labels are presentation data, not an authorization boundary. A user
    outside the assignment may intervene only when the tenant's live RBAC
    grants the explicit legal-approval capability.
    """
    if not task.pending_action:
        return
    if task.reviewer_user_id == user.id:
        return
    from app.services.rbac_service import get_user_capabilities

    if "approve_legal_work" in await get_user_capabilities(db, user.id):
        return
    raise HTTPException(
        status_code=403,
        detail=(
            "Only the assigned reviewer or a user with legal approval "
            "authority can change this outbound action"
        ),
    )


async def _delivery_history(db: AsyncSession, task: Task) -> list[TaskDeliveryState]:
    """Immutable automation attempts, newest first.

    Read separately rather than joined onto the list query: only a handful of
    tasks ever carry an action. Bound the response while retaining far more
    history than a normal task should ever accumulate.
    """
    runs = (
        (
            await db.execute(
                select(TaskAutomationRun)
                .where(
                    TaskAutomationRun.task_id == task.id,
                    TaskAutomationRun.tenant_id == task.tenant_id,
                )
                .order_by(
                    TaskAutomationRun.created_at.desc(),
                    TaskAutomationRun.id.desc(),
                )
                .limit(25)
            )
        )
        .scalars()
        .all()
    )
    return [TaskDeliveryState.model_validate(run) for run in runs]


async def _task_response_with_delivery(db: AsyncSession, task: Task) -> TaskResponse:
    response = TaskResponse.model_validate(task)
    # Only assistant-drafted work can carry an action, so skip the extra read
    # for the overwhelming majority of tasks.
    if task.source == "assistant":
        response.delivery_history = await _delivery_history(db, task)
        response.delivery = (
            response.delivery_history[0] if response.delivery_history else None
        )
    return response


def _task_card_from_row(row) -> TaskBoardCard:
    (
        task,
        matter_name,
        case_number,
        assignee_name,
        assignee_email,
        reviewer_name,
        reviewer_email,
        delivery_run,
    ) = row
    return TaskBoardCard(
        id=task.id,
        title=task.title,
        task_type=task.task_type,
        status=task.status,
        priority=task.priority,
        due_date=task.due_date,
        due_time=task.due_time,
        matter_id=task.matter_id,
        contact_id=task.contact_id,
        assigned_to_user_id=task.assigned_to_user_id,
        created_by_user_id=task.created_by_user_id,
        reviewer_user_id=task.reviewer_user_id,
        matter=(
            TaskCardMatter(
                id=task.matter_id, label=matter_name, case_number=case_number
            )
            if task.matter_id and matter_name
            else None
        ),
        assignee=(
            TaskCardPerson(
                id=task.assigned_to_user_id,
                label=assignee_name or assignee_email or "Assigned user",
            )
            if task.assigned_to_user_id
            else None
        ),
        reviewer=(
            TaskCardPerson(
                id=task.reviewer_user_id,
                label=reviewer_name or reviewer_email or "Reviewer",
            )
            if task.reviewer_user_id
            else None
        ),
        viewed_at=task.viewed_at,
        customer_contacted_at=task.customer_contacted_at,
        customer_contact_method=task.customer_contact_method,
        waiting_reason=task.waiting_reason,
        waiting_follow_up_date=task.waiting_follow_up_date,
        completed_at=task.completed_at,
        closed_reason=task.closed_reason,
        source=task.source,
        external_ref=task.external_ref,
        version=task.version,
        pending_action=task.pending_action,
        delivery=(
            TaskDeliveryState.model_validate(delivery_run) if delivery_run else None
        ),
        status_changed_at=task.status_changed_at,
        updated_at=task.updated_at,
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


@router.get("/board/config", response_model=TaskBoardConfig)
async def get_task_board_config(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_uuid = uuid.UUID(str(current_user.tenant_id))
    await set_tenant_context(db, str(tenant_uuid))
    return TaskBoardConfig(
        enabled=await _task_board_enabled(db, tenant_uuid),
        statuses=BOARD_STATUS_LABELS,
    )


@router.post("/board/telemetry", status_code=202)
async def record_task_board_telemetry(
    payload: TaskBoardTelemetryRequest,
    current_user=Depends(get_current_user),
):
    """Record allow-listed, content-free view adoption signals."""
    logger.info(
        "task_board_view event=%s tenant_id=%s user_id=%s scope=%s",
        payload.event,
        current_user.tenant_id,
        current_user.id,
        payload.scope or "none",
    )
    return {"accepted": True}


@router.get("/board", response_model=TaskBoardResponse)
async def get_task_board(
    scope: str = "mine",
    assigned_to_user_id: Optional[uuid.UUID] = None,
    matter_id: Optional[uuid.UUID] = None,
    priority: Optional[str] = None,
    task_type: Optional[str] = None,
    due_window: Optional[str] = None,
    include_completed_days: int = Query(14, ge=1, le=365),
    per_column_limit: int = Query(50, ge=1, le=100),
    column_status: Optional[str] = None,
    cursor: int = Query(0, ge=0),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a risk-ordered, label-enriched task board.

    Descriptions and customer details intentionally stay out of this read model;
    the detail endpoint remains the authoritative expanded task view.
    """
    started_at = time.perf_counter()
    if scope not in {"mine", "firm"}:
        raise HTTPException(status_code=422, detail="scope must be mine or firm")
    if column_status and column_status not in BOARD_TASK_STATUSES:
        raise HTTPException(status_code=422, detail="Unknown board column")
    if due_window not in {None, "overdue", "today", "7_days", "30_days", "none"}:
        raise HTTPException(status_code=422, detail="Unknown due window")

    tenant_uuid = uuid.UUID(str(current_user.tenant_id))
    await set_tenant_context(db, str(tenant_uuid))
    if not await _task_board_enabled(db, tenant_uuid):
        raise HTTPException(status_code=404, detail="The Work Board is disabled")
    today = date.today()
    base_conditions = [Task.tenant_id == tenant_uuid]
    if scope == "mine":
        base_conditions.append(
            or_(
                Task.assigned_to_user_id == current_user.id,
                Task.reviewer_user_id == current_user.id,
            )
        )
    elif assigned_to_user_id:
        await _require_task_references_for_tenant(
            db, tenant_uuid, {"assigned_to_user_id": assigned_to_user_id}
        )
        base_conditions.append(Task.assigned_to_user_id == assigned_to_user_id)
    if matter_id:
        await _require_task_references_for_tenant(
            db, tenant_uuid, {"matter_id": matter_id}
        )
        base_conditions.append(Task.matter_id == matter_id)
    if priority:
        base_conditions.append(Task.priority == priority)
    if task_type:
        base_conditions.append(Task.task_type == task_type)
    if due_window == "overdue":
        base_conditions.append(Task.due_date < today)
    elif due_window == "today":
        base_conditions.append(Task.due_date == today)
    elif due_window == "7_days":
        base_conditions.extend(
            [Task.due_date >= today, Task.due_date <= today + timedelta(days=7)]
        )
    elif due_window == "30_days":
        base_conditions.extend(
            [Task.due_date >= today, Task.due_date <= today + timedelta(days=30)]
        )
    elif due_window == "none":
        base_conditions.append(Task.due_date.is_(None))

    open_conditions = [Task.status.in_(OPEN_TASK_STATUSES)]
    risk_counts = TaskBoardRiskCounts(
        overdue=(
            await db.scalar(
                select(func.count())
                .select_from(Task)
                .where(*base_conditions, *open_conditions, Task.due_date < today)
            )
            or 0
        ),
        due_today=(
            await db.scalar(
                select(func.count())
                .select_from(Task)
                .where(*base_conditions, *open_conditions, Task.due_date == today)
            )
            or 0
        ),
        unassigned=(
            await db.scalar(
                select(func.count())
                .select_from(Task)
                .where(
                    *base_conditions,
                    *open_conditions,
                    Task.assigned_to_user_id.is_(None),
                )
            )
            or 0
        ),
        waiting_follow_up_due=(
            await db.scalar(
                select(func.count())
                .select_from(Task)
                .where(
                    *base_conditions,
                    Task.status == "waiting",
                    Task.waiting_follow_up_date.is_not(None),
                    Task.waiting_follow_up_date <= today,
                )
            )
            or 0
        ),
    )

    assignee = aliased(User)
    reviewer = aliased(User)
    delivery_run = aliased(TaskAutomationRun)
    latest_delivery_id = (
        select(TaskAutomationRun.id)
        .where(
            TaskAutomationRun.task_id == Task.id,
            TaskAutomationRun.tenant_id == tenant_uuid,
        )
        .order_by(
            TaskAutomationRun.created_at.desc(),
            TaskAutomationRun.id.desc(),
        )
        .limit(1)
        .correlate(Task)
        .scalar_subquery()
    )
    risk_bucket = case(
        (Task.due_date < today, 0),
        (Task.due_date == today, 1),
        else_=2,
    )
    priority_bucket = case(
        (Task.priority == "urgent", 0),
        (Task.priority == "high", 1),
        (Task.priority == "medium", 2),
        else_=3,
    )
    columns = []
    completed_cutoff = datetime.now(timezone.utc) - timedelta(
        days=include_completed_days
    )
    for status_value in BOARD_TASK_STATUSES:
        status_conditions = [Task.status == status_value]
        if status_value == "completed":
            status_conditions.append(Task.completed_at >= completed_cutoff)
        total = (
            await db.scalar(
                select(func.count())
                .select_from(Task)
                .where(*base_conditions, *status_conditions)
            )
            or 0
        )
        offset = cursor if column_status == status_value else 0
        stmt = (
            select(
                Task,
                Matter.matter_name,
                Matter.case_number,
                assignee.full_name,
                assignee.email,
                reviewer.full_name,
                reviewer.email,
                delivery_run,
            )
            .outerjoin(
                Matter,
                and_(Matter.id == Task.matter_id, Matter.tenant_id == tenant_uuid),
            )
            .outerjoin(
                assignee,
                and_(
                    assignee.id == Task.assigned_to_user_id,
                    assignee.tenant_id == tenant_uuid,
                ),
            )
            .outerjoin(
                reviewer,
                and_(
                    reviewer.id == Task.reviewer_user_id,
                    reviewer.tenant_id == tenant_uuid,
                ),
            )
            .outerjoin(delivery_run, delivery_run.id == latest_delivery_id)
            .where(*base_conditions, *status_conditions)
            .order_by(
                risk_bucket,
                priority_bucket,
                Task.due_date.asc().nulls_last(),
                Task.due_time.asc().nulls_last(),
                Task.status_changed_at.desc(),
                Task.id,
            )
            .offset(offset)
            .limit(per_column_limit)
        )
        rows = (await db.execute(stmt)).all()
        next_offset = offset + len(rows)
        columns.append(
            TaskBoardColumn(
                status=status_value,
                label=BOARD_STATUS_LABELS[status_value],
                total=total,
                items=[_task_card_from_row(row) for row in rows],
                next_cursor=str(next_offset) if next_offset < total else None,
            )
        )

    oldest_waiting_at, oldest_review_at = (
        await db.execute(
            select(
                func.min(
                    case((Task.status == "waiting", Task.status_changed_at), else_=None)
                ),
                func.min(
                    case((Task.status == "review", Task.status_changed_at), else_=None)
                ),
            ).where(*base_conditions)
        )
    ).one()
    now = datetime.now(timezone.utc)

    def age_hours(value):
        if value is None:
            return 0
        return max(0, int((now - value).total_seconds() / 3600))

    result = TaskBoardResponse(
        columns=columns,
        risk_counts=risk_counts,
        scope=scope,
        generated_at=now,
    )
    logger.info(
        "task_board_load tenant_id=%s user_id=%s scope=%s cards=%d "
        "waiting_oldest_hours=%d review_oldest_hours=%d duration_ms=%d",
        tenant_uuid,
        current_user.id,
        scope,
        sum(len(column.items) for column in columns),
        age_hours(oldest_waiting_at),
        age_hours(oldest_review_at),
        int((time.perf_counter() - started_at) * 1000),
    )
    return result


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
    tenant_uuid = uuid.UUID(tenant_id)
    await _require_task_references_for_tenant(db, tenant_uuid, data)

    task = Task(
        tenant_id=tenant_uuid,
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
    append_task_event(
        db,
        task,
        event_type="created",
        actor_user_id=current_user.id,
        to_status=task.status,
    )
    if task.assigned_to_user_id:
        append_task_event(
            db,
            task,
            event_type="assigned",
            actor_user_id=current_user.id,
            note=assignment_note,
            metadata={"assigned_to_user_id": str(task.assigned_to_user_id)},
        )
        await record_task_event(
            db, task, event="assigned", actor=current_user, note=assignment_note
        )
    await db.commit()
    await set_tenant_context(db, tenant_id)
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
        append_task_event(
            db,
            attorney_task,
            event_type="created",
            actor_user_id=current_user.id,
            to_status="pending",
        )
        append_task_event(
            db,
            attorney_task,
            event_type="assigned",
            actor_user_id=current_user.id,
            note=payload.partner_notes,
            metadata={"assigned_to_user_id": str(attorney.id)},
        )
    else:
        previous_calendar_user_id = task_calendar_user_id(attorney_task)
        assignment_changed = attorney_task.assigned_to_user_id != attorney.id
        attorney_task.title = f"Qualified intake: {caller}"
        attorney_task.description = attorney_description
        attorney_task.task_type = "intake"
        reopened_attorney_task = False
        if attorney_task.status == "cancelled":
            reopened_attorney_task = transition_task(
                db,
                attorney_task,
                to_status="pending",
                actor_user_id=current_user.id,
                reason="Intake re-qualified",
            )
        attorney_task.priority = "urgent"
        attorney_task.due_date = attorney_task.due_date or date.today()
        attorney_task.contact_id = lead.contact_id
        attorney_task.assigned_to_user_id = attorney.id
        if not reopened_attorney_task:
            increment_task_version(attorney_task)
        if assignment_changed:
            append_task_event(
                db,
                attorney_task,
                event_type="reassigned",
                actor_user_id=current_user.id,
                note=payload.partner_notes,
                metadata={"assigned_to_user_id": str(attorney.id)},
            )

    if partner_task.status != "completed":
        transition_task(
            db,
            partner_task,
            to_status="completed",
            actor_user_id=current_user.id,
            reason="Lead qualified and handed to attorney intake",
        )
        await record_task_event(
            db,
            partner_task,
            event="completed",
            actor=current_user,
            note=partner_task.closed_reason,
        )

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


@router.get("/{task_id}/events", response_model=TaskEventListResponse)
async def get_task_events(
    task_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_uuid = uuid.UUID(str(current_user.tenant_id))
    await set_tenant_context(db, str(tenant_uuid))
    await _load_task_or_404(db, task_id, tenant_uuid)
    total = (
        await db.scalar(
            select(func.count())
            .select_from(TaskEvent)
            .where(
                TaskEvent.tenant_id == tenant_uuid,
                TaskEvent.task_id == task_id,
            )
        )
        or 0
    )
    rows = (
        await db.execute(
            select(TaskEvent, User.full_name, User.email)
            .outerjoin(
                User,
                and_(
                    User.id == TaskEvent.actor_user_id,
                    User.tenant_id == tenant_uuid,
                ),
            )
            .where(
                TaskEvent.tenant_id == tenant_uuid,
                TaskEvent.task_id == task_id,
            )
            .order_by(TaskEvent.created_at.desc(), TaskEvent.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return TaskEventListResponse(
        items=[
            TaskEventResponse(
                id=event.id,
                task_id=event.task_id,
                event_type=event.event_type,
                actor_user_id=event.actor_user_id,
                actor_label=full_name or email,
                from_status=event.from_status,
                to_status=event.to_status,
                note=event.note,
                metadata_json=event.metadata_json or {},
                created_at=event.created_at,
            )
            for event, full_name, email in rows
        ],
        total=total,
    )


@router.post("/{task_id}/review/staff", response_model=TaskResponse)
async def review_task_as_staff(
    task_id: uuid.UUID,
    payload: TaskReviewDecisionRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_uuid = uuid.UUID(str(current_user.tenant_id))
    await set_tenant_context(db, str(tenant_uuid))
    task = (
        await db.execute(
            select(Task)
            .where(Task.id == task_id, Task.tenant_id == tenant_uuid)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    try:
        if payload.expected_version != task.version:
            raise TaskVersionConflict()
        await record_review_decision(
            db,
            task,
            actor=current_user,
            stage="staff",
            decision=payload.decision,
            reason=payload.reason,
        )
    except TaskWorkflowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await db.commit()
    await set_tenant_context(db, str(tenant_uuid))

    await db.refresh(task)
    return await _task_response_with_delivery(db, task)


async def _approve_staged_task(
    task_id: uuid.UUID,
    payload: TaskReviewDecisionRequest | AttorneyOverrideRequest,
    current_user,
    db: AsyncSession,
    *,
    override: bool = False,
):
    tenant_uuid = uuid.UUID(str(current_user.tenant_id))
    tenant_id = str(tenant_uuid)
    await set_tenant_context(db, tenant_id)
    task = (
        await db.execute(
            select(Task)
            .where(Task.id == task_id, Task.tenant_id == tenant_uuid)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if payload.expected_version != task.version:
        raise HTTPException(
            status_code=409, detail="This task changed after it was loaded"
        )
    decision = "approve" if override else payload.decision
    try:
        await record_review_decision(
            db,
            task,
            actor=current_user,
            stage="attorney",
            decision=decision,
            reason=payload.reason,
            override=override,
        )
        previous_status = task.status
        transition_task(
            db,
            task,
            to_status="in_progress",
            actor_user_id=current_user.id,
            expected_version=task.version,
        )
        await enqueue_durable_automation(
            db,
            task,
            from_status=previous_status,
            to_status=task.status,
            actor_user_id=current_user.id,
        )
    except (TaskWorkflowError, ActionApprovalConflict) as exc:
        await db.rollback()
        status_code = getattr(exc, "status_code", 409)
        raise HTTPException(
            status_code=status_code, detail=getattr(exc, "detail", str(exc))
        ) from exc
    await db.commit()
    await set_tenant_context(db, tenant_id)
    await db.refresh(task)
    return await _task_response_with_delivery(db, task)


@router.post("/{task_id}/review/attorney", response_model=TaskResponse)
async def review_task_as_attorney(
    task_id: uuid.UUID,
    payload: TaskReviewDecisionRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if payload.decision == "request_changes":
        tenant_uuid = uuid.UUID(str(current_user.tenant_id))
        await set_tenant_context(db, str(tenant_uuid))
        task = (
            await db.execute(
                select(Task)
                .where(Task.id == task_id, Task.tenant_id == tenant_uuid)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if payload.expected_version != task.version:
            raise HTTPException(
                status_code=409, detail="This task changed after it was loaded"
            )
        try:
            await record_review_decision(
                db,
                task,
                actor=current_user,
                stage="attorney",
                decision="request_changes",
                reason=payload.reason,
            )
        except TaskWorkflowError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        await db.commit()
        await set_tenant_context(db, str(tenant_uuid))
        await db.refresh(task)
        return await _task_response_with_delivery(db, task)
    return await _approve_staged_task(task_id, payload, current_user, db)


@router.post("/{task_id}/review/attorney-override", response_model=TaskResponse)
async def override_staff_review(
    task_id: uuid.UUID,
    payload: AttorneyOverrideRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _approve_staged_task(task_id, payload, current_user, db, override=True)


@router.post("/{task_id}/transition", response_model=TaskResponse)
async def transition_task_status(
    task_id: uuid.UUID,
    payload: TaskTransitionRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_uuid = uuid.UUID(str(current_user.tenant_id))
    tenant_id = str(tenant_uuid)
    await set_tenant_context(db, tenant_id)
    task = (
        await db.execute(
            select(Task)
            .where(Task.id == task_id, Task.tenant_id == tenant_uuid)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if (
        task.review_policy == "staff_then_attorney"
        and payload.to_status == "in_progress"
        and not staged_review_is_approved(task)
    ):
        raise HTTPException(
            status_code=409,
            detail="Attorney approval is required before this staged task can proceed",
        )

    if (
        task.review_policy == "staff_then_attorney"
        and payload.reviewer_user_id is not None
        and payload.reviewer_user_id != task.reviewer_user_id
    ):
        raise HTTPException(
            status_code=409,
            detail="Staged reviewers must be reassigned through the review workflow",
        )

    if task.pending_action:
        reviewer_change = (
            payload.reviewer_user_id is not None
            and payload.reviewer_user_id != task.reviewer_user_id
        )
        if payload.to_status != task.status or reviewer_change:
            await _require_action_reviewer_or_approver(db, task, current_user)

    if payload.reviewer_user_id:
        await _require_task_references_for_tenant(
            db, tenant_uuid, {"reviewer_user_id": payload.reviewer_user_id}
        )
    previous_calendar_user_id = task_calendar_user_id(task)
    previous_status = task.status
    try:
        changed = transition_task(
            db,
            task,
            to_status=payload.to_status,
            actor_user_id=current_user.id,
            expected_version=payload.expected_version,
            reason=payload.reason,
            waiting_follow_up_date=payload.waiting_follow_up_date,
            reviewer_user_id=payload.reviewer_user_id,
        )
    except TaskVersionConflict as exc:
        logger.warning(
            "task_board_transition_conflict tenant_id=%s user_id=%s task_id=%s "
            "from_status=%s to_status=%s expected_version=%s current_version=%s",
            tenant_uuid,
            current_user.id,
            task.id,
            previous_status,
            payload.to_status,
            payload.expected_version,
            task.version,
        )
        current_response = await _task_response_with_delivery(db, task)
        raise HTTPException(
            status_code=409,
            detail={
                "message": exc.detail,
                "current_task": current_response.model_dump(mode="json"),
            },
        ) from exc
    except TaskWorkflowError as exc:
        logger.info(
            "task_board_transition_rejected tenant_id=%s user_id=%s task_id=%s "
            "from_status=%s to_status=%s reason_code=workflow_validation",
            tenant_uuid,
            current_user.id,
            task.id,
            previous_status,
            payload.to_status,
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    if (
        changed
        and task.status in {"completed", "cancelled"}
        and task.status != previous_status
    ):
        await record_task_event(
            db,
            task,
            event=task.status,
            actor=current_user,
            note=task.closed_reason,
        )
    if changed:
        # Before the commit on purpose: the queued delivery row and its durable
        # job land in the same transaction as the approval, so there is no window
        # where a task is approved but the send is unrecorded.
        try:
            await enqueue_durable_automation(
                db,
                task,
                from_status=previous_status,
                to_status=task.status,
                actor_user_id=current_user.id,
                acknowledge_prior_delivery_risk=(
                    payload.acknowledge_prior_delivery_risk
                ),
            )
        except ActionApprovalConflict as exc:
            await db.rollback()
            await set_tenant_context(db, tenant_id)
            current_task = await _load_task_or_404(db, task_id, tenant_uuid)
            current_response = await _task_response_with_delivery(db, current_task)
            raise HTTPException(
                status_code=409,
                detail={
                    "message": exc.detail,
                    "current_task": current_response.model_dump(mode="json"),
                },
            ) from exc
    await db.commit()
    await set_tenant_context(db, tenant_id)
    await db.refresh(task)
    if changed:
        await notify_task_updated(
            db,
            task,
            tenant_id,
            calendar_changed=True,
            assignment_changed=False,
            previous_calendar_user_id=previous_calendar_user_id,
        )
    logger.info(
        "task_board_transition_success tenant_id=%s user_id=%s task_id=%s "
        "from_status=%s to_status=%s changed=%s version=%s",
        tenant_uuid,
        current_user.id,
        task.id,
        previous_status,
        task.status,
        changed,
        task.version,
    )
    return await _task_response_with_delivery(db, task)


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
    return await _task_response_with_delivery(db, task)


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
    transitioned = False
    if task.customer_contacted_at is None:
        task.customer_contacted_at = now
    task.customer_contact_method = payload.method
    if task.assigned_to_user_id == current_user.id and task.viewed_at is None:
        task.viewed_at = now
    if task.status == "pending":
        transitioned = transition_task(
            db,
            task,
            to_status="in_progress",
            actor_user_id=current_user.id,
        )
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
    append_task_event(
        db,
        task,
        event_type="contacted",
        actor_user_id=current_user.id,
        note=payload.note,
        metadata={"method": payload.method},
    )
    if not transitioned:
        increment_task_version(task)
    await db.commit()
    await set_tenant_context(db, tenant_id)
    await db.refresh(task)
    return TaskResponse.model_validate(task)


@router.patch("/{task_id}/pending-action", response_model=TaskResponse)
async def update_pending_action(
    task_id: uuid.UUID,
    payload: PendingActionEdit,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revise the text of a drafted action before approving it.

    Narrow on purpose. Only the subject and body are editable: recipients were
    resolved from the matter's own parties, and accepting them here would undo
    that guarantee. The automation key is derived from the canonical action
    payload, so a meaningful draft edit creates a reviewable new attempt while
    unrelated task/version changes and unchanged re-approvals still collide.
    """
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(Task)
        .where(
            Task.id == task_id,
            Task.tenant_id == uuid.UUID(tenant_id),
        )
        .with_for_update()
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not task.pending_action:
        raise HTTPException(
            status_code=409, detail="This task has no drafted action to edit"
        )
    await _require_action_reviewer_or_approver(db, task, current_user)
    if task.status != "review":
        # Once approved, the draft is history. Editing it would misrepresent
        # what was actually sent.
        raise HTTPException(
            status_code=409,
            detail="Only a task still in Review can have its draft edited",
        )
    if payload.expected_version != task.version:
        conflict = TaskVersionConflict()
        raise HTTPException(
            status_code=conflict.status_code, detail=conflict.detail
        ) from None

    updates = payload.model_dump(exclude_none=True, exclude={"expected_version"})
    if not updates:
        return await _task_response_with_delivery(db, task)

    action_type = str(task.pending_action.get("type") or "")
    allowed_fields = {
        "email_client": {"subject", "body"},
        "matter_document_draft": {"title", "body"},
    }.get(action_type, set())
    if set(updates) - allowed_fields:
        raise HTTPException(
            status_code=422,
            detail="This draft does not support those edits",
        )

    # Replace the whole mapping: SQLAlchemy does not track in-place JSON edits.
    action = dict(task.pending_action)
    action.update(updates)
    artifact_revision_no = None
    if action_type == "matter_document_draft" and action.get("artifact_id"):
        try:
            bound_action = MatterDocumentDraftAction.model_validate(action)
            if bound_action.document_edit_mode == "office_snapshot":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Continue editing this formatted snapshot in the cloud DOCX, "
                        "then refresh it into LawHand"
                    ),
                )
            revision = await create_generated_artifact_revision(
                db,
                tenant_id=uuid.UUID(tenant_id),
                artifact_id=bound_action.artifact_id,
                actor_user_id=current_user.id,
                expected_revision_no=bound_action.artifact_revision_no,
                content_text=bound_action.body,
                title=bound_action.title,
            )
            materialized = await cloud_artifact_materializer.materialize(
                db=db,
                tenant_id=uuid.UUID(tenant_id),
                artifact_id=bound_action.artifact_id,
                revision_id=revision.id,
                task_id=task.id,
                uploaded_by_user_id=current_user.id,
                supersedes_document_id=bound_action.document_id,
            )
        except GeneratedArtifactError as exc:
            status_code = 409 if "conflict" in exc.code else 422
            raise HTTPException(status_code=status_code, detail=exc.message) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409, detail="The artifact revision binding is invalid"
            ) from exc
        except CloudArtifactMaterializationError as exc:
            # The old revision remains the review target unless the replacement
            # was written, read back, and bound successfully.
            await db.rollback()
            await set_tenant_context(db, tenant_id)
            status_code = 503 if getattr(exc, "retryable", False) else 409
            raise HTTPException(
                status_code=status_code,
                detail=(
                    "The revised draft could not be written and verified in "
                    "tenant storage"
                ),
            ) from exc

        document = materialized.document
        action.update(
            {
                "body": revision.content_text,
                "artifact_revision_id": str(revision.id),
                "artifact_revision_no": revision.revision_no,
                "artifact_sha256": revision.content_sha256,
                "document_id": str(document.id),
                "document_sha256": materialized.sha256,
                "document_storage_backend": document.storage_backend,
                "document_provider_etag": document.provider_etag,
                "document_provider_version_id": document.provider_version_id,
                "document_preview_truncated": False,
                "document_edit_mode": "lawhand_text",
            }
        )
        try:
            action = MatterDocumentDraftAction.model_validate(action).model_dump(
                mode="json"
            )
        except ValueError as exc:
            await db.rollback()
            await set_tenant_context(db, tenant_id)
            raise HTTPException(
                status_code=409,
                detail="The revised cloud document binding is invalid",
            ) from exc
        artifact_revision_no = revision.revision_no
    task.pending_action = action
    review_reset = reset_staged_review_after_edit(task)
    increment_task_version(task)
    append_task_event(
        db,
        task,
        event_type="draft_edited",
        actor_user_id=current_user.id,
        metadata={
            "fields": sorted(updates),
            "artifact_revision_no": artifact_revision_no,
            "staged_review_reset": review_reset,
        },
    )
    await db.commit()
    await set_tenant_context(db, tenant_id)
    await db.refresh(task)
    return await _task_response_with_delivery(db, task)


@router.post(
    "/{task_id}/pending-action/sync-cloud",
    response_model=TaskCloudSyncResponse,
)
async def sync_pending_action_from_cloud(
    task_id: uuid.UUID,
    payload: PendingActionCloudSync,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Adopt external Word/LibreOffice edits as a new verified revision.

    The mutable provider object is never silently substituted for reviewed
    evidence.  Changed bytes are safely inspected, copied byte-for-byte to a
    new cloud object, read back, linked to a new immutable artifact revision,
    and routed through review again.
    """
    tenant_uuid = uuid.UUID(str(current_user.tenant_id))
    tenant_id = str(tenant_uuid)
    await set_tenant_context(db, tenant_id)

    task = await db.scalar(
        select(Task)
        .where(Task.id == task_id, Task.tenant_id == tenant_uuid)
        .with_for_update()
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if not task.pending_action:
        raise HTTPException(
            status_code=409, detail="This task has no cloud document to refresh"
        )
    await _require_action_reviewer_or_approver(db, task, current_user)
    if task.status != "review":
        raise HTTPException(
            status_code=409,
            detail="Only a document still in Review can be refreshed from cloud",
        )
    if payload.expected_version != task.version:
        conflict = TaskVersionConflict()
        raise HTTPException(
            status_code=conflict.status_code, detail=conflict.detail
        ) from None

    try:
        action = MatterDocumentDraftAction.model_validate(task.pending_action)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="The review task does not have a complete cloud document binding",
        ) from exc
    if (
        action.artifact_id is None
        or action.artifact_revision_id is None
        or action.artifact_revision_no is None
        or action.document_id is None
        or action.document_sha256 is None
        or task.matter_id != action.matter_id
    ):
        raise HTTPException(
            status_code=409,
            detail="The review task does not have a complete cloud document binding",
        )

    document = await db.scalar(
        select(MatterDocument)
        .where(
            MatterDocument.id == action.document_id,
            MatterDocument.tenant_id == tenant_uuid,
            MatterDocument.matter_id == action.matter_id,
            MatterDocument.task_id == task.id,
            MatterDocument.generated_artifact_id == action.artifact_id,
            MatterDocument.generated_artifact_revision_id
            == action.artifact_revision_id,
        )
        .with_for_update()
    )
    if document is None:
        raise HTTPException(
            status_code=409,
            detail="The bound tenant-cloud document is unavailable",
        )
    if (
        document.storage_backend not in {"onedrive", "sharepoint", "google_drive"}
        or not document.provider_object_id
        or document.document_sha256 != action.document_sha256
        or document.document_role != "working_copy"
        or document.document_status not in {"draft", "in_review"}
        or document.storage_state not in {"verified", "conflict"}
    ):
        raise HTTPException(
            status_code=409,
            detail="The bound document is not a refreshable tenant-cloud working copy",
        )

    try:
        cloud_bytes = await cloud_artifact_materializer.read_current_cloud_bytes(
            tenant_id=tenant_uuid,
            document=document,
        )
    except MatterFileTooLarge as exc:
        raise HTTPException(
            status_code=422,
            detail="The cloud DOCX is too large to adopt as a review revision",
        ) from exc
    except MatterFileReadError as exc:
        raise HTTPException(
            status_code=409,
            detail="The tenant-cloud document could not be read from its durable binding",
        ) from exc
    except ProviderError as exc:
        raise HTTPException(
            status_code=503,
            detail="The cloud provider could not return the current document",
        ) from exc

    try:
        snapshot = inspect_cloud_docx_snapshot(
            cloud_bytes,
            filename=document.filename,
        )
    except CloudDocxSnapshotError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc

    if hashlib.sha256(cloud_bytes).hexdigest() == action.document_sha256:
        document.storage_state = "verified"
        document.storage_verified_at = datetime.now(timezone.utc)
        await append_document_integrity_event(
            db,
            tenant_id=tenant_uuid,
            matter_id=action.matter_id,
            task_id=task.id,
            artifact_id=action.artifact_id,
            artifact_revision_id=action.artifact_revision_id,
            document_id=document.id,
            event_type="cloud_working_copy_reverified",
            actor_type="user",
            actor_user_id=current_user.id,
            content_sha256=action.document_sha256,
            provider_object_id=document.provider_object_id,
            provider_etag=document.provider_etag,
            provider_version_id=document.provider_version_id,
            metadata={"storage_backend": document.storage_backend},
        )
        await db.commit()
        await set_tenant_context(db, tenant_id)
        await db.refresh(task)
        return TaskCloudSyncResponse(
            task=await _task_response_with_delivery(db, task),
            changed=False,
            message="Cloud working copy already matches the review revision.",
        )

    previous_sha256 = action.document_sha256
    try:
        revision = await create_generated_artifact_revision(
            db,
            tenant_id=tenant_uuid,
            artifact_id=action.artifact_id,
            actor_user_id=current_user.id,
            expected_revision_no=action.artifact_revision_no,
            content_text=snapshot.review_text,
            title=action.title,
        )
        materialized = await cloud_artifact_materializer.materialize(
            db=db,
            tenant_id=tenant_uuid,
            artifact_id=action.artifact_id,
            revision_id=revision.id,
            task_id=task.id,
            uploaded_by_user_id=current_user.id,
            supersedes_document_id=document.id,
            source_docx_bytes=cloud_bytes,
        )
    except GeneratedArtifactError as exc:
        status_code = 409 if "conflict" in exc.code else 422
        raise HTTPException(status_code=status_code, detail=exc.message) from exc
    except CloudArtifactMaterializationError as exc:
        await db.rollback()
        await set_tenant_context(db, tenant_id)
        status_code = 503 if getattr(exc, "retryable", False) else 409
        raise HTTPException(
            status_code=status_code,
            detail=(
                "The cloud edits could not be copied and verified as a new "
                "review revision"
            ),
        ) from exc

    new_document = materialized.document
    action_payload = action.model_dump(mode="json")
    action_payload.update(
        {
            "body": revision.content_text,
            "artifact_revision_id": str(revision.id),
            "artifact_revision_no": revision.revision_no,
            "artifact_sha256": revision.content_sha256,
            "document_id": str(new_document.id),
            "document_sha256": materialized.sha256,
            "document_storage_backend": new_document.storage_backend,
            "document_provider_etag": new_document.provider_etag,
            "document_provider_version_id": new_document.provider_version_id,
            "document_preview_truncated": snapshot.preview_truncated,
            "document_edit_mode": "office_snapshot",
        }
    )
    try:
        task.pending_action = MatterDocumentDraftAction.model_validate(
            action_payload
        ).model_dump(mode="json")
    except ValueError as exc:
        await db.rollback()
        await set_tenant_context(db, tenant_id)
        raise HTTPException(
            status_code=409,
            detail="The adopted cloud document binding is invalid",
        ) from exc

    # The provider object the user edited no longer represents its persisted
    # historical hash.  Keep that evidence but mark its pointer as conflicted;
    # the new document is the independently verified review snapshot.
    document.storage_state = "conflict"
    review_reset = reset_staged_review_after_edit(task)
    increment_task_version(task)
    append_task_event(
        db,
        task,
        event_type="cloud_revision_adopted",
        actor_user_id=current_user.id,
        metadata={
            "previous_document_id": str(document.id),
            "document_id": str(new_document.id),
            "previous_sha256": previous_sha256,
            "document_sha256": materialized.sha256,
            "artifact_revision_no": revision.revision_no,
            "preview_truncated": snapshot.preview_truncated,
            "staged_review_reset": review_reset,
        },
    )
    await append_document_integrity_event(
        db,
        tenant_id=tenant_uuid,
        matter_id=action.matter_id,
        task_id=task.id,
        artifact_id=action.artifact_id,
        artifact_revision_id=revision.id,
        document_id=new_document.id,
        operation_id=materialized.operation.id,
        event_type="external_cloud_edit_adopted",
        actor_type="user",
        actor_user_id=current_user.id,
        content_sha256=materialized.sha256,
        provider_object_id=new_document.provider_object_id,
        provider_etag=new_document.provider_etag,
        provider_version_id=new_document.provider_version_id,
        metadata={
            "storage_backend": new_document.storage_backend,
            "previous_document_id": str(document.id),
            "previous_sha256": previous_sha256,
            "byte_count": len(cloud_bytes),
            "preview_truncated": snapshot.preview_truncated,
        },
    )
    await db.commit()
    await set_tenant_context(db, tenant_id)
    await db.refresh(task)
    return TaskCloudSyncResponse(
        task=await _task_response_with_delivery(db, task),
        changed=True,
        message=(
            "Cloud edits were preserved byte-for-byte as a new verified DOCX "
            "revision. Review approvals were reset."
        ),
    )


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
        select(Task)
        .where(
            Task.id == task_id,
            Task.tenant_id == uuid.UUID(tenant_id),
        )
        .with_for_update()
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if (
        task.review_policy == "staff_then_attorney"
        and payload.status == "in_progress"
        and not staged_review_is_approved(task)
    ):
        raise HTTPException(
            status_code=409,
            detail="Attorney approval is required before this staged task can proceed",
        )

    previous_calendar_user_id = task_calendar_user_id(task)
    previous_assignee_id = task.assigned_to_user_id
    previous_status = task.status
    updates = payload.model_dump(exclude_none=True)
    reference_fields = {
        "matter_id",
        "contact_id",
        "assigned_to_user_id",
        "reviewer_user_id",
    }
    for field in reference_fields & payload.model_fields_set:
        # PATCH distinguishes omission (leave unchanged) from an explicit null
        # (remove the optional link). Other nullable-looking fields retain the
        # endpoint's existing exclude-none behavior.
        updates[field] = getattr(payload, field)
    expected_version = updates.pop("expected_version", None)
    acknowledge_prior_delivery_risk = updates.pop(
        "acknowledge_prior_delivery_risk", False
    )
    if (
        task.status == "review"
        and task.pending_action
        and updates.get("status") == "in_progress"
        and expected_version is None
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "This approval must include the proposal version. "
                "Refresh the task and approve the current draft."
            ),
        )
    assignment_note = (updates.pop("assignment_note", None) or "").strip() or None
    closed_reason = (updates.pop("closed_reason", None) or "").strip() or None
    context_changes = {
        field
        for field in {"matter_id", "contact_id"} & payload.model_fields_set
        if updates.get(field) != getattr(task, field)
    }
    if context_changes:
        has_delivery_audit = (
            await db.scalar(
                select(func.count())
                .select_from(TaskAutomationRun)
                .where(
                    TaskAutomationRun.task_id == task.id,
                    TaskAutomationRun.tenant_id == task.tenant_id,
                )
            )
            or 0
        ) > 0
        if task.pending_action or has_delivery_audit:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A task with an outbound draft or delivery audit cannot be "
                    "relinked to a different matter/contact. Create a new task."
                ),
            )
    changed_references = {
        field: updates[field] for field in reference_fields & payload.model_fields_set
    }
    # Validate only links this PATCH writes. Historical assignments may become
    # inactive after creation; that must not prevent completion, notes, or
    # cleanup, while any newly supplied reference remains fail-closed.
    await _require_task_references_for_tenant(
        db, uuid.UUID(tenant_id), changed_references
    )
    if expected_version is not None and expected_version != task.version:
        current_response = await _task_response_with_delivery(db, task)
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This task changed after it was loaded. Review the latest task and try again.",
                "current_task": current_response.model_dump(mode="json"),
            },
        )
    calendar_changed = bool(_CALENDAR_RELEVANT_FIELDS & set(updates))
    assignment_changed = "assigned_to_user_id" in updates

    new_status = updates.pop("status", None)
    waiting_reason = updates.pop("waiting_reason", None)
    waiting_follow_up_date = updates.pop("waiting_follow_up_date", None)
    reviewer_user_id = updates.pop("reviewer_user_id", None)
    if (
        task.review_policy == "staff_then_attorney"
        and "reviewer_user_id" in payload.model_fields_set
        and reviewer_user_id != task.reviewer_user_id
    ):
        raise HTTPException(
            status_code=409,
            detail="Staged reviewers must be reassigned through the review workflow",
        )
    if task.pending_action:
        reviewer_change = (
            "reviewer_user_id" in payload.model_fields_set
            and reviewer_user_id != task.reviewer_user_id
        )
        status_change = new_status is not None and new_status != task.status
        if status_change or reviewer_change:
            await _require_action_reviewer_or_approver(db, task, current_user)
    transition_changed = False
    workflow_metadata_requested = bool(
        {"waiting_reason", "waiting_follow_up_date", "reviewer_user_id"}
        & payload.model_fields_set
    )
    if new_status is None and workflow_metadata_requested:
        if task.status not in {"waiting", "review"}:
            raise HTTPException(
                status_code=422,
                detail="Waiting and reviewer fields require a Waiting or Review task",
            )
        new_status = task.status
    if new_status is not None:
        try:
            transition_changed = transition_task(
                db,
                task,
                to_status=new_status,
                actor_user_id=current_user.id,
                expected_version=expected_version,
                reason=waiting_reason if new_status == "waiting" else closed_reason,
                waiting_follow_up_date=waiting_follow_up_date,
                reviewer_user_id=reviewer_user_id,
            )
        except TaskWorkflowError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

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

    closing = (
        task.status in ("completed", "cancelled") and task.status != previous_status
    )
    if (
        new_status is None
        and closed_reason
        and task.status in ("completed", "cancelled")
    ):
        task.closed_reason = closed_reason

    if reassigned:
        internal_event = (
            "unassigned"
            if task.assigned_to_user_id is None
            else "reassigned"
            if previous_assignee_id
            else "assigned"
        )
        append_task_event(
            db,
            task,
            event_type=internal_event,
            actor_user_id=current_user.id,
            note=assignment_note,
            metadata={
                "previous_assignee_user_id": (
                    str(previous_assignee_id) if previous_assignee_id else None
                ),
                "assigned_to_user_id": (
                    str(task.assigned_to_user_id) if task.assigned_to_user_id else None
                ),
            },
        )
        await record_task_event(
            db,
            task,
            event=internal_event,
            actor=current_user,
            note=assignment_note,
            previous_assignee_user_id=previous_assignee_id,
        )
    if closing:
        await record_task_event(
            db, task, event=task.status, actor=current_user, note=task.closed_reason
        )

    changed_fields = sorted(set(updates) - {"assigned_to_user_id"})
    if changed_fields:
        append_task_event(
            db,
            task,
            event_type="updated",
            actor_user_id=current_user.id,
            metadata={"fields": changed_fields},
        )
    if (
        updates or reassigned or (closed_reason and not transition_changed)
    ) and not transition_changed:
        increment_task_version(task)

    # Same transactional enqueue as the transition endpoint, for the same reason:
    # this endpoint can also approve drafted work out of Review.
    try:
        await enqueue_durable_automation(
            db,
            task,
            from_status=previous_status,
            to_status=task.status,
            actor_user_id=current_user.id,
            acknowledge_prior_delivery_risk=acknowledge_prior_delivery_risk,
        )
    except ActionApprovalConflict as exc:
        await db.rollback()
        await set_tenant_context(db, tenant_id)
        current_task = await _load_task_or_404(db, task_id, uuid.UUID(tenant_id))
        current_response = await _task_response_with_delivery(db, current_task)
        raise HTTPException(
            status_code=409,
            detail={
                "message": exc.detail,
                "current_task": current_response.model_dump(mode="json"),
            },
        ) from exc

    await db.commit()
    await set_tenant_context(db, tenant_id)
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
    # Connected-mail token refresh can commit during assignment notification,
    # which clears SET LOCAL before the delivery-state query below.
    await set_tenant_context(db, tenant_id)

    return await _task_response_with_delivery(db, task)


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(Task)
        .where(
            Task.id == task_id,
            Task.tenant_id == uuid.UUID(tenant_id),
        )
        .with_for_update()
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    delivery_audit_id = await db.scalar(
        select(TaskAutomationRun.id)
        .where(
            TaskAutomationRun.task_id == task.id,
            TaskAutomationRun.tenant_id == task.tenant_id,
        )
        .limit(1)
    )
    if delivery_audit_id is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This task has an outbound delivery audit and cannot be deleted. "
                "Keep it as the record of what was approved and delivered."
            ),
        )

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
