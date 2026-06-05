"""Portfolio router — cross-matter views for the current user."""

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.matter_assignment import MatterAssignment
from app.models.plugin import Matter
from app.models.task import Task

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("/upcoming")
async def get_portfolio_upcoming(
    days: int = 14,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Tasks due in the next N days across all matters assigned to the current user."""
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))

    today = date.today()
    cutoff = today + timedelta(days=days)

    # Find all matters assigned to this user
    assigned_result = await db.execute(
        select(MatterAssignment.matter_id).where(
            MatterAssignment.user_id == current_user.id,
            MatterAssignment.tenant_id == tenant_id,
        )
    )
    matter_ids = [row[0] for row in assigned_result.all()]

    if not matter_ids:
        return {"tasks": [], "total": 0}

    # Get matter names in one query
    matter_result = await db.execute(
        select(Matter)
        .where(
            Matter.id.in_(matter_ids),
            Matter.tenant_id == tenant_id,
        )
        .options(selectinload(Matter.client))
    )
    matter_rows = matter_result.unique().scalars().all()
    matter_map = {str(row.id): row for row in matter_rows}

    # Get upcoming tasks across those matters
    task_result = await db.execute(
        select(Task).where(
            Task.tenant_id == tenant_id,
            Task.matter_id.in_(matter_ids),
            Task.due_date >= today,
            Task.due_date <= cutoff,
            Task.status.notin_(["completed", "cancelled"]),
        ).order_by(Task.due_date, Task.priority)
    )
    tasks = task_result.scalars().all()

    def _client_name(matter_row):
        try:
            return matter_row.client.display_name if matter_row.client else None
        except Exception:
            return None

    items = []
    for t in tasks:
        m = matter_map.get(str(t.matter_id))
        items.append({
            "id": str(t.id),
            "title": t.title,
            "task_type": t.task_type,
            "due_date": str(t.due_date),
            "due_time": str(t.due_time) if t.due_time else None,
            "priority": t.priority,
            "status": t.status,
            "matter_id": str(t.matter_id) if t.matter_id else None,
            "matter_name": m.matter_name if m else None,
            "client_name": _client_name(m) if m else None,
        })

    return {"tasks": items, "total": len(items)}
