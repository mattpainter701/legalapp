import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services.matter_budget import (
    MatterBillableTotals,
    load_matter_billable_totals,
)


def test_matter_billable_totals_include_expenses_but_keep_hours_time_only():
    totals = MatterBillableTotals(
        total_hours=2.5,
        time_amount=Decimal("500.00"),
        expense_amount=Decimal("125.00"),
        unbilled_time_amount=Decimal("200.00"),
        unbilled_expense_amount=Decimal("50.00"),
    )

    assert totals.total_hours == 2.5
    assert totals.total_amount == Decimal("625.00")
    assert totals.total_unbilled == Decimal("250.00")


@pytest.mark.asyncio
async def test_loader_uses_client_charge_and_excludes_nonbillable_expenses():
    time_result = MagicMock()
    time_result.one.return_value = (Decimal("2.5"), Decimal("500.00"))
    unbilled_time_result = MagicMock()
    unbilled_time_result.scalar.return_value = Decimal("200.00")
    expense_result = MagicMock()
    expense_result.one.return_value = (Decimal("125.00"), Decimal("50.00"))
    db = AsyncMock()
    db.execute.side_effect = [time_result, unbilled_time_result, expense_result]

    totals = await load_matter_billable_totals(db, uuid.uuid4(), uuid.uuid4())

    assert totals.total_amount == Decimal("625.00")
    assert totals.total_unbilled == Decimal("250.00")
    expense_statement = db.execute.await_args_list[2].args[0]
    sql = str(expense_statement.compile(dialect=postgresql.dialect()))
    assert "coalesce(expenses.client_amount, expenses.amount)" in sql
    assert "expenses.is_billable IS true" in sql
    assert "FILTER (WHERE expenses.invoice_id IS NULL)" in sql
