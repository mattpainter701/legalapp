"""Extended billing router — time entries, expenses, invoice generation, payments."""

import asyncio
import logging
import uuid
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context, async_session_maker
from app.middleware.tenant import get_current_user
from app.services.access_control import require_finance_admin
from app.models.billing import TimeEntry, Expense, Invoice, InvoiceLineItem, Payment
from app.models.plugin import Matter
from app.models.tenant import TenantSettings
from app.schemas.billing import (
    TimeEntryCreate,
    TimeEntryUpdate,
    TimeEntryResponse,
    TimeEntryListResponse,
    TimerStartRequest,
    TimerStopRequest,
    ExpenseCreate,
    ExpenseUpdate,
    ExpenseResponse,
    ExpenseListResponse,
    InvoiceUpdate,
    InvoiceResponse,
    InvoiceListResponse,
    InvoiceLineItemResponse,
    GenerateInvoiceRequest,
    PaymentCreate,
    PaymentResponse,
    StripePaymentLinkResponse,
    InvoiceExportRequest,
    BillingSettingsResponse,
    BillingSettingsUpdate,
)
from app.services.billing_workflow import (
    DEFAULT_ROUNDING_MINUTES,
    can_transition_invoice,
    is_invoice_overdue,
    next_invoice_number,
    round_timer_hours,
)

settings = get_settings()
router = APIRouter(prefix="/api/billing", tags=["billing"])
logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _tenant_filter(tenant_id: uuid.UUID):
    """Return filter kwargs for tenant-scoped queries."""
    return {"tenant_id": tenant_id}


async def _get_billing_config(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """Return the tenant's billing config dict from TenantSettings.custom_config."""
    ts_result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )
    ts = ts_result.scalar_one_or_none()
    if ts and ts.custom_config:
        return (ts.custom_config or {}).get("billing", {}) or {}
    return {}


async def _resolve_hourly_rate(
    db: AsyncSession,
    user,
    matter_obj: Matter,
    explicit_rate: Decimal | None,
) -> Decimal | None:
    """Rate resolution: explicit > matter override > user default > tenant default."""
    rate = explicit_rate or matter_obj.hourly_rate or user.default_billing_rate
    if not rate:
        billing_cfg = await _get_billing_config(db, user.tenant_id)
        tenant_default = billing_cfg.get("default_hourly_rate")
        if tenant_default:
            rate = Decimal(str(tenant_default))
    return rate


async def _get_matter_or_404(
    db: AsyncSession, matter_id: str, tenant_id: uuid.UUID
) -> Matter:
    try:
        matter_uuid = uuid.UUID(matter_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid matter ID format")
    matter_result = await db.execute(
        select(Matter).where(
            Matter.id == matter_uuid,
            Matter.tenant_id == tenant_id,
        )
    )
    matter_obj = matter_result.scalar_one_or_none()
    if not matter_obj:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter_obj


# ── Time Entries ────────────────────────────────────────────────────────────


@router.post("/time-entries", status_code=201)
async def create_time_entry(
    body: TimeEntryCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TimeEntryResponse:
    """Create a billable time entry linked to a matter."""
    user = await get_current_user(request, db)

    # Verify matter belongs to tenant
    await set_tenant_context(db, str(user.tenant_id))
    matter_obj = await _get_matter_or_404(db, body.matter_id, user.tenant_id)

    hourly_rate = await _resolve_hourly_rate(db, user, matter_obj, body.hourly_rate)
    if not hourly_rate:
        raise HTTPException(
            status_code=400,
            detail="No hourly rate provided and no default billing rate set on your profile. Please contact your administrator.",
        )

    try:
        amount = body.hours * hourly_rate
        entry = TimeEntry(
            tenant_id=user.tenant_id,
            matter_id=matter_obj.id,
            user_id=user.id,
            description=body.description,
            hours=body.hours,
            hourly_rate=hourly_rate,
            amount=amount,
            date=body.date,
            is_billable=body.is_billable,
            utbms_task_code=body.utbms_task_code,
            utbms_activity_code=body.utbms_activity_code,
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
    except Exception:
        await db.rollback()
        logger.exception("Failed to create time entry")
        raise HTTPException(
            status_code=500,
            detail="Failed to create time entry due to a server error. Please try again.",
        )

    return TimeEntryResponse.model_validate(entry, from_attributes=True)


def _time_entry_filters(
    tenant_id: uuid.UUID,
    matter_id: str | None,
    status: str | None,
    unbilled_only: bool,
    user_id: str | None,
    date_from: date | None,
    date_to: date | None,
    billable_only: bool,
):
    """Shared WHERE clauses for the time-entry list and its totals query."""
    conditions = [TimeEntry.tenant_id == tenant_id]
    if matter_id:
        conditions.append(TimeEntry.matter_id == matter_id)
    if status:
        conditions.append(TimeEntry.status == status)
    if unbilled_only:
        conditions.append(TimeEntry.invoice_id.is_(None))
    if user_id:
        conditions.append(TimeEntry.user_id == user_id)
    if date_from:
        conditions.append(TimeEntry.date >= date_from)
    if date_to:
        conditions.append(TimeEntry.date <= date_to)
    if billable_only:
        conditions.append(TimeEntry.is_billable.is_(True))
    return conditions


@router.get("/time-entries")
async def list_time_entries(
    matter_id: str | None = Query(None),
    status: str | None = Query(None),
    unbilled_only: bool = Query(False),
    user_id: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    billable_only: bool = Query(False),
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
) -> TimeEntryListResponse:
    """List time entries with optional filters and pagination.

    Totals (count/hours/amount) cover the whole filtered set, not just the
    returned page.
    """
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    conditions = _time_entry_filters(
        user.tenant_id,
        matter_id,
        status,
        unbilled_only,
        user_id,
        date_from,
        date_to,
        billable_only,
    )

    totals_result = await db.execute(
        select(
            func.count(TimeEntry.id),
            func.coalesce(func.sum(TimeEntry.hours), 0),
            func.coalesce(func.sum(TimeEntry.amount), 0),
        ).where(*conditions)
    )
    total_count, total_hours, total_amount = totals_result.one()

    stmt = (
        select(TimeEntry)
        .where(*conditions)
        .order_by(TimeEntry.date.desc(), TimeEntry.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    entries = result.scalars().all()

    return TimeEntryListResponse(
        items=[
            TimeEntryResponse.model_validate(e, from_attributes=True) for e in entries
        ],
        total=total_count,
        total_hours=Decimal(str(total_hours)),
        total_amount=Decimal(str(total_amount)),
    )


# ── Live Timer ──────────────────────────────────────────────────────────────
# NOTE: these routes must be declared before /time-entries/{entry_id} so
# "timer" isn't captured as an entry_id path parameter.


@router.post("/time-entries/timer/start", status_code=201)
async def start_timer(
    body: TimerStartRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TimeEntryResponse:
    """Start a live timer for the current user on a matter.

    Creates a time entry in 'running' status; hours/amount are computed on
    stop. Only one timer may run per user at a time.
    """
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    matter_obj = await _get_matter_or_404(db, body.matter_id, user.tenant_id)

    existing = await db.execute(
        select(TimeEntry).where(
            TimeEntry.tenant_id == user.tenant_id,
            TimeEntry.user_id == user.id,
            TimeEntry.timer_started_at.is_not(None),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="A timer is already running. Stop it before starting a new one.",
        )

    hourly_rate = await _resolve_hourly_rate(db, user, matter_obj, body.hourly_rate)
    if not hourly_rate:
        raise HTTPException(
            status_code=400,
            detail="No hourly rate provided and no default billing rate set on your profile. Please contact your administrator.",
        )

    now = datetime.now(timezone.utc)
    entry = TimeEntry(
        tenant_id=user.tenant_id,
        matter_id=matter_obj.id,
        user_id=user.id,
        description=body.description or "Timer session",
        hours=Decimal("0"),
        hourly_rate=hourly_rate,
        amount=Decimal("0"),
        date=now.date(),
        is_billable=body.is_billable,
        status="running",
        timer_started_at=now,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return TimeEntryResponse.model_validate(entry, from_attributes=True)


@router.get("/time-entries/timer")
async def get_active_timer(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TimeEntryResponse | None:
    """Return the current user's running timer, or null if none."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(TimeEntry).where(
            TimeEntry.tenant_id == user.tenant_id,
            TimeEntry.user_id == user.id,
            TimeEntry.timer_started_at.is_not(None),
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        return None
    return TimeEntryResponse.model_validate(entry, from_attributes=True)


@router.post("/time-entries/timer/stop")
async def stop_timer(
    body: TimerStopRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TimeEntryResponse:
    """Stop the current user's running timer.

    Elapsed time is rounded UP to the tenant's billing increment (default
    6 minutes) with a one-increment minimum, and the entry becomes a normal
    draft time entry ready for invoicing.
    """
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(TimeEntry).where(
            TimeEntry.tenant_id == user.tenant_id,
            TimeEntry.user_id == user.id,
            TimeEntry.timer_started_at.is_not(None),
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="No running timer found")

    billing_cfg = await _get_billing_config(db, user.tenant_id)
    rounding_minutes = int(
        billing_cfg.get("time_rounding_minutes") or DEFAULT_ROUNDING_MINUTES
    )

    started_at = entry.timer_started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()

    entry.hours = round_timer_hours(elapsed, rounding_minutes)
    entry.amount = entry.hours * entry.hourly_rate
    entry.status = "draft"
    entry.timer_started_at = None
    if body.description is not None and body.description.strip():
        entry.description = body.description.strip()

    await db.commit()
    await db.refresh(entry)
    return TimeEntryResponse.model_validate(entry, from_attributes=True)


@router.delete("/time-entries/timer", status_code=204)
async def cancel_timer(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Cancel the current user's running timer without logging time."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(TimeEntry).where(
            TimeEntry.tenant_id == user.tenant_id,
            TimeEntry.user_id == user.id,
            TimeEntry.timer_started_at.is_not(None),
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="No running timer found")

    await db.delete(entry)
    await db.commit()


@router.get("/time-entries/{entry_id}")
async def get_time_entry(
    entry_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TimeEntryResponse:
    """Get a single time entry by ID."""
    user = await get_current_user(request, db)

    result = await db.execute(
        select(TimeEntry).where(
            TimeEntry.id == entry_id,
            TimeEntry.tenant_id == user.tenant_id,
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Time entry not found")

    return TimeEntryResponse.model_validate(entry, from_attributes=True)


@router.patch("/time-entries/{entry_id}")
async def update_time_entry(
    entry_id: str,
    body: TimeEntryUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TimeEntryResponse:
    """Update a time entry. Only updates provided fields."""
    user = await get_current_user(request, db)

    result = await db.execute(
        select(TimeEntry).where(
            TimeEntry.id == entry_id,
            TimeEntry.tenant_id == user.tenant_id,
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Time entry not found")
    if entry.invoice_id and body.status not in ("written_off", None):
        raise HTTPException(
            status_code=400,
            detail="Cannot modify a billed time entry",
        )
    if entry.timer_started_at is not None:
        raise HTTPException(
            status_code=400,
            detail="Stop the running timer before editing this entry",
        )

    update_data = body.model_dump(exclude_unset=True)
    if "hours" in update_data or "hourly_rate" in update_data:
        hours = Decimal(str(update_data.get("hours", entry.hours)))
        rate = Decimal(str(update_data.get("hourly_rate", entry.hourly_rate)))
        update_data["amount"] = hours * rate

    for key, value in update_data.items():
        setattr(entry, key, value)

    await db.commit()
    await db.refresh(entry)
    return TimeEntryResponse.model_validate(entry, from_attributes=True)


@router.delete("/time-entries/{entry_id}", status_code=204)
async def delete_time_entry(
    entry_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete a time entry. Only unbilled entries can be deleted."""
    user = await get_current_user(request, db)

    result = await db.execute(
        select(TimeEntry).where(
            TimeEntry.id == entry_id,
            TimeEntry.tenant_id == user.tenant_id,
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Time entry not found")
    if entry.invoice_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete a billed time entry. Write it off instead.",
        )

    await db.delete(entry)
    await db.commit()


# ── Expenses ────────────────────────────────────────────────────────────────


@router.post("/expenses", status_code=201)
async def create_expense(
    body: ExpenseCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ExpenseResponse:
    """Create an expense (disbursement) linked to a matter."""
    user = await get_current_user(request, db)

    await set_tenant_context(db, str(user.tenant_id))
    matter = await db.execute(
        select(Matter).where(
            Matter.id == body.matter_id,
            Matter.tenant_id == user.tenant_id,
        )
    )
    if not matter.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Matter not found")

    expense = Expense(
        tenant_id=user.tenant_id,
        matter_id=uuid.UUID(body.matter_id),
        user_id=user.id,
        description=body.description,
        amount=body.amount,
        date=body.date,
        category=body.category,
        vendor=body.vendor,
        is_billable=body.is_billable,
    )
    db.add(expense)
    await db.commit()
    await db.refresh(expense)

    return ExpenseResponse.model_validate(expense, from_attributes=True)


@router.get("/expenses")
async def list_expenses(
    matter_id: str | None = Query(None),
    category: str | None = Query(None),
    unbilled_only: bool = Query(False),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
) -> ExpenseListResponse:
    """List expenses with optional filters."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    stmt = select(Expense).where(Expense.tenant_id == user.tenant_id)

    if matter_id:
        stmt = stmt.where(Expense.matter_id == matter_id)
    if category:
        stmt = stmt.where(Expense.category == category)
    if unbilled_only:
        stmt = stmt.where(Expense.invoice_id.is_(None))

    stmt = stmt.order_by(Expense.date.desc(), Expense.created_at.desc())

    result = await db.execute(stmt)
    expenses = result.scalars().all()

    total_amount = sum((e.amount for e in expenses), Decimal("0"))

    return ExpenseListResponse(
        items=[
            ExpenseResponse.model_validate(e, from_attributes=True) for e in expenses
        ],
        total=len(expenses),
        total_amount=total_amount,
    )


@router.get("/expenses/{expense_id}")
async def get_expense(
    expense_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ExpenseResponse:
    """Get a single expense by ID."""
    user = await get_current_user(request, db)

    result = await db.execute(
        select(Expense).where(
            Expense.id == expense_id,
            Expense.tenant_id == user.tenant_id,
        )
    )
    expense = result.scalar_one_or_none()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")

    return ExpenseResponse.model_validate(expense, from_attributes=True)


@router.patch("/expenses/{expense_id}")
async def update_expense(
    expense_id: str,
    body: ExpenseUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ExpenseResponse:
    """Update an expense."""
    user = await get_current_user(request, db)

    result = await db.execute(
        select(Expense).where(
            Expense.id == expense_id,
            Expense.tenant_id == user.tenant_id,
        )
    )
    expense = result.scalar_one_or_none()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    if expense.invoice_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot modify a billed expense",
        )

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(expense, key, value)

    await db.commit()
    await db.refresh(expense)
    return ExpenseResponse.model_validate(expense, from_attributes=True)


@router.delete("/expenses/{expense_id}", status_code=204)
async def delete_expense(
    expense_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete an expense. Only unbilled expenses can be deleted."""
    user = await get_current_user(request, db)

    result = await db.execute(
        select(Expense).where(
            Expense.id == expense_id,
            Expense.tenant_id == user.tenant_id,
        )
    )
    expense = result.scalar_one_or_none()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    if expense.invoice_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete a billed expense",
        )

    await db.delete(expense)
    await db.commit()


# ── Invoices ────────────────────────────────────────────────────────────────


async def _next_invoice_number(
    db: AsyncSession, tenant_id: uuid.UUID, year: int | None = None
) -> str:
    """Next sequential invoice number for the tenant: INV-YYYY-NNNN."""
    if year is None:
        year = datetime.now(timezone.utc).year
    result = await db.execute(
        select(Invoice.invoice_number).where(
            Invoice.tenant_id == tenant_id,
            Invoice.invoice_number.like(f"INV-{year}-%"),
        )
    )
    existing = [row[0] for row in result.all()]
    return next_invoice_number(existing, year)


@router.post("/invoices/generate", status_code=201)
async def generate_invoice(
    body: GenerateInvoiceRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> InvoiceResponse:
    """Generate an invoice from unbilled time entries and expenses for a matter. Admin only."""
    user = await require_finance_admin(request, db)

    # Verify matter
    matter_result = await db.execute(
        select(Matter).where(
            Matter.id == body.matter_id,
            Matter.tenant_id == user.tenant_id,
        )
    )
    matter = matter_result.scalar_one_or_none()
    if not matter:
        raise HTTPException(status_code=404, detail="Matter not found")

    # Gather unbilled time entries
    time_result = await db.execute(
        select(TimeEntry).where(
            TimeEntry.tenant_id == user.tenant_id,
            TimeEntry.matter_id == body.matter_id,
            TimeEntry.invoice_id.is_(None),
            TimeEntry.is_billable.is_(True),
            TimeEntry.status == "draft",
        )
    )
    time_entries = time_result.scalars().all()

    # Gather unbilled expenses
    exp_result = await db.execute(
        select(Expense).where(
            Expense.tenant_id == user.tenant_id,
            Expense.matter_id == body.matter_id,
            Expense.invoice_id.is_(None),
            Expense.is_billable.is_(True),
        )
    )
    expenses = exp_result.scalars().all()

    if not time_entries and not expenses:
        raise HTTPException(
            status_code=400,
            detail="No unbilled time entries or expenses found for this matter",
        )

    # Build line items
    line_items = []
    subtotal = Decimal("0")
    sort_order = 0

    for entry in time_entries:
        line_items.append(
            {
                "source_type": "time_entry",
                "source_id": str(entry.id),
                "description": f"{entry.description} ({entry.hours}h @ ${entry.hourly_rate}/hr)",
                "quantity": entry.hours,
                "unit_price": entry.hourly_rate,
                "amount": entry.amount,
                "sort_order": sort_order,
            }
        )
        subtotal += entry.amount
        sort_order += 1

    for exp in expenses:
        line_items.append(
            {
                "source_type": "expense",
                "source_id": str(exp.id),
                "description": f"{exp.description} (Category: {exp.category})",
                "quantity": Decimal("1"),
                "unit_price": exp.amount,
                "amount": exp.amount,
                "sort_order": sort_order,
            }
        )
        subtotal += exp.amount
        sort_order += 1

    # Calculate tax
    tax_rate = body.tax_rate or Decimal("0")
    tax_amount = (subtotal * tax_rate).quantize(Decimal("0.01"))
    total = subtotal + tax_amount

    # Create invoice as a draft — it must be reviewed and explicitly sent
    # (PATCH status → "sent") before it counts as outstanding A/R or syncs
    # to QuickBooks.
    issue_date = body.issue_date or date.today()
    due_date = issue_date + timedelta(days=body.due_date_days)
    invoice_number = await _next_invoice_number(db, user.tenant_id)

    billed_dates = [e.date for e in time_entries] + [x.date for x in expenses]

    invoice = Invoice(
        tenant_id=user.tenant_id,
        matter_id=uuid.UUID(body.matter_id),
        invoice_number=invoice_number,
        status="draft",
        issue_date=issue_date,
        due_date=due_date,
        subtotal=subtotal,
        tax_amount=tax_amount,
        total=total,
        notes=body.notes,
        payment_terms=body.payment_terms,
        billing_period_start=min(billed_dates) if billed_dates else None,
        billing_period_end=max(billed_dates) if billed_dates else None,
        created_by=user.id,
    )
    db.add(invoice)
    await db.flush()

    # Create line items and link sources
    for li_data in line_items:
        li = InvoiceLineItem(
            invoice_id=invoice.id,
            source_type=li_data["source_type"],
            source_id=uuid.UUID(li_data["source_id"]),
            description=li_data["description"],
            quantity=li_data["quantity"],
            unit_price=li_data["unit_price"],
            amount=li_data["amount"],
            sort_order=li_data["sort_order"],
        )
        db.add(li)

        # Link time entries to invoice
        if li_data["source_type"] == "time_entry":
            entry = next(
                (e for e in time_entries if str(e.id) == li_data["source_id"]), None
            )
            if entry:
                entry.invoice_id = invoice.id
                entry.status = "invoiced"

        # Link expenses to invoice
        if li_data["source_type"] == "expense":
            exp = next((e for e in expenses if str(e.id) == li_data["source_id"]), None)
            if exp:
                exp.invoice_id = invoice.id

    try:
        await db.commit()
    except IntegrityError:
        # Lost a race for the next sequential invoice number — rare enough
        # that asking the caller to retry beats holding a tenant-wide lock.
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Invoice number conflict — another invoice was generated concurrently. Please retry.",
        )
    await db.refresh(invoice)

    # Reload with relationships
    return await _load_invoice_response(db, invoice.id, user.tenant_id)


async def _load_invoice_response(
    db: AsyncSession, invoice_id: uuid.UUID, tenant_id: uuid.UUID
) -> InvoiceResponse:
    """Reload invoice with line items and payments."""
    result = await db.execute(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.tenant_id == tenant_id,
        )
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Eager load relationships
    li_result = await db.execute(
        select(InvoiceLineItem)
        .where(InvoiceLineItem.invoice_id == invoice.id)
        .order_by(InvoiceLineItem.sort_order)
    )
    line_items = li_result.scalars().all()

    pay_result = await db.execute(
        select(Payment)
        .where(Payment.invoice_id == invoice.id)
        .order_by(Payment.payment_date)
    )
    payments = pay_result.scalars().all()

    matter_result = await db.execute(
        select(Matter.matter_name).where(Matter.id == invoice.matter_id)
    )
    matter_name = matter_result.scalar_one_or_none()

    amount_paid = sum((p.amount for p in payments), Decimal("0"))

    return InvoiceResponse(
        id=str(invoice.id),
        tenant_id=str(invoice.tenant_id),
        matter_id=str(invoice.matter_id),
        invoice_number=invoice.invoice_number,
        status=invoice.status,
        issue_date=invoice.issue_date,
        due_date=invoice.due_date,
        subtotal=invoice.subtotal,
        tax_amount=invoice.tax_amount,
        total=invoice.total,
        notes=invoice.notes,
        payment_terms=invoice.payment_terms,
        stripe_payment_link=invoice.stripe_payment_link,
        qbo_invoice_id=invoice.qbo_invoice_id,
        qbo_sync_status=invoice.qbo_sync_status,
        ledes_exported_at=invoice.ledes_exported_at,
        retainer_id=str(invoice.retainer_id) if invoice.retainer_id else None,
        billing_period_start=invoice.billing_period_start,
        billing_period_end=invoice.billing_period_end,
        sent_at=invoice.sent_at,
        amount_paid=amount_paid,
        balance_due=invoice.total - amount_paid,
        is_overdue=is_invoice_overdue(invoice.status, invoice.due_date),
        matter_name=matter_name,
        created_by=str(invoice.created_by),
        line_items=[
            InvoiceLineItemResponse.model_validate(li, from_attributes=True)
            for li in line_items
        ],
        payments=[
            PaymentResponse.model_validate(p, from_attributes=True) for p in payments
        ],
        created_at=invoice.created_at,
        updated_at=invoice.updated_at,
    )


@router.get("/invoices")
async def list_invoices(
    matter_id: str | None = Query(None),
    status: str | None = Query(None),
    overdue_only: bool = Query(False),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
) -> InvoiceListResponse:
    """List invoices with optional filters, including paid/balance amounts."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    stmt = select(Invoice, Matter.matter_name).join(
        Matter, Matter.id == Invoice.matter_id
    ).where(Invoice.tenant_id == user.tenant_id)
    if matter_id:
        stmt = stmt.where(Invoice.matter_id == matter_id)
    if status:
        stmt = stmt.where(Invoice.status == status)

    stmt = stmt.order_by(Invoice.created_at.desc())

    result = await db.execute(stmt)
    rows = result.all()

    # One grouped query for payment totals instead of N per-invoice queries
    paid_result = await db.execute(
        select(Payment.invoice_id, func.sum(Payment.amount))
        .where(Payment.tenant_id == user.tenant_id)
        .group_by(Payment.invoice_id)
    )
    paid_by_invoice = {row[0]: row[1] for row in paid_result.all()}

    items = []
    total_amount = Decimal("0")
    for inv, matter_name in rows:
        overdue = is_invoice_overdue(inv.status, inv.due_date)
        if overdue_only and not overdue:
            continue
        amount_paid = paid_by_invoice.get(inv.id, Decimal("0"))
        total_amount += inv.total
        items.append(
            InvoiceResponse(
                id=str(inv.id),
                tenant_id=str(inv.tenant_id),
                matter_id=str(inv.matter_id),
                invoice_number=inv.invoice_number,
                status=inv.status,
                issue_date=inv.issue_date,
                due_date=inv.due_date,
                subtotal=inv.subtotal,
                tax_amount=inv.tax_amount,
                total=inv.total,
                notes=inv.notes,
                payment_terms=inv.payment_terms,
                stripe_payment_link=inv.stripe_payment_link,
                qbo_invoice_id=inv.qbo_invoice_id,
                qbo_sync_status=inv.qbo_sync_status,
                ledes_exported_at=inv.ledes_exported_at,
                retainer_id=str(inv.retainer_id) if inv.retainer_id else None,
                billing_period_start=inv.billing_period_start,
                billing_period_end=inv.billing_period_end,
                sent_at=inv.sent_at,
                amount_paid=amount_paid,
                balance_due=inv.total - amount_paid,
                is_overdue=overdue,
                matter_name=matter_name,
                created_by=str(inv.created_by),
                line_items=[],
                payments=[],
                created_at=inv.created_at,
                updated_at=inv.updated_at,
            )
        )

    return InvoiceListResponse(
        items=items,
        total=len(items),
        total_amount=total_amount,
    )


@router.get("/invoices/{invoice_id}")
async def get_invoice(
    invoice_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> InvoiceResponse:
    """Get a single invoice by ID with line items and payments."""
    user = await get_current_user(request, db)
    return await _load_invoice_response(db, uuid.UUID(invoice_id), user.tenant_id)


async def _trigger_qbo_sync_invoice(invoice_id: str, tenant_id: str):
    """Fire-and-forget QBO invoice sync with retry.

    Resolves a fresh (auto-refreshed) access token inside the task's own
    session — the tokens are stored Fernet-encrypted, so they must go through
    the token vault rather than being read off the integration row.
    """
    from app.routers.qbo import _get_fresh_qbo_token, _get_qbo_integration
    from app.services.qbo_sync import QBOSyncService

    async with async_session_maker() as session:
        access_token = await _get_fresh_qbo_token(session, tenant_id)
        if not access_token:
            logger.warning(
                f"QBO invoice sync skipped for {invoice_id}: no valid token"
            )
            return
        qbo = await _get_qbo_integration(session, tenant_id)
        sandbox = qbo.sandbox_mode if qbo else True
        service = QBOSyncService(session, tenant_id, access_token, sandbox)
        await service.sync_invoice_with_retry(invoice_id)


async def _trigger_qbo_sync_payment(payment_id: str, tenant_id: str):
    """Fire-and-forget QBO payment sync with retry (fresh token, own session)."""
    from app.routers.qbo import _get_fresh_qbo_token, _get_qbo_integration
    from app.services.qbo_sync import QBOSyncService

    async with async_session_maker() as session:
        access_token = await _get_fresh_qbo_token(session, tenant_id)
        if not access_token:
            logger.warning(
                f"QBO payment sync skipped for {payment_id}: no valid token"
            )
            return
        qbo = await _get_qbo_integration(session, tenant_id)
        sandbox = qbo.sandbox_mode if qbo else True
        service = QBOSyncService(session, tenant_id, access_token, sandbox)
        await service.sync_payment_with_retry(payment_id)


@router.patch("/invoices/{invoice_id}")
async def update_invoice(
    invoice_id: str,
    body: InvoiceUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> InvoiceResponse:
    """Update invoice fields or transition status. Admin only."""
    user = await require_finance_admin(request, db)

    result = await db.execute(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.tenant_id == user.tenant_id,
        )
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    old_status = invoice.status
    update_data = body.model_dump(exclude_unset=True)
    new_status = update_data.get("status", old_status)

    if new_status != old_status:
        if not can_transition_invoice(old_status, new_status):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot change invoice status from '{old_status}' to '{new_status}'",
            )
        if new_status == "void":
            paid_result = await db.execute(
                select(func.count(Payment.id)).where(
                    Payment.invoice_id == invoice.id
                )
            )
            if (paid_result.scalar() or 0) > 0:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot void an invoice with recorded payments",
                )
        if new_status == "sent" and invoice.sent_at is None:
            invoice.sent_at = datetime.now(timezone.utc)

    for key, value in update_data.items():
        setattr(invoice, key, value)

    # Voiding an invoice releases its time entries and expenses back to the
    # unbilled pool so they can be corrected and re-invoiced.
    if new_status == "void" and old_status != "void":
        time_result = await db.execute(
            select(TimeEntry).where(TimeEntry.invoice_id == invoice.id)
        )
        for entry in time_result.scalars().all():
            entry.invoice_id = None
            entry.status = "draft"
        exp_result = await db.execute(
            select(Expense).where(Expense.invoice_id == invoice.id)
        )
        for exp in exp_result.scalars().all():
            exp.invoice_id = None

    await db.commit()

    # Trigger QBO sync on status transitions that need it
    if old_status != new_status and new_status in ("sent", "paid", "partially_paid"):
        try:
            from app.models.qbo import QBOIntegration

            qbo_result = await db.execute(
                select(QBOIntegration).where(
                    QBOIntegration.tenant_id == user.tenant_id,
                    QBOIntegration.is_active,
                )
            )
            qbo = qbo_result.scalar_one_or_none()
            if qbo:
                invoice.qbo_sync_status = "syncing"
                await db.commit()
                asyncio.create_task(
                    _trigger_qbo_sync_invoice(invoice_id, str(user.tenant_id))
                )
        except Exception:
            logger.warning("QBO invoice sync task failed", exc_info=True)

    return await _load_invoice_response(db, uuid.UUID(invoice_id), user.tenant_id)


# ── Payments ────────────────────────────────────────────────────────────────


@router.post("/payments", status_code=201)
async def create_payment(
    body: PaymentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PaymentResponse:
    """Record a payment against an invoice. Admin only."""
    user = await require_finance_admin(request, db)

    # Verify invoice exists and belongs to tenant
    inv_result = await db.execute(
        select(Invoice).where(
            Invoice.id == body.invoice_id,
            Invoice.tenant_id == user.tenant_id,
        )
    )
    invoice = inv_result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status in ("draft", "void", "written_off"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot record a payment against a '{invoice.status}' invoice. Send it first.",
        )

    payment = Payment(
        tenant_id=user.tenant_id,
        invoice_id=uuid.UUID(body.invoice_id),
        amount=body.amount,
        payment_date=body.payment_date,
        method=body.method,
        reference_number=body.reference_number,
        notes=body.notes,
    )
    db.add(payment)

    # Update invoice payment status
    total_paid_result = await db.execute(
        select(func.sum(Payment.amount)).where(
            Payment.invoice_id == body.invoice_id,
        )
    )
    total_paid = total_paid_result.scalar() or Decimal("0")
    total_paid += body.amount

    if total_paid >= invoice.total:
        invoice.status = "paid"
    elif total_paid > 0:
        invoice.status = "partially_paid"

    if invoice.qbo_sync_status == "synced":
        invoice.qbo_sync_status = "pending"  # Re-sync needed

    await db.commit()
    await db.refresh(payment)

    # Trigger QBO payment sync (fire-and-forget)
    payment_id_str = str(payment.id)
    tenant_id_str = str(user.tenant_id)
    try:
        from app.models.qbo import QBOIntegration

        qbo_result = await db.execute(
            select(QBOIntegration).where(
                QBOIntegration.tenant_id == user.tenant_id,
                QBOIntegration.is_active,
            )
        )
        qbo = qbo_result.scalar_one_or_none()
        if qbo:
            asyncio.create_task(
                _trigger_qbo_sync_payment(payment_id_str, tenant_id_str)
            )
    except Exception:
        logger.warning("QBO payment sync task failed", exc_info=True)

    return PaymentResponse.model_validate(payment, from_attributes=True)


@router.get("/payments")
async def list_payments(
    invoice_id: str | None = Query(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
) -> list[PaymentResponse]:
    """List payments with optional invoice filter."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    stmt = select(Payment).where(Payment.tenant_id == user.tenant_id)
    if invoice_id:
        stmt = stmt.where(Payment.invoice_id == invoice_id)

    stmt = stmt.order_by(Payment.payment_date.desc())
    result = await db.execute(stmt)
    payments = result.scalars().all()

    return [PaymentResponse.model_validate(p, from_attributes=True) for p in payments]


# ── Stripe Payment Link ─────────────────────────────────────────────────────


@router.post("/invoices/{invoice_id}/payment-link")
async def create_stripe_payment_link(
    invoice_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> StripePaymentLinkResponse:
    """Generate a Stripe Payment Link for an invoice."""
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe not configured")

    import stripe

    stripe.api_key = settings.STRIPE_SECRET_KEY

    user = await get_current_user(request, db)
    inv = await _load_invoice_response(db, uuid.UUID(invoice_id), user.tenant_id)

    if inv.status not in ("sent", "partially_paid"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot create payment link for invoice in '{inv.status}' status. Send the invoice first.",
        )

    # Get the invoice from DB to update
    result = await db.execute(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.tenant_id == user.tenant_id,
        )
    )
    invoice = result.scalar_one_or_none()

    try:
        price = stripe.Price.create(
            unit_amount=int(invoice.total * 100),  # cents
            currency="usd",
            product_data={
                "name": f"Invoice {invoice.invoice_number}",
                "description": f"Legal services — Matter: {inv.matter_id}",
            },
        )

        payment_link = stripe.PaymentLink.create(
            line_items=[{"price": price.id, "quantity": 1}],
            metadata={
                "invoice_id": invoice_id,
                "invoice_number": invoice.invoice_number,
                "tenant_id": str(user.tenant_id),
            },
        )

        invoice.stripe_payment_link = payment_link.url
        invoice.stripe_payment_link_id = payment_link.id
        await db.commit()

        return StripePaymentLinkResponse(
            invoice_id=inv.id,
            payment_link_url=payment_link.url,
            payment_link_id=payment_link.id,
        )
    except stripe.StripeError as exc:
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc}")


# ── Invoice Export ──────────────────────────────────────────────────────────


@router.post("/invoices/{invoice_id}/export")
async def export_invoice(
    invoice_id: str,
    body: InvoiceExportRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Export an invoice in the requested format (csv, ledes1998b). Admin only."""
    user = await require_finance_admin(request, db)
    inv = await _load_invoice_response(db, uuid.UUID(invoice_id), user.tenant_id)

    if body.format == "csv":
        import csv
        from io import StringIO

        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Invoice Number",
                "Issue Date",
                "Due Date",
                "Status",
                "Description",
                "Quantity",
                "Unit Price",
                "Amount",
                "Subtotal",
                "Tax",
                "Total",
                "Payment Terms",
            ]
        )
        for li in inv.line_items:
            writer.writerow(
                [
                    inv.invoice_number,
                    inv.issue_date,
                    inv.due_date,
                    inv.status,
                    li.description,
                    li.quantity,
                    li.unit_price,
                    li.amount,
                    inv.subtotal if li.sort_order == 0 else "",
                    inv.tax_amount if li.sort_order == 0 else "",
                    inv.total if li.sort_order == 0 else "",
                    inv.payment_terms if li.sort_order == 0 else "",
                ]
            )
        return {"format": "csv", "data": output.getvalue()}

    elif body.format == "ledes1998b":
        from app.services.ledes_export import export_ledes_1998b

        ledes_data = export_ledes_1998b(inv)
        return {"format": "ledes1998b", "data": ledes_data}

    elif body.format == "pdf":
        from app.services.invoice_pdf import generate_invoice_pdf
        from fastapi.responses import Response

        pdf_bytes = generate_invoice_pdf(inv)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=invoice_{inv.invoice_number}.pdf"
            },
        )

    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported export format: {body.format}. Use 'csv', 'pdf', or 'ledes1998b'.",
        )


# ── Billing Settings ────────────────────────────────────────────────────────


@router.get("/settings")
async def get_billing_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> BillingSettingsResponse:
    """Return tenant-level billing defaults (rate, timer rounding). Admin only."""
    user = await require_finance_admin(request, db)
    billing_cfg = await _get_billing_config(db, user.tenant_id)
    rate = billing_cfg.get("default_hourly_rate")
    return BillingSettingsResponse(
        default_hourly_rate=Decimal(str(rate)) if rate else None,
        time_rounding_minutes=int(
            billing_cfg.get("time_rounding_minutes") or DEFAULT_ROUNDING_MINUTES
        ),
    )


@router.put("/settings")
async def update_billing_settings(
    body: BillingSettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> BillingSettingsResponse:
    """Update tenant-level billing defaults. Admin only."""
    user = await require_finance_admin(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    ts_result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == user.tenant_id)
    )
    ts = ts_result.scalar_one_or_none()
    if not ts:
        ts = TenantSettings(tenant_id=user.tenant_id, custom_config={})
        db.add(ts)

    # Reassign (rather than mutate) the JSON column so SQLAlchemy detects the change
    custom_config = dict(ts.custom_config or {})
    billing_cfg = dict(custom_config.get("billing", {}) or {})
    if body.default_hourly_rate is not None:
        billing_cfg["default_hourly_rate"] = str(body.default_hourly_rate)
    if body.time_rounding_minutes is not None:
        billing_cfg["time_rounding_minutes"] = body.time_rounding_minutes
    custom_config["billing"] = billing_cfg
    ts.custom_config = custom_config

    await db.commit()

    rate = billing_cfg.get("default_hourly_rate")
    return BillingSettingsResponse(
        default_hourly_rate=Decimal(str(rate)) if rate else None,
        time_rounding_minutes=int(
            billing_cfg.get("time_rounding_minutes") or DEFAULT_ROUNDING_MINUTES
        ),
    )


# ── Stripe Webhook ─────────────────────────────────────────────────────────


@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle Stripe webhook events for payment reconciliation.

    Events handled:
      - payment_intent.succeeded → auto-create Payment + update invoice status
      - payment_intent.payment_failed → log warning
      - checkout.session.completed → reconcile Payment Link checkout
    """
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=501, detail="Stripe webhook not configured")

    import stripe

    stripe.api_key = settings.STRIPE_SECRET_KEY

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing stripe-signature header")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")

    event_type = event["type"]
    event_data = event["data"]["object"]
    logger.info(f"Stripe webhook received: {event_type} (id={event['id']})")

    try:
        if event_type == "payment_intent.succeeded":
            await _handle_payment_intent_succeeded(db, event_data)
        elif event_type == "payment_intent.payment_failed":
            await _handle_payment_intent_failed(db, event_data)
        elif event_type == "checkout.session.completed":
            await _handle_checkout_session_completed(db, event_data)
        else:
            logger.debug(f"Unhandled Stripe event type: {event_type}")
    except Exception as exc:
        logger.exception(f"Stripe webhook handler failed for {event_type}: {exc}")
        # Still return 200 to Stripe — we logged the error for investigation
        return {"status": "received", "warning": str(exc)}

    return {"status": "received"}


async def _handle_payment_intent_succeeded(db: AsyncSession, intent: dict):
    """Handle payment_intent.succeeded: create Payment + update invoice status."""
    stripe_payment_intent_id = intent["id"]
    metadata = intent.get("metadata", {})
    invoice_id = metadata.get("invoice_id")

    # Idempotency check: skip if this payment intent was already recorded
    existing = await db.execute(
        select(Payment).where(
            Payment.stripe_payment_intent_id == stripe_payment_intent_id
        )
    )
    if existing.scalar_one_or_none():
        logger.info(
            f"Duplicate Stripe event: {stripe_payment_intent_id} already recorded"
        )
        return

    amount = Decimal(str(intent["amount"] / 100))  # Convert cents to dollars
    payment_date = date.today()
    method = "stripe"

    if not invoice_id:
        # No invoice metadata — create an unlinked payment for tracking
        logger.warning(
            f"payment_intent.succeeded without invoice_id metadata: {stripe_payment_intent_id}"
        )
        return

    # Verify invoice exists
    inv_result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    invoice = inv_result.scalar_one_or_none()
    if not invoice:
        logger.warning(
            f"Stripe webhook: invoice {invoice_id} not found for PI {stripe_payment_intent_id}"
        )
        return

    # Create payment
    payment = Payment(
        tenant_id=invoice.tenant_id,
        invoice_id=uuid.UUID(invoice_id),
        amount=amount,
        payment_date=payment_date,
        method=method,
        reference_number=intent.get("id"),
        stripe_payment_intent_id=stripe_payment_intent_id,
        notes=f"Auto-reconciled from Stripe intent {stripe_payment_intent_id}",
    )
    db.add(payment)

    # Update invoice status
    total_paid_result = await db.execute(
        select(func.sum(Payment.amount)).where(
            Payment.invoice_id == invoice_id,
        )
    )
    total_paid = (total_paid_result.scalar() or Decimal("0")) + amount

    if total_paid >= invoice.total:
        invoice.status = "paid"
    elif total_paid > 0:
        invoice.status = "partially_paid"

    if invoice.qbo_sync_status == "synced":
        invoice.qbo_sync_status = "pending"

    await db.commit()
    logger.info(
        f"Auto-reconciled payment for invoice {invoice_id}: ${amount} via Stripe"
    )


async def _handle_payment_intent_failed(db: AsyncSession, intent: dict):
    """Handle payment_intent.payment_failed: log the failure."""
    metadata = intent.get("metadata", {})
    invoice_id = metadata.get("invoice_id")
    error_msg = (
        intent.get("last_payment_error", {}).get("message", "Unknown failure")
        if intent.get("last_payment_error")
        else "Payment failed"
    )
    logger.warning(
        f"Stripe payment failed for invoice {invoice_id}: {error_msg} "
        f"(PI: {intent['id']})"
    )

    # Optionally mark invoice for follow-up
    if invoice_id:
        inv_result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
        invoice = inv_result.scalar_one_or_none()
        if invoice and invoice.status not in ("paid", "written_off"):
            # Add a note to the invoice about the failed payment
            fail_note = f"[{datetime.now(timezone.utc).date()}] Stripe payment failed: {error_msg[:200]}"
            if invoice.notes:
                invoice.notes = f"{invoice.notes}\n{fail_note}"
            else:
                invoice.notes = fail_note
            await db.commit()


async def _handle_checkout_session_completed(db: AsyncSession, session: dict):
    """Handle checkout.session.completed: reconcile Payment Link checkout."""
    metadata = session.get("metadata", {})
    invoice_id = metadata.get("invoice_id")
    payment_intent_id = session.get("payment_intent")

    if not invoice_id:
        logger.debug("checkout.session.completed without invoice_id metadata")
        return

    # If we have a payment intent, delegate to the payment_intent handler
    if payment_intent_id:
        # Check if already processed
        existing = await db.execute(
            select(Payment).where(Payment.stripe_payment_intent_id == payment_intent_id)
        )
        if existing.scalar_one_or_none():
            logger.info(f"Checkout session already reconciled: PI {payment_intent_id}")
            return

        # Fetch the payment intent from Stripe for full details
        import stripe

        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            await _handle_payment_intent_succeeded(db, intent)
        except stripe.StripeError as exc:
            logger.error(
                f"Failed to retrieve payment intent {payment_intent_id}: {exc}"
            )

    logger.info(f"Checkout session completed for invoice {invoice_id}")
