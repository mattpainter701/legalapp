"""Tests for billing reports: realization, WIP, and A/R aging (Task 1308b)."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.billing import Expense, Invoice, Payment, TimeEntry
from app.models.plugin import Matter
from app.models.tenant import Tenant
from app.models.user import User


def _make_matter(tenant_id, user_id, name="Case A", slug=None) -> Matter:
    return Matter(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        slug=slug or name.lower().replace(" ", "-"),
        matter_name=name,
        matter_type="general",
        status="open",
    )


def _make_time_entry(
    tenant_id,
    matter_id,
    user_id,
    hours,
    rate,
    is_billable=True,
    invoice_id=None,
    entry_date=None,
) -> TimeEntry:
    amount = Decimal(hours) * Decimal(rate)
    return TimeEntry(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        matter_id=matter_id,
        user_id=user_id,
        description="Work performed",
        hours=Decimal(hours),
        hourly_rate=Decimal(rate),
        amount=amount,
        date=entry_date or date.today(),
        is_billable=is_billable,
        invoice_id=invoice_id,
    )


def _make_invoice(
    tenant_id,
    matter_id,
    created_by,
    invoice_number,
    total,
    status="sent",
    issue_date=None,
    due_date=None,
) -> Invoice:
    total_dec = Decimal(total)
    return Invoice(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        matter_id=matter_id,
        invoice_number=invoice_number,
        status=status,
        issue_date=issue_date or date.today(),
        due_date=due_date or date.today(),
        subtotal=total_dec,
        total=total_dec,
        created_by=created_by,
    )


def _make_expense(tenant_id, matter_id, user_id, amount, *, client_amount=None, billable=True, invoice_id=None, review_status="ready") -> Expense:
    return Expense(
        id=uuid.uuid4(), tenant_id=tenant_id, matter_id=matter_id, user_id=user_id,
        description="Filing fee", amount=Decimal(amount), client_amount=(Decimal(client_amount) if client_amount is not None else None),
        date=date.today(), category="filing_fee", is_billable=billable, invoice_id=invoice_id,
        review_status=review_status,
    )


def _make_payment(tenant_id, invoice_id, amount, payment_date=None) -> Payment:
    return Payment(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        invoice_id=invoice_id,
        amount=Decimal(amount),
        payment_date=payment_date or date.today(),
    )


@pytest.mark.asyncio
async def test_realization_report_basic(client, db_session, test_tenant, test_user):
    matter = _make_matter(test_tenant.id, test_user.id, name="Realization Case")
    db_session.add(matter)
    await db_session.commit()

    invoice = _make_invoice(
        test_tenant.id, matter.id, test_user.id, "INV-1001", total="1000.00"
    )
    db_session.add(invoice)
    await db_session.commit()

    entry = _make_time_entry(
        test_tenant.id,
        matter.id,
        test_user.id,
        hours="10.00",
        rate="100.00",
        is_billable=True,
        invoice_id=invoice.id,
    )
    db_session.add(entry)

    payment = _make_payment(test_tenant.id, invoice.id, amount="800.00")
    db_session.add(payment)
    await db_session.commit()

    r = await client.get("/api/reports/billing/realization")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    row = data[0]
    assert row["matter_id"] == str(matter.id)
    assert row["matter_name"] == "Realization Case"
    assert row["billable_hours"] == 10.0
    assert row["billable_amount"] == 1000.0
    assert row["collected_amount"] == 800.0
    assert row["realization_pct"] == 80.0


@pytest.mark.asyncio
async def test_realization_report_csv(client, db_session, test_tenant, test_user):
    matter = _make_matter(test_tenant.id, test_user.id, name="CSV Case")
    db_session.add(matter)
    await db_session.commit()

    entry = _make_time_entry(
        test_tenant.id, matter.id, test_user.id, hours="5.00", rate="200.00"
    )
    db_session.add(entry)
    await db_session.commit()

    r = await client.get("/api/reports/billing/realization?format=csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    body = r.text
    assert "matter_name" in body
    assert "realization_pct" in body
    assert "CSV Case" in body


@pytest.mark.asyncio
async def test_wip_excludes_invoiced(client, db_session, test_tenant, test_user):
    matter = _make_matter(test_tenant.id, test_user.id, name="WIP Case")
    db_session.add(matter)
    await db_session.commit()

    invoice = _make_invoice(
        test_tenant.id, matter.id, test_user.id, "INV-2001", total="500.00"
    )
    db_session.add(invoice)
    await db_session.commit()

    # Invoiced entry — should be excluded from WIP
    invoiced_entry = _make_time_entry(
        test_tenant.id,
        matter.id,
        test_user.id,
        hours="5.00",
        rate="100.00",
        is_billable=True,
        invoice_id=invoice.id,
    )
    db_session.add(invoiced_entry)

    # Uninvoiced entry — should count toward WIP
    uninvoiced_entry = _make_time_entry(
        test_tenant.id,
        matter.id,
        test_user.id,
        hours="3.00",
        rate="100.00",
        is_billable=True,
        invoice_id=None,
    )
    db_session.add(uninvoiced_entry)
    await db_session.commit()

    r = await client.get("/api/reports/billing/wip")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    row = data[0]
    assert row["matter_id"] == str(matter.id)
    assert row["matter_name"] == "WIP Case"
    assert row["wip_hours"] == 3.0
    assert row["wip_value"] == 300.0


@pytest.mark.asyncio
async def test_budget_and_billing_reports_include_billable_expense_client_amount_only(
    client, db_session, test_tenant, test_user
):
    matter = _make_matter(test_tenant.id, test_user.id, name="Expense Budget Case")
    matter.budget_amount = Decimal("1000.00")
    db_session.add(matter)
    await db_session.commit()
    db_session.add_all([
        _make_expense(test_tenant.id, matter.id, test_user.id, "50.00", client_amount="75.00"),
        _make_expense(test_tenant.id, matter.id, test_user.id, "40.00", billable=False),
    ])
    await db_session.commit()

    budget = await client.get(f"/api/reports/matters/{matter.id}/budget")
    assert budget.status_code == 200, budget.text
    body = budget.json()
    assert body["total_hours"] == 0.0
    assert body["total_billed"] == 75.0
    assert body["billable_expense_amount"] == 75.0
    assert body["utilization_pct"] == 7.5

    matter_budget = await client.get(f"/api/matters/{matter.id}/budget")
    assert matter_budget.status_code == 200, matter_budget.text
    matter_budget_body = matter_budget.json()
    assert matter_budget_body["total_billed"] == "75.00"
    assert matter_budget_body["billable_time_amount"] == "0"
    assert matter_budget_body["billable_expense_amount"] == "75.00"
    assert matter_budget_body["remaining"] == "925.00"

    matter_stats = await client.get("/api/matters/stats")
    assert matter_stats.status_code == 200, matter_stats.text
    assert matter_stats.json()["total_billed"] == "75.00"
    assert matter_stats.json()["total_unbilled"] == "75.00"

    realization = await client.get("/api/reports/billing/realization")
    row = next(item for item in realization.json() if item["matter_id"] == str(matter.id))
    assert row["billable_hours"] == 0.0
    assert row["billable_expense_amount"] == 75.0
    assert row["billable_amount"] == 75.0

    wip = await client.get("/api/reports/billing/wip")
    row = next(item for item in wip.json() if item["matter_id"] == str(matter.id))
    assert row["wip_hours"] == 0.0
    assert row["wip_value"] == 75.0


@pytest.mark.asyncio
async def test_aging_buckets(client, db_session, test_tenant, test_user):
    today = date.today()

    matter_a = _make_matter(test_tenant.id, test_user.id, name="Aging Case A")
    matter_b = _make_matter(test_tenant.id, test_user.id, name="Aging Case B")
    matter_c = _make_matter(test_tenant.id, test_user.id, name="Paid Case")
    db_session.add_all([matter_a, matter_b, matter_c])
    await db_session.commit()

    # Outstanding invoice, 40 days overdue -> days_31_60
    invoice_a = _make_invoice(
        test_tenant.id,
        matter_a.id,
        test_user.id,
        "INV-3001",
        total="1000.00",
        status="sent",
        issue_date=today - timedelta(days=70),
        due_date=today - timedelta(days=40),
    )
    db_session.add(invoice_a)

    # Outstanding invoice, 75 days overdue -> days_61_90
    invoice_b = _make_invoice(
        test_tenant.id,
        matter_b.id,
        test_user.id,
        "INV-3002",
        total="2000.00",
        status="sent",
        issue_date=today - timedelta(days=100),
        due_date=today - timedelta(days=75),
    )
    db_session.add(invoice_b)

    # Fully paid invoice -> excluded
    invoice_c = _make_invoice(
        test_tenant.id,
        matter_c.id,
        test_user.id,
        "INV-3003",
        total="500.00",
        status="paid",
        issue_date=today - timedelta(days=50),
        due_date=today - timedelta(days=20),
    )
    db_session.add(invoice_c)
    await db_session.commit()

    payment_c = _make_payment(test_tenant.id, invoice_c.id, amount="500.00")
    db_session.add(payment_c)
    await db_session.commit()

    r = await client.get("/api/reports/billing/aging")
    assert r.status_code == 200
    data = r.json()

    by_matter = {row["matter_id"]: row for row in data}

    assert str(matter_a.id) in by_matter
    assert str(matter_b.id) in by_matter
    assert str(matter_c.id) not in by_matter

    row_a = by_matter[str(matter_a.id)]
    assert row_a["days_31_60"] == 1000.0
    assert row_a["days_0_30"] == 0.0
    assert row_a["days_61_90"] == 0.0
    assert row_a["days_90_plus"] == 0.0

    row_b = by_matter[str(matter_b.id)]
    assert row_b["days_61_90"] == 2000.0
    assert row_b["days_0_30"] == 0.0
    assert row_b["days_31_60"] == 0.0
    assert row_b["days_90_plus"] == 0.0


@pytest.mark.asyncio
async def test_empty_reports_return_empty_lists(
    client, db_session, test_tenant, test_user
):
    r1 = await client.get("/api/reports/billing/realization")
    r2 = await client.get("/api/reports/billing/wip")
    r3 = await client.get("/api/reports/billing/aging")

    assert r1.status_code == 200
    assert r1.json() == []
    assert r2.status_code == 200
    assert r2.json() == []
    assert r3.status_code == 200
    assert r3.json() == []


@pytest.mark.asyncio
async def test_realization_tenant_isolation(client, db_session, test_tenant, test_user):
    # A second tenant + user must really exist — matters.user_id is an FK to users.
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other Law Firm",
        domain="otherfirm.com",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(other_tenant)
    await db_session.commit()

    other_user = User(
        id=uuid.uuid4(),
        tenant_id=other_tenant.id,
        email="attorney@otherfirm.com",
        full_name="Other Attorney",
        role="admin",
        oauth_provider="google",
        oauth_subject="google-sub-other",
        is_active=True,
    )
    db_session.add(other_user)
    await db_session.commit()

    own_matter = _make_matter(test_tenant.id, test_user.id, name="Own Case")
    other_matter = _make_matter(other_tenant.id, other_user.id, name="Other Case")
    db_session.add_all([own_matter, other_matter])
    await db_session.commit()

    own_entry = _make_time_entry(
        test_tenant.id, own_matter.id, test_user.id, hours="2.00", rate="100.00"
    )
    other_entry = _make_time_entry(
        other_tenant.id, other_matter.id, other_user.id, hours="4.00", rate="100.00"
    )
    db_session.add_all([own_entry, other_entry])
    await db_session.commit()

    r = await client.get("/api/reports/billing/realization")
    assert r.status_code == 200
    data = r.json()
    matter_ids = {row["matter_id"] for row in data}
    assert str(own_matter.id) in matter_ids
    assert str(other_matter.id) not in matter_ids
