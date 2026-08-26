import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import billing_extended
from app.services import recurring_billing
from app.services import rbac_service
from app.services.stripe_webhook_guard import StripeTargetUnresolved


class _Result:
    def __init__(self, *, scalar=None, scalars=None, first=None):
        self._scalar = scalar
        self._scalars = scalars or []
        self._first = first

    def scalar_one_or_none(self):
        return self._scalar

    def scalar(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._scalars

    def first(self):
        return self._first


@pytest.mark.asyncio
async def test_billing_manager_rejects_view_only_capability(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4(), role="user")

    async def fake_current_user(request, db):
        return user

    async def fake_capabilities(db, user_id):
        return {"view_billing"}

    monkeypatch.setattr(billing_extended, "get_current_user", fake_current_user)
    monkeypatch.setattr(rbac_service, "get_user_capabilities", fake_capabilities)

    with pytest.raises(HTTPException) as exc:
        await billing_extended._require_billing_manager(object(), object())

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_stripe_payment_intent_binds_tenant_before_reconciliation(
    monkeypatch,
):
    tenant_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    events = []

    async def fake_set_tenant_context(db, value):
        events.append(("context", value))

    monkeypatch.setattr(billing_extended, "set_tenant_context", fake_set_tenant_context)

    invoice = SimpleNamespace(
        tenant_id=tenant_id,
        total=Decimal("25.00"),
        status="sent",
        qbo_sync_status="pending",
    )

    class FakeDb:
        def __init__(self):
            self.responses = [
                _Result(scalar=None),
                _Result(scalar=invoice),
                _Result(scalar=Decimal("0")),
            ]
            self.added = []

        async def execute(self, statement):
            events.append(("execute", statement))
            return self.responses.pop(0)

        def add(self, obj):
            events.append(("add", obj))
            self.added.append(obj)

        async def commit(self):
            events.append(("commit", None))

    db = FakeDb()

    await billing_extended._handle_payment_intent_succeeded(
        db,
        {
            "id": "pi_test_123",
            "amount": 2500,
            "metadata": {
                "tenant_id": str(tenant_id),
                "invoice_id": str(invoice_id),
            },
        },
    )

    assert events[0] == ("context", str(tenant_id))
    assert invoice.status == "paid"
    assert len(db.added) == 1
    assert db.added[0].tenant_id == tenant_id
    assert db.added[0].invoice_id == invoice_id


@pytest.mark.asyncio
async def test_stripe_payment_intent_rejects_amount_above_remaining_balance(
    monkeypatch,
):
    tenant_id = uuid.uuid4()
    invoice_id = uuid.uuid4()

    async def fake_set_tenant_context(db, value):
        return None

    monkeypatch.setattr(billing_extended, "set_tenant_context", fake_set_tenant_context)
    invoice = SimpleNamespace(
        tenant_id=tenant_id,
        total=Decimal("25.00"),
        status="partially_paid",
        qbo_sync_status="pending",
    )

    class FakeDb:
        def __init__(self):
            self.responses = [
                _Result(scalar=None),
                _Result(scalar=invoice),
                _Result(scalar=Decimal("20.00")),
            ]
            self.added = []

        async def execute(self, statement):
            return self.responses.pop(0)

        def add(self, obj):
            self.added.append(obj)

    db = FakeDb()

    with pytest.raises(StripeTargetUnresolved, match="exceeds"):
        await billing_extended._handle_payment_intent_succeeded(
            db,
            {
                "id": "pi_overpayment",
                "amount": 1000,
                "metadata": {
                    "tenant_id": str(tenant_id),
                    "invoice_id": str(invoice_id),
                },
            },
        )

    assert db.added == []
    assert invoice.status == "partially_paid"


@pytest.mark.asyncio
async def test_stripe_tenant_resolution_falls_back_to_invoice_scan(monkeypatch):
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    invoice_id = uuid.uuid4()
    contexts = []

    async def fake_set_tenant_context(db, value):
        contexts.append(value)

    monkeypatch.setattr(billing_extended, "set_tenant_context", fake_set_tenant_context)

    class FakeDb:
        def __init__(self):
            self.responses = [
                _Result(scalars=[tenant_a, tenant_b]),
                _Result(scalar=None),
                _Result(scalar=tenant_b),
            ]

        async def execute(self, statement):
            return self.responses.pop(0)

    resolved = await billing_extended._resolve_and_bind_stripe_tenant(
        FakeDb(),
        {"invoice_id": str(invoice_id)},
    )

    assert resolved == tenant_b
    assert contexts == [str(tenant_a), str(tenant_b)]


@pytest.mark.asyncio
async def test_recurring_billing_loops_and_rebinds_per_tenant(monkeypatch):
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    events = []

    async def fake_set_tenant_context(db, value):
        events.append(("context", value))

    async def fake_generate_for_tenant(db, tenant_id, today, generated_so_far):
        events.append(("process", str(tenant_id), generated_so_far, today))
        return {"generated": 1, "skipped": 2, "errors": 3}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def execute(self, statement):
            events.append(("tenants", statement))
            return _Result(scalars=[tenant_a, tenant_b])

        async def commit(self):
            events.append(("commit", None))

    monkeypatch.setattr(
        recurring_billing, "set_tenant_context", fake_set_tenant_context
    )
    monkeypatch.setattr(
        recurring_billing,
        "_generate_recurring_invoices_for_tenant",
        fake_generate_for_tenant,
    )
    monkeypatch.setattr(recurring_billing, "async_session_maker", lambda: FakeSession())

    result = await recurring_billing.generate_recurring_invoices()

    assert result == {"generated": 2, "skipped": 4, "errors": 6}
    assert events[1:] == [
        ("context", str(tenant_a)),
        ("process", str(tenant_a), 0, date.today()),
        ("commit", None),
        ("context", str(tenant_a)),
        ("context", str(tenant_b)),
        ("process", str(tenant_b), 1, date.today()),
        ("commit", None),
        ("context", str(tenant_b)),
    ]
