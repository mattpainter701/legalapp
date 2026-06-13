"""
Reports router — firm-level analytics.

  GET /api/reports/matters              Matter status breakdown
  GET /api/reports/intake               Intake funnel stats
  GET /api/reports/overdue-tasks        Overdue task list
  GET /api/reports/bundle               All three in one response
  GET /api/reports/matters/{id}/budget  Matter budget vs actuals
  GET /api/reports/billing/realization  Billable vs collected per matter
  GET /api/reports/billing/wip          Uninvoiced billable work per matter
  GET /api/reports/billing/aging        A/R aging buckets per matter
"""

import csv
import io
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.billing import Invoice, Payment, TimeEntry
from app.models.contact import Lead
from app.models.plugin import Matter
from app.models.task import Task
from app.schemas.reports import (
    FirmReportBundle,
    IntakeFunnelReport,
    MatterBudgetReport,
    MatterStatusReport,
    OverdueTasksReport,
)

router = APIRouter(prefix="/api/reports", tags=["reports"])

# Invoice statuses that do NOT represent outstanding receivables
_NON_RECEIVABLE_STATUSES = {"draft", "paid", "cancelled", "void"}

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
            Matter.risk_level,
            func.count(Matter.id),
        )
        .where(Matter.tenant_id == tenant_id)
        .group_by(Matter.risk_level)
    )
    by_risk_level: dict[str, int] = {
        row[0] or "unset": row[1] for row in risk_rows.all()
    }

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


async def _realization_report(db: AsyncSession, tenant_id: uuid.UUID) -> list[dict]:
    """Per-matter billable hours/amount vs. amount collected via payments."""

    # Billable hours/amount per matter (only matters with billable time entries)
    time_rows = await db.execute(
        select(
            TimeEntry.matter_id,
            Matter.matter_name,
            func.sum(TimeEntry.hours),
            func.sum(TimeEntry.amount),
        )
        .join(Matter, Matter.id == TimeEntry.matter_id)
        .where(
            TimeEntry.tenant_id == tenant_id,
            TimeEntry.is_billable.is_(True),
        )
        .group_by(TimeEntry.matter_id, Matter.matter_name)
    )

    results: dict[uuid.UUID, dict] = {}
    for matter_id, matter_name, billable_hours, billable_amount in time_rows.all():
        results[matter_id] = {
            "matter_id": str(matter_id),
            "matter_name": matter_name,
            "billable_hours": float(billable_hours or 0),
            "billable_amount": float(billable_amount or 0),
            "collected_amount": 0.0,
        }

    if not results:
        return []

    # Amount collected per matter via Payment -> Invoice -> matter_id
    payment_rows = await db.execute(
        select(
            Invoice.matter_id,
            func.sum(Payment.amount),
        )
        .join(Payment, Payment.invoice_id == Invoice.id)
        .where(
            Invoice.tenant_id == tenant_id,
            Invoice.matter_id.in_(results.keys()),
        )
        .group_by(Invoice.matter_id)
    )
    for matter_id, collected in payment_rows.all():
        if matter_id in results:
            results[matter_id]["collected_amount"] = float(collected or 0)

    rows = []
    for row in results.values():
        billable_amount = row["billable_amount"]
        collected_amount = row["collected_amount"]
        realization_pct = (
            round(collected_amount / billable_amount * 100, 1)
            if billable_amount > 0
            else 0.0
        )
        rows.append({**row, "realization_pct": realization_pct})

    return rows


async def _wip_report(db: AsyncSession, tenant_id: uuid.UUID) -> list[dict]:
    """Per-matter uninvoiced billable time (work-in-progress)."""

    rows = await db.execute(
        select(
            TimeEntry.matter_id,
            Matter.matter_name,
            func.sum(TimeEntry.hours),
            func.sum(TimeEntry.amount),
        )
        .join(Matter, Matter.id == TimeEntry.matter_id)
        .where(
            TimeEntry.tenant_id == tenant_id,
            TimeEntry.is_billable.is_(True),
            TimeEntry.invoice_id.is_(None),
        )
        .group_by(TimeEntry.matter_id, Matter.matter_name)
    )

    result = []
    for matter_id, matter_name, wip_hours, wip_value in rows.all():
        wip_hours_f = float(wip_hours or 0)
        if wip_hours_f <= 0:
            continue
        result.append(
            {
                "matter_id": str(matter_id),
                "matter_name": matter_name,
                "wip_hours": wip_hours_f,
                "wip_value": float(wip_value or 0),
            }
        )

    return result


async def _aging_report(db: AsyncSession, tenant_id: uuid.UUID) -> list[dict]:
    """Per-matter outstanding A/R balance, bucketed by days overdue."""

    today = date.today()

    # Outstanding invoices (not draft/paid/cancelled/void) with their matter
    invoice_rows = await db.execute(
        select(
            Invoice.id,
            Invoice.matter_id,
            Matter.matter_name,
            Invoice.total,
            Invoice.due_date,
        )
        .join(Matter, Matter.id == Invoice.matter_id)
        .where(
            Invoice.tenant_id == tenant_id,
            Invoice.status.not_in(list(_NON_RECEIVABLE_STATUSES)),
        )
    )
    invoices = invoice_rows.all()
    if not invoices:
        return []

    invoice_ids = [row.id for row in invoices]

    # Sum of payments per invoice
    payment_rows = await db.execute(
        select(
            Payment.invoice_id,
            func.sum(Payment.amount),
        )
        .where(
            Payment.tenant_id == tenant_id,
            Payment.invoice_id.in_(invoice_ids),
        )
        .group_by(Payment.invoice_id)
    )
    paid_by_invoice: dict[uuid.UUID, float] = {
        invoice_id: float(total or 0) for invoice_id, total in payment_rows.all()
    }

    buckets: dict[uuid.UUID, dict] = {}
    for row in invoices:
        total = float(row.total or 0)
        paid = paid_by_invoice.get(row.id, 0.0)
        balance = total - paid
        if balance <= 0:
            continue

        bucket = buckets.setdefault(
            row.matter_id,
            {
                "matter_id": str(row.matter_id),
                "matter_name": row.matter_name,
                "days_0_30": 0.0,
                "days_31_60": 0.0,
                "days_61_90": 0.0,
                "days_90_plus": 0.0,
            },
        )

        days_overdue = (today - row.due_date).days
        if days_overdue <= 30:
            bucket["days_0_30"] += balance
        elif days_overdue <= 60:
            bucket["days_31_60"] += balance
        elif days_overdue <= 90:
            bucket["days_61_90"] += balance
        else:
            bucket["days_90_plus"] += balance

    return list(buckets.values())


def _csv_response(rows: list[dict], filename: str) -> StreamingResponse:
    """Build a StreamingResponse rendering ``rows`` as a CSV download."""

    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    buffer.seek(0)

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get("/matters", response_model=MatterStatusReport)
async def get_matter_status_report(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    return await _matter_status_report(db, tenant_id)


@router.get("/intake", response_model=IntakeFunnelReport)
async def get_intake_funnel_report(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    return await _intake_funnel_report(db, tenant_id)


@router.get("/overdue-tasks", response_model=OverdueTasksReport)
async def get_overdue_tasks_report(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    return await _overdue_tasks_report(db, tenant_id)


@router.get("/bundle", response_model=FirmReportBundle)
async def get_reports_bundle(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
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
    tenant_id = current_user.tenant_id
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
            TimeEntry.is_billable.is_(True),
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


@router.get("/billing/realization")
async def get_realization_report(
    format: str = Query("json"),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Per-matter billable hours/amount vs. amount collected."""
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    rows = await _realization_report(db, tenant_id)

    if format == "csv":
        return _csv_response(rows, "realization.csv")
    return rows


@router.get("/billing/wip")
async def get_wip_report(
    format: str = Query("json"),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Per-matter uninvoiced billable time (work-in-progress)."""
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    rows = await _wip_report(db, tenant_id)

    if format == "csv":
        return _csv_response(rows, "wip.csv")
    return rows


@router.get("/billing/aging")
async def get_aging_report(
    format: str = Query("json"),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Per-matter outstanding A/R balance bucketed by days overdue."""
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    rows = await _aging_report(db, tenant_id)

    if format == "csv":
        return _csv_response(rows, "aging.csv")
    return rows
