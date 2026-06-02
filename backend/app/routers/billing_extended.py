"""Extended billing router — time entries, expenses, invoice generation, payments."""

import logging
import uuid
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.billing import TimeEntry, Expense, Invoice, InvoiceLineItem, Payment
from app.models.plugin import Matter
from app.schemas.billing import (
    TimeEntryCreate,
    TimeEntryUpdate,
    TimeEntryResponse,
    TimeEntryListResponse,
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
)

settings = get_settings()
router = APIRouter(prefix="/api/billing", tags=["billing"])
logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _tenant_filter(tenant_id: uuid.UUID):
    """Return filter kwargs for tenant-scoped queries."""
    return {"tenant_id": tenant_id}


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
    matter = await db.execute(
        select(Matter).where(
            Matter.id == body.matter_id,
            Matter.tenant_id == user.tenant_id,
        )
    )
    if not matter.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Matter not found")

    amount = body.hours * body.hourly_rate
    entry = TimeEntry(
        tenant_id=user.tenant_id,
        matter_id=uuid.UUID(body.matter_id),
        user_id=user.id,
        description=body.description,
        hours=body.hours,
        hourly_rate=body.hourly_rate,
        amount=amount,
        date=body.date,
        is_billable=body.is_billable,
        utbms_task_code=body.utbms_task_code,
        utbms_activity_code=body.utbms_activity_code,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    return TimeEntryResponse.model_validate(entry)


@router.get("/time-entries")
async def list_time_entries(
    matter_id: str | None = Query(None),
    status: str | None = Query(None),
    unbilled_only: bool = Query(False),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
) -> TimeEntryListResponse:
    """List time entries with optional filters."""
    user = await get_current_user(request, db)

    stmt = select(TimeEntry).where(TimeEntry.tenant_id == user.tenant_id)

    if matter_id:
        stmt = stmt.where(TimeEntry.matter_id == matter_id)
    if status:
        stmt = stmt.where(TimeEntry.status == status)
    if unbilled_only:
        stmt = stmt.where(TimeEntry.invoice_id.is_(None))

    stmt = stmt.order_by(TimeEntry.date.desc(), TimeEntry.created_at.desc())

    result = await db.execute(stmt)
    entries = result.scalars().all()

    total_hours = sum((e.hours for e in entries), Decimal("0"))
    total_amount = sum((e.amount for e in entries), Decimal("0"))

    return TimeEntryListResponse(
        items=[TimeEntryResponse.model_validate(e) for e in entries],
        total=len(entries),
        total_hours=total_hours,
        total_amount=total_amount,
    )


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

    return TimeEntryResponse.model_validate(entry)


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

    update_data = body.model_dump(exclude_unset=True)
    if "hours" in update_data or "hourly_rate" in update_data:
        hours = update_data.get("hours", entry.hours)
        rate = update_data.get("hourly_rate", entry.hourly_rate)
        update_data["amount"] = hours * rate

    for key, value in update_data.items():
        setattr(entry, key, value)

    await db.commit()
    await db.refresh(entry)
    return TimeEntryResponse.model_validate(entry)


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

    entry.status = "written_off"
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

    return ExpenseResponse.model_validate(expense)


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
        items=[ExpenseResponse.model_validate(e) for e in expenses],
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

    return ExpenseResponse.model_validate(expense)


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
    return ExpenseResponse.model_validate(expense)


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


def _next_invoice_number(tenant_id: str, year: int | None = None) -> str:
    """Generate next invoice number: INV-YYYY-NNNN."""
    if year is None:
        year = datetime.now(timezone.utc).year
    # Simple sequential — in production, use a DB sequence or counter table
    suffix = uuid.uuid4().hex[:6].upper()
    return f"INV-{year}-{suffix}"


@router.post("/invoices/generate", status_code=201)
async def generate_invoice(
    body: GenerateInvoiceRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> InvoiceResponse:
    """Generate an invoice from unbilled time entries and expenses for a matter."""
    user = await get_current_user(request, db)
    tenant_id = str(user.tenant_id)

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
            TimeEntry.is_billable == True,
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
            Expense.is_billable == True,
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

    # Create invoice
    issue_date = body.issue_date or date.today()
    due_date = issue_date + timedelta(days=body.due_date_days)
    invoice_number = _next_invoice_number(tenant_id)

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
                entry.status = "billed"

        # Link expenses to invoice
        if li_data["source_type"] == "expense":
            exp = next((e for e in expenses if str(e.id) == li_data["source_id"]), None)
            if exp:
                exp.invoice_id = invoice.id

    await db.commit()
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
        created_by=str(invoice.created_by),
        line_items=[InvoiceLineItemResponse.model_validate(li) for li in line_items],
        payments=[PaymentResponse.model_validate(p) for p in payments],
        created_at=invoice.created_at,
        updated_at=invoice.updated_at,
    )


@router.get("/invoices")
async def list_invoices(
    matter_id: str | None = Query(None),
    status: str | None = Query(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
) -> InvoiceListResponse:
    """List invoices with optional filters."""
    user = await get_current_user(request, db)

    stmt = select(Invoice).where(Invoice.tenant_id == user.tenant_id)
    if matter_id:
        stmt = stmt.where(Invoice.matter_id == matter_id)
    if status:
        stmt = stmt.where(Invoice.status == status)

    stmt = stmt.order_by(Invoice.created_at.desc())

    result = await db.execute(stmt)
    invoices = result.scalars().all()

    items = []
    total_amount = Decimal("0")
    for inv in invoices:
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


@router.patch("/invoices/{invoice_id}")
async def update_invoice(
    invoice_id: str,
    body: InvoiceUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> InvoiceResponse:
    """Update invoice fields or transition status."""
    user = await get_current_user(request, db)

    result = await db.execute(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.tenant_id == user.tenant_id,
        )
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(invoice, key, value)

    await db.commit()
    return await _load_invoice_response(db, uuid.UUID(invoice_id), user.tenant_id)


# ── Payments ────────────────────────────────────────────────────────────────


@router.post("/payments", status_code=201)
async def create_payment(
    body: PaymentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PaymentResponse:
    """Record a payment against an invoice."""
    user = await get_current_user(request, db)

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

    return PaymentResponse.model_validate(payment)


@router.get("/payments")
async def list_payments(
    invoice_id: str | None = Query(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
) -> list[PaymentResponse]:
    """List payments with optional invoice filter."""
    user = await get_current_user(request, db)

    stmt = select(Payment).where(Payment.tenant_id == user.tenant_id)
    if invoice_id:
        stmt = stmt.where(Payment.invoice_id == invoice_id)

    stmt = stmt.order_by(Payment.payment_date.desc())
    result = await db.execute(stmt)
    payments = result.scalars().all()

    return [PaymentResponse.model_validate(p) for p in payments]


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

    if inv.status not in ("draft", "sent"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot create payment link for invoice in '{inv.status}' status",
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
    """Export an invoice in the requested format (csv, ledes1998b)."""
    user = await get_current_user(request, db)
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
