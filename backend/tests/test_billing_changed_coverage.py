"""Focused coverage for billing branches added in the current change."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import recurring_billing
from app.services.matter_context import MatterContextService
from app.services.qbo_sync import QBOSyncService


class _Result:
    def __init__(self, *, scalar=None, one=None, scalars=None, rows=None):
        self._scalar = scalar
        self._one = one
        self._scalars = scalars or []
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalar(self):
        return self._scalar

    def one(self):
        return self._one

    def scalars(self):
        return SimpleNamespace(all=lambda: self._scalars)

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


@pytest.mark.parametrize(
    ("tax_amount", "tax_code"),
    [(Decimal("0"), "NON"), (Decimal("1.25"), "TAX")],
)
@pytest.mark.asyncio
async def test_qbo_invoice_uses_legacy_expense_mapping_and_private_note(
    monkeypatch, tax_amount, tax_code
):
    tenant_id = str(uuid4())
    invoice_id = uuid4()
    expense_id = uuid4()
    invoice = SimpleNamespace(
        id=invoice_id,
        tenant_id=tenant_id,
        matter_id=uuid4(),
        status="sent",
        issue_date=date(2026, 1, 2),
        due_date=date(2026, 2, 1),
        invoice_number="INV-1",
        notes=None,
        tax_amount=tax_amount,
        qbo_invoice_id=None,
    )
    matter = SimpleNamespace(counterparty="Client", matter_name="Matter")
    line = SimpleNamespace(
        source_type="expense",
        source_id=expense_id,
        unit_price=Decimal("25"),
        quantity=Decimal("1"),
        amount=Decimal("25"),
        description="Filing fee",
    )
    mapping = SimpleNamespace(
        source_type="expense",
        expense_category="filing_fee",
        qbo_item_id="7",
        qbo_item_name="Filing",
    )

    class FakeDb:
        def __init__(self):
            self.results = iter(
                [
                    _Result(scalar=invoice),
                    _Result(scalar=matter),
                    _Result(scalars=[line]),
                    _Result(scalars=[mapping]),
                    _Result(rows=[(expense_id, "court filing")]),
                ]
            )

        async def execute(self, _statement):
            return next(self.results)

        async def commit(self):
            return None

    service = QBOSyncService(FakeDb(), tenant_id, "token")
    monkeypatch.setattr(
        "app.services.qbo_sync.set_tenant_context",
        lambda _db, _tenant_id: _async_value(None),
    )
    monkeypatch.setattr(service, "_get_realm_id", lambda: _async_value("realm"))
    monkeypatch.setattr(
        service,
        "_ensure_customer",
        lambda _realm, _matter: _async_value({"Id": "customer-1"}),
    )
    requests = []

    async def fake_request(method, _url, json_data=None, **_kwargs):
        requests.append((method, json_data))
        return {"Invoice": {"Id": "qbo-1"}}

    monkeypatch.setattr(service, "_request", fake_request)

    result = await service.sync_invoice(str(invoice_id))

    assert result == {"Invoice": {"Id": "qbo-1"}}
    assert (
        requests[0][1]["PrivateNote"]
        == f"LawHand invoice INV-1; matter {invoice.matter_id}"
    )
    assert invoice.billed_at is not None
    assert invoice.status == "sent"
    assert requests[0][1]["Line"][0]["SalesItemLineDetail"]["ItemRef"] == {
        "value": "7",
        "name": "Filing",
    }
    assert requests[0][1]["Line"][0]["SalesItemLineDetail"]["TaxCodeRef"] == {
        "value": tax_code
    }


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_recurring_invoice_uses_expense_amount_fallback(monkeypatch):
    tenant_id = uuid4()
    matter_id = uuid4()
    expense = SimpleNamespace(
        id=uuid4(),
        client_amount=None,
        amount=Decimal("40"),
        description="Courier",
        category="courier",
    )
    matter = SimpleNamespace(
        id=matter_id,
        tenant_id=tenant_id,
        billing_cycle="monthly",
        billing_method="hourly",
        is_closed=False,
        status="active",
        tax_rate=Decimal("0"),
        user_id=uuid4(),
        matter_name="Matter",
    )

    class FakeDb:
        def __init__(self):
            self.results = iter(
                [
                    _Result(scalars=[matter]),
                    _Result(scalar=None),
                    _Result(scalars=[]),
                    _Result(scalars=[expense]),
                    _Result(rows=[]),
                ]
            )
            self.added = []

        async def execute(self, _statement):
            return next(self.results)

        def add(self, value):
            self.added.append(value)

        async def flush(self):
            return None

    db = FakeDb()
    result = await recurring_billing._generate_recurring_invoices_for_tenant(
        db, tenant_id, date(2026, 2, 1), 0
    )

    assert result == {"generated": 1, "skipped": 0, "errors": 0}
    invoice = db.added[0]
    line_item = db.added[1]
    assert invoice.subtotal == Decimal("40")
    assert line_item.amount == Decimal("40")


def test_matter_context_formats_billable_expense_summary():
    rendered = MatterContextService().format_matter_context(
        {
            "matter_name": "Matter",
            "budget": {
                "budget_amount": 1000,
                "budget_currency": "USD",
                "total_billed": 250,
                "total_hours": 3,
                "billable_expense_amount": 75,
            },
        }
    )

    assert "Billable client expenses: $75.00" in rendered


@pytest.mark.asyncio
async def test_qbo_invoice_populates_contact_ar_and_billing_state(monkeypatch):
    tenant_id = str(uuid4())
    invoice_id = uuid4()
    time_entry_id = uuid4()
    contact_id = uuid4()
    invoice = SimpleNamespace(
        id=invoice_id,
        tenant_id=tenant_id,
        matter_id=uuid4(),
        status="draft",
        issue_date=date(2026, 1, 2),
        due_date=date(2026, 2, 1),
        invoice_number="INV-2",
        notes="Client-facing memo",
        qbo_invoice_id=None,
        billed_at=None,
    )
    matter = SimpleNamespace(
        counterparty="Client",
        matter_name="Matter",
        case_number="CASE-123456789012345",
        client_contact_id=contact_id,
    )
    line = SimpleNamespace(
        source_type="time_entry",
        source_id=time_entry_id,
        unit_price=Decimal("250"),
        quantity=Decimal("1"),
        amount=Decimal("250"),
        description="Legal services",
    )
    contact = SimpleNamespace(
        email="billing@example.com",
        address={
            "street": "1 Main St",
            "city": "Fargo",
            "state": "ND",
            "zip": "58102",
            "country": "US",
        },
    )
    time_entry = SimpleNamespace(status="draft")

    class FakeDb:
        def __init__(self):
            self.results = iter(
                [
                    _Result(scalar=invoice),
                    _Result(scalar=matter),
                    _Result(scalars=[line]),
                    _Result(scalars=[]),
                    _Result(scalar=contact),
                    _Result(scalars=[time_entry]),
                ]
            )
            self.committed = False

        async def execute(self, _statement):
            return next(self.results)

        async def commit(self):
            self.committed = True

    db = FakeDb()
    service = QBOSyncService(
        db,
        tenant_id,
        "token",
        ar_account_id="84",
        ar_account_name="Accounts Receivable",
    )
    monkeypatch.setattr(
        "app.services.qbo_sync.set_tenant_context",
        lambda _db, _tenant_id: _async_value(None),
    )
    monkeypatch.setattr(service, "_get_realm_id", lambda: _async_value("realm"))
    monkeypatch.setattr(
        service,
        "_ensure_customer",
        lambda _realm, _matter: _async_value({"Id": "customer-1"}),
    )
    requests = []

    async def fake_request(method, _url, json_data=None, **_kwargs):
        requests.append((method, json_data))
        return {"Invoice": {"Id": "qbo-2", "SyncToken": "3"}}

    monkeypatch.setattr(service, "_request", fake_request)

    result = await service.sync_invoice(str(invoice_id))
    payload = requests[0][1]

    assert result == {"Invoice": {"Id": "qbo-2", "SyncToken": "3"}}
    assert payload["ARAccountRef"] == {"value": "84", "name": "Accounts Receivable"}
    assert payload["CustomerMemo"] == {"value": "Client-facing memo"}
    assert payload["PONumber"] == "CASE-1234567890"
    assert payload["BillEmail"] == {"Address": "billing@example.com"}
    assert payload["BillAddr"]["Line1"] == "1 Main St"
    assert invoice.qbo_invoice_id == "qbo-2"
    assert invoice.qbo_sync_token == "3"
    assert invoice.qbo_sync_status == "synced"
    assert invoice.status == "sent"
    assert invoice.billed_at == invoice.qbo_synced_at
    assert time_entry.status == "invoiced"
    assert db.committed is True
