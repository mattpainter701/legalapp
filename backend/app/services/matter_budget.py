"""Shared billable totals for matter budget surfaces."""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import Expense, TimeEntry


@dataclass(frozen=True)
class MatterBillableTotals:
    total_hours: float
    time_amount: Decimal
    expense_amount: Decimal
    unbilled_time_amount: Decimal
    unbilled_expense_amount: Decimal

    @property
    def total_amount(self) -> Decimal:
        return self.time_amount + self.expense_amount

    @property
    def total_unbilled(self) -> Decimal:
        return self.unbilled_time_amount + self.unbilled_expense_amount


def expense_client_amount_expression():
    """Return the amount charged to the client for an expense."""
    return func.coalesce(Expense.client_amount, Expense.amount)


async def load_matter_billable_totals(
    db: AsyncSession, matter_id: UUID, tenant_id: UUID
) -> MatterBillableTotals:
    """Load billable time and expense totals for one tenant-scoped matter."""
    time_result = await db.execute(
        select(
            func.coalesce(func.sum(TimeEntry.hours), 0),
            func.coalesce(func.sum(TimeEntry.amount), 0),
        ).where(
            TimeEntry.matter_id == matter_id,
            TimeEntry.tenant_id == tenant_id,
            TimeEntry.is_billable.is_(True),
        )
    )
    total_hours, time_amount = time_result.one()

    unbilled_time_result = await db.execute(
        select(func.coalesce(func.sum(TimeEntry.amount), 0)).where(
            TimeEntry.matter_id == matter_id,
            TimeEntry.tenant_id == tenant_id,
            TimeEntry.is_billable.is_(True),
            TimeEntry.invoice_id.is_(None),
        )
    )

    client_amount = expense_client_amount_expression()
    expense_result = await db.execute(
        select(
            func.coalesce(func.sum(client_amount), 0),
            func.coalesce(
                func.sum(client_amount).filter(Expense.invoice_id.is_(None)), 0
            ),
        ).where(
            Expense.matter_id == matter_id,
            Expense.tenant_id == tenant_id,
            Expense.is_billable.is_(True),
        )
    )
    expense_amount, unbilled_expense_amount = expense_result.one()

    return MatterBillableTotals(
        total_hours=float(total_hours or 0),
        time_amount=Decimal(str(time_amount or 0)),
        expense_amount=Decimal(str(expense_amount or 0)),
        unbilled_time_amount=Decimal(str(unbilled_time_result.scalar() or 0)),
        unbilled_expense_amount=Decimal(str(unbilled_expense_amount or 0)),
    )
