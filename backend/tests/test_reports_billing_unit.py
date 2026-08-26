import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.routers.reports import _realization_report, _wip_report


def _rows(*values):
    result = MagicMock()
    result.all.return_value = list(values)
    return result


@pytest.mark.asyncio
async def test_realization_includes_expense_only_matters_without_n_plus_one_queries():
    matter_id = uuid.uuid4()
    db = AsyncMock()
    db.execute.side_effect = [
        _rows(),
        _rows((matter_id, "Expense Only", Decimal("75.00"))),
        _rows(),
    ]

    report = await _realization_report(db, uuid.uuid4())

    assert report == [
        {
            "matter_id": str(matter_id),
            "matter_name": "Expense Only",
            "billable_hours": 0.0,
            "billable_amount": 75.0,
            "billable_time_amount": 0.0,
            "billable_expense_amount": 75.0,
            "collected_amount": 0.0,
            "realization_pct": 0.0,
        }
    ]
    assert db.execute.await_count == 3
    expense_sql = str(
        db.execute.await_args_list[1].args[0].compile(dialect=postgresql.dialect())
    )
    assert "coalesce(expenses.client_amount, expenses.amount)" in expense_sql
    assert "expenses.is_billable IS true" in expense_sql


@pytest.mark.asyncio
async def test_wip_includes_unbilled_expense_only_matters():
    matter_id = uuid.uuid4()
    db = AsyncMock()
    db.execute.side_effect = [
        _rows(),
        _rows((matter_id, "Expense WIP", Decimal("90.00"))),
    ]

    report = await _wip_report(db, uuid.uuid4())

    assert report == [
        {
            "matter_id": str(matter_id),
            "matter_name": "Expense WIP",
            "wip_hours": 0.0,
            "wip_value": 90.0,
        }
    ]
    assert db.execute.await_count == 2
    expense_sql = str(
        db.execute.await_args_list[1].args[0].compile(dialect=postgresql.dialect())
    )
    assert "expenses.is_billable IS true" in expense_sql
    assert "expenses.invoice_id IS NULL" in expense_sql
