"""
Calendar router — aggregate deadlines from tasks, matter key dates, and renewals.

  GET /api/calendar/events   list events in a date range (default: today + 90 days)
"""

import uuid
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.task import Task
from app.models.scheduled_event import ScheduledEvent
from app.models.plugin import Matter, Renewal
from app.models.estate import EstateDeadline
from app.schemas.calendar import (
    CalendarEvent,
    CalendarEventsResponse,
    CalendarSyncRequest,
    CalendarSyncResponse,
    ExternalCalendarEventResponse,
    ScheduledEventCreate,
    ScheduledEventResponse,
    ScheduledEventUpdate,
)
from app.services.calendar_sync import calendar_sync
from app.services.scheduled_events import create_external_event, delete_external_event

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


def _date_range_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(start, time.min),
        datetime.combine(end + timedelta(days=1), time.min),
    )


async def run_calendar_sync(
    body: CalendarSyncRequest,
    current_user,
    db: AsyncSession,
) -> CalendarSyncResponse:
    tenant_id = str(current_user.tenant_id)
    user_id = str(current_user.id)
    if body.user_id and body.user_id != user_id:
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")
        user_id = body.user_id

    await set_tenant_context(db, tenant_id)

    try:
        if body.provider == "microsoft":
            events = await calendar_sync.ms_get_events(db, tenant_id, user_id)
        elif body.provider == "google":
            events = await calendar_sync.google_get_events(db, tenant_id, user_id)
        else:
            raise HTTPException(
                status_code=400, detail=f"Unsupported provider: {body.provider}"
            )
    except ValueError as exc:
        raise HTTPException(status_code=424, detail=str(exc))

    deadlines_created = 0
    if body.sync_deadlines:
        try:
            sync_result = await calendar_sync.sync_deadlines_to_calendar(
                db, tenant_id, user_id, body.provider
            )
            deadlines_created = sync_result.get("created", 0)
        except ValueError as exc:
            raise HTTPException(status_code=424, detail=str(exc))

    return CalendarSyncResponse(
        provider=body.provider,
        events=[
            ExternalCalendarEventResponse(
                id=e["id"],
                provider=e["provider"],
                subject=e.get("subject"),
                start=e.get("start"),
                end=e.get("end"),
                location=e.get("location"),
            )
            for e in events
        ],
        deadlines_created=deadlines_created,
    )


@router.get("/events", response_model=CalendarEventsResponse)
async def get_calendar_events(
    start: date = Query(default=None),
    end: date = Query(default=None),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all deadline events (tasks, matter key dates, renewals, estate deadlines) in [start, end]."""
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    tid = uuid.UUID(tenant_id)
    today = date.today()
    if start is None:
        start = today
    if end is None:
        end = today + timedelta(days=90)

    events: list[CalendarEvent] = []

    # ── Query 1: Tasks with due_date in range ─────────────────────────────────
    # Include completed tasks so they show as done on the calendar (not vanish).
    task_stmt = select(Task).where(
        Task.tenant_id == tid,
        Task.due_date >= start,
        Task.due_date <= end,
        Task.status.notin_(["cancelled"]),
    )
    task_result = await db.execute(task_stmt)
    tasks = task_result.scalars().all()

    for task in tasks:
        is_done = task.status in ("completed", "done")
        events.append(
            CalendarEvent(
                id=f"task-{task.id}",
                title=task.task_type or task.title,
                date=task.due_date,
                event_type="task_due",
                matter_id=task.matter_id,
                task_id=task.id,
                url="/tasks",
                is_completed=is_done,
            )
        )

    # ── Query 2: Matter key dates in range ────────────────────────────────────
    matter_stmt = (
        select(Matter)
        .where(
            Matter.tenant_id == tid,
            Matter.key_dates.isnot(None),
            Matter.is_closed.is_(False),
        )
        .limit(500)
    )
    matter_result = await db.execute(matter_stmt)
    matters = matter_result.scalars().all()

    for matter in matters:
        if not isinstance(matter.key_dates, dict):
            continue
        for key, val in matter.key_dates.items():
            if not isinstance(val, str):
                continue
            try:
                event_date = date.fromisoformat(val)
            except (ValueError, TypeError):
                continue
            if start <= event_date <= end:
                events.append(
                    CalendarEvent(
                        id=f"matter-{matter.id}-{key}",
                        title=f"{matter.matter_name} — {key.replace('_', ' ')}",
                        date=event_date,
                        event_type="matter_key_date",
                        matter_id=matter.id,
                        matter_name=matter.matter_name,
                        url=f"/matters/{matter.id}",
                    )
                )

    # ── Query 3: Renewals with renewal_date in range ──────────────────────────
    renewal_stmt = select(Renewal).where(
        Renewal.tenant_id == tid,
        Renewal.renewal_date >= start,
        Renewal.renewal_date <= end,
    )
    renewal_result = await db.execute(renewal_stmt)
    renewals = renewal_result.scalars().all()

    for renewal in renewals:
        renewal_date = (
            renewal.renewal_date
            if isinstance(renewal.renewal_date, date)
            else renewal.renewal_date.date()
            if hasattr(renewal.renewal_date, "date")
            else None
        )
        if renewal_date is None:
            continue
        events.append(
            CalendarEvent(
                id=f"renewal-{renewal.id}",
                title=f"{renewal.contract_name} ({renewal.vendor})",
                date=renewal_date,
                event_type="renewal",
                url="/plugins/commercial/renewals",
            )
        )

    # ── Query 4: Estate deadlines (tax filings, court dates) in range ─────────
    deadline_stmt = select(EstateDeadline).where(
        EstateDeadline.tenant_id == tid,
        EstateDeadline.due_date >= start,
        EstateDeadline.due_date <= end,
        EstateDeadline.status.notin_(["complete", "na", "cancelled"]),
    )
    deadline_result = await db.execute(deadline_stmt)
    deadlines = deadline_result.scalars().all()

    for dl in deadlines:
        events.append(
            CalendarEvent(
                id=f"estate-deadline-{dl.id}",
                title=f"{dl.title} ({dl.deadline_type.replace('_', ' ')})",
                date=dl.due_date,
                event_type="estate_deadline",
                url=f"/plugins/trust-estate/estates/{dl.estate_id}",
            )
        )

    # ── Query 5: Firm-created scheduled events / online meetings ─────────────
    range_start, range_end = _date_range_bounds(start, end)
    sched_stmt = select(ScheduledEvent).where(
        ScheduledEvent.tenant_id == tid,
        ScheduledEvent.start_at >= range_start,
        ScheduledEvent.start_at < range_end,
    )
    sched_result = await db.execute(sched_stmt)
    scheduled_events = sched_result.scalars().all()

    matter_names: dict[uuid.UUID, str] = {}
    matter_ids = [row.matter_id for row in scheduled_events if row.matter_id]
    if matter_ids:
        matter_name_result = await db.execute(
            select(Matter.id, Matter.matter_name).where(
                Matter.tenant_id == tid, Matter.id.in_(matter_ids)
            )
        )
        matter_names = {row.id: row.matter_name for row in matter_name_result}

    for row in scheduled_events:
        events.append(
            CalendarEvent(
                id=f"scheduled-{row.id}",
                title=row.title,
                date=row.start_at.date(),
                event_type="scheduled_event",
                matter_id=row.matter_id,
                matter_name=matter_names.get(row.matter_id) if row.matter_id else None,
                url=row.external_calendar_url,
                start=row.start_at.isoformat(),
                end=row.end_at.isoformat(),
                calendar_provider=row.calendar_provider,
                meeting_provider=row.meeting_provider,
                join_url=row.join_url,
                location=row.join_url or "",
            )
        )

    # ── Sort and return ───────────────────────────────────────────────────────
    events.sort(key=lambda e: e.date)

    return CalendarEventsResponse(events=events, total=len(events))


@router.post("/sync", response_model=CalendarSyncResponse)
async def sync_calendar(
    body: CalendarSyncRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await run_calendar_sync(body, current_user, db)


async def _require_matter(
    db: AsyncSession, tenant_id: str, matter_id: uuid.UUID | None
):
    if not matter_id:
        return None
    result = await db.execute(
        select(Matter.id).where(
            Matter.tenant_id == uuid.UUID(tenant_id), Matter.id == matter_id
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter_id


def _validate_scheduled_payload(
    calendar_provider: str | None, meeting_provider: str
) -> None:
    if meeting_provider == "teams" and calendar_provider != "microsoft":
        raise HTTPException(
            status_code=422,
            detail="Teams meetings require Microsoft Calendar as the calendar provider.",
        )


@router.get("/scheduled-events", response_model=list[ScheduledEventResponse])
async def list_scheduled_events(
    start: date = Query(default=None),
    end: date = Query(default=None),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)
    today = date.today()
    start = start or today
    end = end or today + timedelta(days=90)
    range_start, range_end = _date_range_bounds(start, end)
    result = await db.execute(
        select(ScheduledEvent)
        .where(
            ScheduledEvent.tenant_id == uuid.UUID(tenant_id),
            ScheduledEvent.start_at >= range_start,
            ScheduledEvent.start_at < range_end,
        )
        .order_by(ScheduledEvent.start_at.asc())
    )
    return [
        ScheduledEventResponse.model_validate(row) for row in result.scalars().all()
    ]


@router.post(
    "/scheduled-events", response_model=ScheduledEventResponse, status_code=201
)
async def create_scheduled_event(
    body: ScheduledEventCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)
    if body.end_at <= body.start_at:
        raise HTTPException(status_code=422, detail="end_at must be after start_at")
    _validate_scheduled_payload(body.calendar_provider, body.meeting_provider)
    await _require_matter(db, tenant_id, body.matter_id)

    row = ScheduledEvent(
        tenant_id=uuid.UUID(tenant_id),
        matter_id=body.matter_id,
        created_by_user_id=current_user.id,
        title=body.title,
        description=body.description,
        start_at=body.start_at,
        end_at=body.end_at,
        timezone=body.timezone or "UTC",
        attendees=body.attendees or [],
        calendar_provider=body.calendar_provider,
        meeting_provider=body.meeting_provider,
        sync_status="pending",
    )
    db.add(row)
    await db.flush()
    row = await create_external_event(
        db, row, tenant_id=tenant_id, user_id=str(current_user.id)
    )
    await db.commit()
    await db.refresh(row)
    return ScheduledEventResponse.model_validate(row)


@router.patch("/scheduled-events/{event_id}", response_model=ScheduledEventResponse)
async def update_scheduled_event(
    event_id: uuid.UUID,
    body: ScheduledEventUpdate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)
    result = await db.execute(
        select(ScheduledEvent).where(
            ScheduledEvent.tenant_id == uuid.UUID(tenant_id),
            ScheduledEvent.id == event_id,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Scheduled event not found")

    updates = body.model_dump(exclude_unset=True)
    next_calendar = updates.get("calendar_provider", row.calendar_provider)
    next_meeting = updates.get("meeting_provider", row.meeting_provider)
    if next_calendar in ("", "none"):
        next_calendar = None
        updates["calendar_provider"] = None
    if next_meeting is None:
        next_meeting = "none"
    _validate_scheduled_payload(next_calendar, next_meeting)
    next_start = updates.get("start_at", row.start_at)
    next_end = updates.get("end_at", row.end_at)
    if next_end <= next_start:
        raise HTTPException(status_code=422, detail="end_at must be after start_at")
    if "matter_id" in updates:
        await _require_matter(db, tenant_id, updates["matter_id"])

    await delete_external_event(
        db, row, tenant_id=tenant_id, user_id=str(current_user.id)
    )
    for field, value in updates.items():
        setattr(row, field, value)
    row.external_calendar_event_id = None
    row.external_calendar_url = None
    row.meeting_id = None
    row.join_url = None
    row.sync_status = "pending"
    row.sync_error = None
    row = await create_external_event(
        db, row, tenant_id=tenant_id, user_id=str(current_user.id)
    )
    await db.commit()
    await db.refresh(row)
    return ScheduledEventResponse.model_validate(row)


@router.delete("/scheduled-events/{event_id}", status_code=204)
async def delete_scheduled_event(
    event_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)
    result = await db.execute(
        select(ScheduledEvent).where(
            ScheduledEvent.tenant_id == uuid.UUID(tenant_id),
            ScheduledEvent.id == event_id,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Scheduled event not found")
    await delete_external_event(
        db, row, tenant_id=tenant_id, user_id=str(current_user.id)
    )
    await db.delete(row)
    await db.commit()
    return None
