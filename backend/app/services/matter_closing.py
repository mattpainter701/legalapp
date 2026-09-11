"""What a matter still owes before it can be closed.

Closing used to be two assignments -- ``is_closed`` and ``status`` -- reachable
from no screen at all. For a legal matter that is the wrong shape twice over:
nobody could do it, and nothing checked what closing would strand.

This reads the five things a firm cannot afford to close over. Money the client
was never billed and a client-owed trust balance are *blocking*: both are
somebody else's money, and a closed matter is where they go to be forgotten.
Open tasks, live signature requests, and unfinished intake paperwork are
*warnings* -- real, common, and often deliberate, so they are shown and
acknowledged rather than enforced.
"""

from decimal import Decimal

from sqlalchemy import func, select

from app.models.billing import Expense, TimeEntry
from app.models.matter_intake import MatterIntake
from app.models.task import Task
from app.models.signature import SignatureRequest
from app.models.trust_accounting import TrustAccount
from app.schemas.task import OPEN_TASK_STATUSES

# A request in one of these is still out with a signer and can still come back.
LIVE_SIGNATURE_STATUSES = ("sent", "partially_signed", "draft")


def _money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


async def _unbilled(db, tenant_id, matter_id):
    """Billable time and client expenses not yet attached to an invoice."""
    time_total = await db.scalar(
        select(func.coalesce(func.sum(TimeEntry.amount), 0)).where(
            TimeEntry.tenant_id == tenant_id,
            TimeEntry.matter_id == matter_id,
            TimeEntry.is_billable.is_(True),
            TimeEntry.invoice_id.is_(None),
        )
    )
    time_count = await db.scalar(
        select(func.count(TimeEntry.id)).where(
            TimeEntry.tenant_id == tenant_id,
            TimeEntry.matter_id == matter_id,
            TimeEntry.is_billable.is_(True),
            TimeEntry.invoice_id.is_(None),
        )
    )
    expense_total = await db.scalar(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.tenant_id == tenant_id,
            Expense.matter_id == matter_id,
            Expense.is_billable.is_(True),
            Expense.invoice_id.is_(None),
        )
    )
    expense_count = await db.scalar(
        select(func.count(Expense.id)).where(
            Expense.tenant_id == tenant_id,
            Expense.matter_id == matter_id,
            Expense.is_billable.is_(True),
            Expense.invoice_id.is_(None),
        )
    )
    return (
        _money(time_total) + _money(expense_total),
        int(time_count or 0) + int(expense_count or 0),
    )


async def close_readiness(db, tenant_id, matter):
    """Everything outstanding on this matter, split into blockers and warnings."""
    checks = []

    unbilled_amount, unbilled_count = await _unbilled(db, tenant_id, matter.id)
    checks.append(
        {
            "key": "unbilled_work",
            "label": "Unbilled time and expenses",
            "blocking": True,
            "clear": unbilled_count == 0,
            "count": unbilled_count,
            "amount": str(unbilled_amount),
            "detail": (
                f"{unbilled_count} billable entries worth {unbilled_amount} have never "
                "been invoiced. Invoice or write them off before closing."
                if unbilled_count
                else "All billable work is invoiced."
            ),
        }
    )

    trust_balance = _money(
        await db.scalar(
            select(func.coalesce(func.sum(TrustAccount.current_balance), 0)).where(
                TrustAccount.tenant_id == tenant_id,
                TrustAccount.matter_id == matter.id,
            )
        )
    )
    checks.append(
        {
            "key": "trust_balance",
            "label": "Client trust balance",
            "blocking": True,
            "clear": trust_balance == 0,
            "count": 1 if trust_balance else 0,
            "amount": str(trust_balance),
            "detail": (
                f"{trust_balance} of the client's money is still held in trust. "
                "Disburse or refund it before closing."
                if trust_balance
                else "No client funds are held in trust."
            ),
        }
    )

    open_tasks = int(
        await db.scalar(
            select(func.count(Task.id)).where(
                Task.tenant_id == tenant_id,
                Task.matter_id == matter.id,
                Task.status.in_(tuple(OPEN_TASK_STATUSES)),
            )
        )
        or 0
    )
    checks.append(
        {
            "key": "open_tasks",
            "label": "Open tasks",
            "blocking": False,
            "clear": open_tasks == 0,
            "count": open_tasks,
            "amount": None,
            "detail": (
                f"{open_tasks} tasks are still open. Closing cancels their follow-ups."
                if open_tasks
                else "No open tasks."
            ),
        }
    )

    live_signatures = int(
        await db.scalar(
            select(func.count(SignatureRequest.id)).where(
                SignatureRequest.tenant_id == tenant_id,
                SignatureRequest.matter_id == matter.id,
                SignatureRequest.status.in_(LIVE_SIGNATURE_STATUSES),
            )
        )
        or 0
    )
    checks.append(
        {
            "key": "live_signatures",
            "label": "Signature requests still out",
            "blocking": False,
            "clear": live_signatures == 0,
            "count": live_signatures,
            "amount": None,
            "detail": (
                f"{live_signatures} signature requests are still with a signer. "
                "Closing voids them."
                if live_signatures
                else "No signature requests are outstanding."
            ),
        }
    )

    packet = await db.scalar(
        select(MatterIntake).where(
            MatterIntake.tenant_id == tenant_id, MatterIntake.matter_id == matter.id
        )
    )
    outstanding = (
        [
            key
            for key, requirement in (packet.requirements or {}).items()
            if requirement.get("required", True) and not requirement.get("completed")
        ]
        if packet and packet.status != "cancelled"
        else []
    )
    checks.append(
        {
            "key": "client_paperwork",
            "label": "Client paperwork",
            "blocking": False,
            "clear": not outstanding,
            "count": len(outstanding),
            "amount": None,
            "detail": (
                f"{len(outstanding)} paperwork items were never completed. "
                "Closing cancels their follow-ups."
                if outstanding
                else "No outstanding client paperwork."
            ),
        }
    )

    blockers = [check for check in checks if check["blocking"] and not check["clear"]]
    warnings = [
        check for check in checks if not check["blocking"] and not check["clear"]
    ]
    return {
        "matter_id": str(matter.id),
        "already_closed": bool(matter.is_closed),
        "can_close": not blockers,
        "checks": checks,
        "blocking_count": len(blockers),
        "warning_count": len(warnings),
    }
