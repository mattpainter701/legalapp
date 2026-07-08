"""Billing workflow helpers — pure logic shared by billing routes and services.

Keeps timer rounding, invoice numbering, and invoice status transitions in one
testable place so the routers stay thin.
"""

from datetime import date
from decimal import Decimal, ROUND_UP

# Default rounding increment for timer-captured time: 6 minutes = 0.1h,
# the standard legal-billing increment.
DEFAULT_ROUNDING_MINUTES = 6

# Invoice lifecycle. "overdue" is intentionally NOT a stored status — it is
# derived from due_date at read time (see is_invoice_overdue) so invoices
# never need a scheduled job to flip state.
INVOICE_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"sent", "void"},
    "sent": {"paid", "partially_paid", "void", "written_off"},
    "partially_paid": {"paid", "written_off"},
    "paid": set(),
    "void": set(),
    "written_off": set(),
}


def can_transition_invoice(old_status: str, new_status: str) -> bool:
    """Whether an invoice may move from old_status to new_status."""
    if old_status == new_status:
        return True
    return new_status in INVOICE_STATUS_TRANSITIONS.get(old_status, set())


def is_invoice_overdue(status: str, due_date: date, today: date | None = None) -> bool:
    """An invoice is overdue when unpaid past its due date."""
    if status not in ("sent", "partially_paid"):
        return False
    return (today or date.today()) > due_date


def round_timer_hours(elapsed_seconds: float, rounding_minutes: int | None) -> Decimal:
    """Round elapsed timer seconds UP to the billing increment, in hours.

    A stopped timer always bills at least one increment (industry convention:
    starting the clock is a billable event). rounding_minutes <= 0 or None
    falls back to the 6-minute default.
    """
    if not rounding_minutes or rounding_minutes <= 0:
        rounding_minutes = DEFAULT_ROUNDING_MINUTES
    increment_hours = Decimal(rounding_minutes) / Decimal(60)
    elapsed_hours = Decimal(str(max(elapsed_seconds, 0))) / Decimal(3600)
    increments = (elapsed_hours / increment_hours).to_integral_value(rounding=ROUND_UP)
    if increments < 1:
        increments = Decimal(1)
    return (increments * increment_hours).quantize(Decimal("0.01"))


def next_invoice_number(existing_numbers: list[str], year: int) -> str:
    """Next sequential invoice number for a tenant: INV-YYYY-NNNN.

    Scans existing numbers for the given year and returns the next sequence.
    Numbers that don't match the INV-YYYY-NNNN pattern (e.g. legacy random
    suffixes) are ignored rather than breaking the sequence.
    """
    prefix = f"INV-{year}-"
    max_seq = 0
    for number in existing_numbers:
        if not number or not number.startswith(prefix):
            continue
        suffix = number[len(prefix):]
        if suffix.isdigit():
            max_seq = max(max_seq, int(suffix))
    return f"{prefix}{max_seq + 1:04d}"
