"""
Reports router — firm-level analytics.

  GET /api/reports/matters              Matter status breakdown
  GET /api/reports/intake               Intake funnel stats
  GET /api/reports/overdue-tasks        Overdue task list
  GET /api/reports/bundle               All three in one response
  GET /api/reports/matters/{id}/budget  Matter budget vs actuals
"""

import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.contact import Lead
from app.models.plugin import Matter
from app.models.task import Task
from app.schemas.reports import (
    FirmReportBundle,
    IntakeFunnelReport,
    MatterStatusReport,
    OverdueTasksReport,
)

router = APIRouter(prefix="/api/reports", tags=["reports"])

# Lead statuses that represent a converted / matter-opened lead
_CONVERTED_STATUSES = {"matter_opened"}

# Task statuses that count as done (not overdue)
_DONE_STATUSES = {"completed", "cancelled", "done"}


async def _matter_status_report(
    db: AsyncSession, tenant_id: uuid.UUID
) -> MatterStatusReport:
    """Aggregate matter counts by status, type, and risk level."""

    # total
    total_result = await db.execute(
        select(func.count(Matter.id)).where(Matter.tenant_id == tenant_id)
    )
    total_matters = total_result.scalar_one() or 0

    # by status
    status_rows = await db.execute(
        select(Matter.status, func.count(Matter.id))
        .where(Matter.tenant_id == tenant_id)
        .group_by(Matter.status)
    )
    by_status: dict[str, int] = {row[0]: row[1] for row in status_rows.all()}

    # by matter_type
    type_rows = await db.execute(
        select(Matter.matter_type, func.count(Matter.id))
        .where(Matter.tenant_id == tenant_id)
        .group_by(Matter.matter_type)
    )
    by_type: dict[str, int] = {row[0]: row[1] for row in type_rows.all()}

    # by risk_level — risk_level is nullable, bucket nulls as "unset"
    risk_rows = await db.execute(
        select(
            func.coalesce(Matter.risk_level, "unset"),
            func.count(Matter.id),
        )
        .where(Matter.tenant_id == tenant_id)
        .group_by(func.coalesce(Matter.risk_level, "unset"))
    )
    by_risk_level: dict[str, int] = {row[0]: row[1] for row in risk_rows.all()}

    return MatterStatusReport(
        total_matters=total_matters,
        by_status=by_status,
        by_type=by_type,
        by_risk_level=by_risk_level,
    )


async def _intake_funnel_report(
    db: AsyncSession, tenant_id: uuid.UUID
) -> IntakeFunnelReport:
    """Aggregate lead counts by status and compute conversion rate."""

    total_result = await db.execute(
        select(func.count(Lead.id)).where(Lead.tenant_id == tenant_id)
    )
    total_leads = total_result.scalar_one() or 0

    status_rows = await db.execute(
        select(Lead.status, func.count(Lead.id))
        .where(Lead.tenant_id == tenant_id)
        .group_by(Lead.status)
    )
    by_status: dict[str, int] = {row[0]: row[1] for row in status_rows.all()}

    converted = sum(
        count for status, count in by_status.items() if status in _CONVERTED_STATUSES
    )
    conversion_rate = round(converted / total_leads, 4) if total_leads else 0.0

    return IntakeFunnelReport(
        total_leads=total_leads,
        by_status=by_status,
        conversion_rate=conversion_rate,
    )


async def _overdue_tasks_report(
    db: AsyncSession, tenant_id: uuid.UUID
) -> OverdueTasksReport:
    """Return tasks where due_date < today and status is still open."""

    today = date.today()

    # Join Task → Matter (outer join — task may have no matter)
    rows = await db.execute(
        select(
            Task.id,
            Task.title,
            Task.due_date,
            Matter.matter_name,
        )
        .outerjoin(Matter, Task.matter_id == Matter.id)
        .where(
            Task.tenant_id == tenant_id,
            Task.due_date < today,
            Task.status.not_in(list(_DONE_STATUSES)),
        )
        .order_by(Task.due_date.asc())
    )

    tasks = []
    for row in rows.all():
        tasks.append(
            {
                "id": str(row.id),
                "title": row.title,
                "due_date": row.due_date.isoformat() if row.due_date else None,
                "matter_name": row.matter_name,
            }
        )

    return OverdueTasksReport(total_overdue=len(tasks), tasks=tasks)


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get("/matters", response_model=MatterStatusReport)
async def get_matter_status_report(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = uuid.UUID(current_user["tenant_id"])
    await set_tenant_context(db, str(tenant_id))
    return await _matter_status_report(db, tenant_id)


@router.get("/intake", response_model=IntakeFunnelReport)
async def get_intake_funnel_report(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = uuid.UUID(current_user["tenant_id"])
    await set_tenant_context(db, str(tenant_id))
    return await _intake_funnel_report(db, tenant_id)


@router.get("/overdue-tasks", response_model=OverdueTasksReport)
async def get_overdue_tasks_report(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = uuid.UUID(current_user["tenant_id"])
    await set_tenant_context(db, str(tenant_id))
    return await _overdue_tasks_report(db, tenant_id)


@router.get("/bundle", response_model=FirmReportBundle)
async def get_reports_bundle(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = uuid.UUID(current_user["tenant_id"])
    await set_tenant_context(db, str(tenant_id))

    matter_status = await _matter_status_report(db, tenant_id)
    intake_funnel = await _intake_funnel_report(db, tenant_id)
    overdue_tasks = await _overdue_tasks_report(db, tenant_id)

    return FirmReportBundle(
        matter_status=matter_status,
        intake_funnel=intake_funnel,
        overdue_tasks=overdue_tasks,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/matters/{matter_id}/budget", response_model=MatterBudgetReport)
async def get_matter_budget_report(
    matter_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return budget utilization for a single matter."""
    tenant_id = uuid.UUID(current_user["tenant_id"])
    await set_tenant_context(db, str(tenant_id))

    # Verify matter belongs to tenant
    result = await db.execute(
        select(Matter).where(
            Matter.id == matter_id,
            Matter.tenant_id == tenant_id,
        )
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    # Sum billable hours and amounts
    agg_result = await db.execute(
        select(
            func.coalesce(func.sum(TimeEntry.hours), 0),
            func.coalesce(func.sum(TimeEntry.amount), 0),
        ).where(
            TimeEntry.matter_id == matter.id,
            TimeEntry.tenant_id == tenant_id,
            TimeEntry.is_billable == True,
        )
    )
    total_hours_decimal, total_billed_decimal = agg_result.one()

    total_hours = float(total_hours_decimal)
    total_billed = float(total_billed_decimal)
    budget_amt = float(matter.budget_amount) if matter.budget_amount else None

    utilization_pct = None
    if budget_amt and budget_amt > 0:
        utilization_pct = round(total_billed / budget_amt * 100, 1)

    return MatterBudgetReport(
        matter_id=str(matter.id),
        matter_name=matter.matter_name,
        budget_amount=budget_amt,
        budget_currency=matter.budget_currency,
        total_hours=total_hours,
        total_billed=total_billed,
        utilization_pct=utilization_pct,
    )
