"""Recurring billing service — auto-generates invoices for matters on billing cycles."""

import logging
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select, func

from app.database import async_session_maker
from app.models.billing import TimeEntry, Expense, Invoice, InvoiceLineItem
from app.models.plugin import Matter

logger = logging.getLogger(__name__)

CYCLE_DAYS = {
    "monthly": 30,
    "quarterly": 90,
}


async def generate_recurring_invoices() -> dict:
    """Check for matters past their billing cycle and auto-generate invoices.

    Called by the scheduler. Only matters with billing_cycle in
    ("monthly", "quarterly") are considered. An invoice is generated if:
      - The matter has unbilled time entries or expenses
      - The matter's last invoice was issued more than <cycle_days> ago
      - The matter's billing_method is "hourly" (flat-fee handled manually)
    """
    today = date.today()
    generated = 0
    skipped = 0
    errors = 0

    async with async_session_maker() as db:
        # Find matters with recurring billing cycles
        result = await db.execute(
            select(Matter).where(
                Matter.billing_cycle.in_(["monthly", "quarterly"]),
                Matter.billing_method == "hourly",
                Matter.is_closed.is_(False),
                Matter.status.in_(["active", "threatened"]),
            )
        )
        matters = result.scalars().all()

        for matter in matters:
            try:
                # Check last invoice date for this matter
                last_inv = await db.execute(
                    select(func.max(Invoice.issue_date)).where(
                        Invoice.matter_id == matter.id,
                        Invoice.tenant_id == matter.tenant_id,
                    )
                )
                last_date = last_inv.scalar()
                last_date = (
                    last_date.date()
                    if hasattr(last_date, "date")
                    else (last_date if isinstance(last_date, date) else None)
                )

                cycle_days = CYCLE_DAYS.get(matter.billing_cycle, 30)
                cutoff = today - timedelta(days=cycle_days)

                if last_date and last_date > cutoff:
                    # Not yet due for next invoice
                    continue

                # Gather unbilled time entries
                time_result = await db.execute(
                    select(TimeEntry).where(
                        TimeEntry.tenant_id == matter.tenant_id,
                        TimeEntry.matter_id == matter.id,
                        TimeEntry.invoice_id.is_(None),
                        TimeEntry.is_billable.is_(True),
                        TimeEntry.status == "draft",
                    )
                )
                time_entries = time_result.scalars().all()

                # Gather unbilled expenses
                exp_result = await db.execute(
                    select(Expense).where(
                        Expense.tenant_id == matter.tenant_id,
                        Expense.matter_id == matter.id,
                        Expense.invoice_id.is_(None),
                        Expense.is_billable.is_(True),
                    )
                )
                expenses = exp_result.scalars().all()

                if not time_entries and not expenses:
                    skipped += 1
                    continue

                # Generate invoice number
                invoice_number = _make_invoice_number(
                    matter.tenant_id, today, generated + 1
                )

                # Calculate billing period
                period_start = last_date or (today - timedelta(days=cycle_days))
                period_end = today

                # Build line items
                line_items = []
                subtotal = Decimal("0")
                sort_order = 0

                for entry in time_entries:
                    line_items.append(
                        InvoiceLineItem(
                            source_type="time_entry",
                            source_id=entry.id,
                            description=(
                                f"{entry.description} "
                                f"({entry.hours}h @ ${entry.hourly_rate}/hr)"
                            ),
                            quantity=entry.hours,
                            unit_price=entry.hourly_rate,
                            amount=entry.amount,
                            sort_order=sort_order,
                        )
                    )
                    subtotal += entry.amount
                    sort_order += 1

                for exp in expenses:
                    line_items.append(
                        InvoiceLineItem(
                            source_type="expense",
                            source_id=exp.id,
                            description=f"{exp.description} (Category: {exp.category})",
                            quantity=Decimal("1"),
                            unit_price=exp.amount,
                            amount=exp.amount,
                            sort_order=sort_order,
                        )
                    )
                    subtotal += exp.amount
                    sort_order += 1

                tax_rate = matter.tax_rate or Decimal("0")
                tax_amount = (subtotal * tax_rate).quantize(Decimal("0.01"))
                total = subtotal + tax_amount

                due_days = 30
                invoice = Invoice(
                    tenant_id=matter.tenant_id,
                    matter_id=matter.id,
                    invoice_number=invoice_number,
                    status="draft",
                    issue_date=period_end,
                    due_date=period_end + timedelta(days=due_days),
                    subtotal=subtotal,
                    tax_amount=tax_amount,
                    total=total,
                    billing_period_start=period_start,
                    billing_period_end=period_end,
                    created_by=matter.user_id,
                )
                db.add(invoice)
                await db.flush()

                # Attach line items
                for li in line_items:
                    li.invoice_id = invoice.id
                    db.add(li)

                # Link time entries and expenses to invoice
                for entry in time_entries:
                    entry.invoice_id = invoice.id
                    entry.status = "billed"
                for exp in expenses:
                    exp.invoice_id = invoice.id

                generated += 1
                logger.info(
                    "Auto-generated invoice %s for matter %s ($%s)",
                    invoice_number,
                    matter.matter_name,
                    total,
                )

            except Exception:
                logger.exception("Failed to generate invoice for matter %s", matter.id)
                errors += 1

        await db.commit()

    return {"generated": generated, "skipped": skipped, "errors": errors}


def _make_invoice_number(tenant_id: uuid.UUID, today: date, seq: int) -> str:
    """Generate a unique invoice number like INV-2026-00042."""
    return f"INV-{today.year}-{seq:05d}"
