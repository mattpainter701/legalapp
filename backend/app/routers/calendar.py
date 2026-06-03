"""
Calendar router — aggregate deadlines from tasks, matter key dates, and renewals.

  GET /api/calendar/events   list events in a date range (default: today + 90 days)
"""

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.task import Task
from app.models.plugin import Matter, Renewal
from app.schemas.calendar import CalendarEvent, CalendarEventsResponse

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


@router.get("/events", response_model=CalendarEventsResponse)
async def get_calendar_events(
    start: date = Query(default=None),
    end: date = Query(default=None),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all deadline events (tasks, matter key dates, renewals) in [start, end]."""
    tenant_id = current_user["tenant_id"]
    await set_tenant_context(db, tenant_id)

    tid = uuid.UUID(tenant_id)
    today = date.today()
    if start is None:
        start = today
    if end is None:
        end = today + timedelta(days=90)

    events: list[CalendarEvent] = []

    # ── Query 1: Tasks with due_date in range ─────────────────────────────────
    task_stmt = select(Task).where(
        Task.tenant_id == tid,
        Task.due_date >= start,
        Task.due_date <= end,
        Task.status.notin_(["completed", "done", "cancelled"]),
    )
    task_result = await db.execute(task_stmt)
    tasks = task_result.scalars().all()

    for task in tasks:
        events.append(
            CalendarEvent(
                id=f"task-{task.id}",
                title=task.task_type or task.title,
                date=task.due_date,
                event_type="task_due",
                matter_id=task.matter_id,
                task_id=task.id,
                url="/tasks",
            )
        )

    # ── Query 2: Matter key dates in range ────────────────────────────────────
    matter_stmt = select(Matter).where(
        Matter.tenant_id == tid,
        Matter.key_dates.isnot(None),
        Matter.is_closed.is_(False),
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
                        url=f"/plugins/litigation/matters/{matter.id}",
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

    # ── Sort and return ───────────────────────────────────────────────────────
    events.sort(key=lambda e: e.date)

    return CalendarEventsResponse(events=events, total=len(events))
