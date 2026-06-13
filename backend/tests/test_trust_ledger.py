"""Tests for pooled trust (IOLTA) ledger, reconciliation persistence, and
client ledger statements (Task 1303)."""

import uuid
from decimal import Decimal

import pytest

from app.models.plugin import Matter
from app.models.tenant import Tenant
from app.models.trust_accounting import TrustReconciliation
from app.models.user import User
from sqlalchemy import select


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


# ── Bank account CRUD ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bank_account_crud(client, db_session, test_tenant, test_user):
    # Create
    r = await client.post(
        "/api/trust/bank-accounts",
        json={
            "account_name": "Main IOLTA",
            "bank_name": "First National",
            "account_number_masked": "1234",
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["account_name"] == "Main IOLTA"
    assert data["is_active"] is True
    assert Decimal(str(data["book_balance"])) == Decimal("0")
    assert data["client_ledger_count"] == 0
    bank_account_id = data["id"]

    # List
    r = await client.get("/api/trust/bank-accounts")
    assert r.status_code == 200
    listed = r.json()
    assert listed["total"] == 1
    assert Decimal(str(listed["total_book_balance"])) == Decimal("0")

    # Get
    r = await client.get(f"/api/trust/bank-accounts/{bank_account_id}")
    assert r.status_code == 200
    assert r.json()["id"] == bank_account_id

    # Patch — rename and deactivate
    r = await client.patch(
        f"/api/trust/bank-accounts/{bank_account_id}",
        json={"account_name": "Main IOLTA (renamed)", "is_active": False},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["account_name"] == "Main IOLTA (renamed)"
    assert data["is_active"] is False


# ── Linking client ledgers to a pooled bank account ────────────────────────


async def _create_bank_account(client, name="Pooled IOLTA"):
    r = await client.post(
        "/api/trust/bank-accounts",
        json={"account_name": name, "bank_name": "First National"},
    )
    assert r.status_code == 201
    return r.json()["id"]


async def _create_trust_account(client, matter_id, name):
    r = await client.post(
        "/api/trust/accounts",
        json={"matter_id": str(matter_id), "account_name": name},
    )
    assert r.status_code == 201
    return r.json()


async def _link_bank_account(client, trust_account_id, bank_account_id):
    r = await client.patch(
        f"/api/trust/accounts/{trust_account_id}",
        json={"bank_account_id": bank_account_id},
    )
    assert r.status_code == 200
    return r.json()


async def _deposit(client, trust_account_id, amount, description="Initial deposit"):
    r = await client.post(
        "/api/trust/transactions",
        json={
            "trust_account_id": trust_account_id,
            "transaction_type": "deposit",
            "amount": str(amount),
            "description": description,
        },
    )
    assert r.status_code == 201
    return r.json()


@pytest.mark.asyncio
async def test_link_client_ledgers_and_book_balance(
    client, db_session, test_tenant, test_user
):
    matter_a = _make_matter(test_tenant.id, test_user.id, name="Ledger Case A")
    matter_b = _make_matter(test_tenant.id, test_user.id, name="Ledger Case B")
    db_session.add_all([matter_a, matter_b])
    await db_session.commit()

    bank_account_id = await _create_bank_account(client)

    acct_a = await _create_trust_account(client, matter_a.id, "Client A Ledger")
    acct_b = await _create_trust_account(client, matter_b.id, "Client B Ledger")

    await _link_bank_account(client, acct_a["id"], bank_account_id)
    await _link_bank_account(client, acct_b["id"], bank_account_id)

    await _deposit(client, acct_a["id"], "1000.00")
    await _deposit(client, acct_b["id"], "500.00")

    r = await client.get(f"/api/trust/bank-accounts/{bank_account_id}")
    assert r.status_code == 200
    data = r.json()
    assert Decimal(str(data["book_balance"])) == Decimal("1500.00")
    assert data["client_ledger_count"] == 2


# ── Pooled three-way reconciliation ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_pooled_reconcile_balanced(client, db_session, test_tenant, test_user):
    matter = _make_matter(test_tenant.id, test_user.id, name="Pooled Balanced Case")
    db_session.add(matter)
    await db_session.commit()

    bank_account_id = await _create_bank_account(client, name="Balanced Pool")
    acct = await _create_trust_account(client, matter.id, "Balanced Ledger")
    await _link_bank_account(client, acct["id"], bank_account_id)
    await _deposit(client, acct["id"], "1000.00")

    r = await client.post(
        f"/api/trust/bank-accounts/{bank_account_id}/reconcile",
        json={"bank_balance": "1000.00"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["is_reconciled"] is True
    assert Decimal(str(data["difference"])) == Decimal("0")
    assert Decimal(str(data["trust_liability"])) == Decimal("1000.00")
    assert data["bank_account_id"] == bank_account_id

    # Snapshot persisted
    result = await db_session.execute(
        select(TrustReconciliation).where(
            TrustReconciliation.bank_account_id == uuid.UUID(bank_account_id)
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].is_reconciled is True

    # History endpoint
    r = await client.get(f"/api/trust/bank-accounts/{bank_account_id}/reconciliations")
    assert r.status_code == 200
    history = r.json()
    assert len(history) == 1
    assert history[0]["is_reconciled"] is True


@pytest.mark.asyncio
async def test_pooled_reconcile_unbalanced(client, db_session, test_tenant, test_user):
    matter = _make_matter(test_tenant.id, test_user.id, name="Pooled Unbalanced Case")
    db_session.add(matter)
    await db_session.commit()

    bank_account_id = await _create_bank_account(client, name="Unbalanced Pool")
    acct = await _create_trust_account(client, matter.id, "Unbalanced Ledger")
    await _link_bank_account(client, acct["id"], bank_account_id)
    await _deposit(client, acct["id"], "1000.00")

    r = await client.post(
        f"/api/trust/bank-accounts/{bank_account_id}/reconcile",
        json={"bank_balance": "900.00"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["is_reconciled"] is False
    assert Decimal(str(data["difference"])) == Decimal("-100.00")

    result = await db_session.execute(
        select(TrustReconciliation).where(
            TrustReconciliation.bank_account_id == uuid.UUID(bank_account_id)
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].is_reconciled is False


# ── Client ledger statement ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ledger_statement(client, db_session, test_tenant, test_user):
    matter = _make_matter(test_tenant.id, test_user.id, name="Statement Case")
    db_session.add(matter)
    await db_session.commit()

    acct = await _create_trust_account(client, matter.id, "Statement Ledger")

    await _deposit(client, acct["id"], "1000.00", description="Retainer deposit")

    r = await client.post(
        "/api/trust/transactions",
        json={
            "trust_account_id": acct["id"],
            "transaction_type": "disbursement",
            "amount": "300.00",
            "description": "Filing fee payment",
        },
    )
    assert r.status_code == 201

    r = await client.get(f"/api/trust/accounts/{acct['id']}/statement")
    assert r.status_code == 200
    data = r.json()
    assert Decimal(str(data["opening_balance"])) == Decimal("0")
    assert Decimal(str(data["closing_balance"])) == Decimal("700.00")
    assert Decimal(str(data["total_credits"])) == Decimal("1000.00")
    assert Decimal(str(data["total_debits"])) == Decimal("300.00")
    assert len(data["lines"]) == 2

    # First line = deposit (+1000), running balance 1000
    assert Decimal(str(data["lines"][0]["amount"])) == Decimal("1000.00")
    assert Decimal(str(data["lines"][0]["running_balance"])) == Decimal("1000.00")

    # Second line = disbursement (-300), running balance 700
    assert Decimal(str(data["lines"][1]["amount"])) == Decimal("-300.00")
    assert Decimal(str(data["lines"][1]["running_balance"])) == Decimal("700.00")


@pytest.mark.asyncio
async def test_ledger_statement_csv(client, db_session, test_tenant, test_user):
    matter = _make_matter(test_tenant.id, test_user.id, name="CSV Statement Case")
    db_session.add(matter)
    await db_session.commit()

    acct = await _create_trust_account(client, matter.id, "CSV Statement Ledger")
    await _deposit(client, acct["id"], "500.00")

    r = await client.get(f"/api/trust/accounts/{acct['id']}/statement?format=csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    body = r.text
    assert "transaction_date" in body
    assert "running_balance" in body
    assert "transaction_type" in body


# ── Overdraft regression ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_overdraft_guardrail_still_blocks(
    client, db_session, test_tenant, test_user
):
    matter = _make_matter(test_tenant.id, test_user.id, name="Overdraft Case")
    db_session.add(matter)
    await db_session.commit()

    acct = await _create_trust_account(client, matter.id, "Overdraft Ledger")
    await _deposit(client, acct["id"], "100.00")

    r = await client.post(
        "/api/trust/transactions",
        json={
            "trust_account_id": acct["id"],
            "transaction_type": "disbursement",
            "amount": "200.00",
            "description": "Over-balance disbursement",
        },
    )
    assert r.status_code == 400


# ── Existing per-account reconcile still works + persists snapshot ─────────


@pytest.mark.asyncio
async def test_per_account_reconcile_unchanged_and_persists_snapshot(
    client, db_session, test_tenant, test_user
):
    matter = _make_matter(test_tenant.id, test_user.id, name="Per Account Recon Case")
    db_session.add(matter)
    await db_session.commit()

    acct = await _create_trust_account(client, matter.id, "Per Account Ledger")
    await _deposit(client, acct["id"], "1000.00")

    r = await client.post(
        f"/api/trust/accounts/{acct['id']}/reconcile",
        json={"trust_account_id": acct["id"], "bank_balance": "1000.00"},
    )
    assert r.status_code == 200
    data = r.json()
    # Original response shape — all keys still present
    assert data["trust_account_id"] == acct["id"]
    assert "as_of_date" in data
    assert "bank_balance" in data
    assert "trust_liability" in data
    assert "unallocated" in data
    assert "outstanding_deposits" in data
    assert "outstanding_disbursements" in data
    assert "adjusted_bank_balance" in data
    assert "is_reconciled" in data
    assert "difference" in data
    assert "reconciling_items" in data

    # New: a TrustReconciliation snapshot row now exists
    result = await db_session.execute(
        select(TrustReconciliation).where(
            TrustReconciliation.trust_account_id == uuid.UUID(acct["id"])
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].bank_account_id is None


# ── Tenant isolation ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bank_account_tenant_isolation(
    client, db_session, test_tenant, test_user
):
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other Law Firm",
        domain="otherfirm-trust.com",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(other_tenant)
    await db_session.commit()

    other_user = User(
        id=uuid.uuid4(),
        tenant_id=other_tenant.id,
        email="attorney@otherfirm-trust.com",
        full_name="Other Attorney",
        role="admin",
        oauth_provider="google",
        oauth_subject="google-sub-other-trust",
        is_active=True,
    )
    db_session.add(other_user)
    await db_session.commit()

    # Create a bank account directly in the other tenant's schema (bypassing
    # the API, which always uses test_user's tenant).
    from app.models.trust_accounting import TrustBankAccount

    other_bank_account = TrustBankAccount(
        tenant_id=other_tenant.id,
        account_name="Other Firm Pool",
    )
    db_session.add(other_bank_account)
    await db_session.commit()
    await db_session.refresh(other_bank_account)

    # Not visible in our tenant's list
    r = await client.get("/api/trust/bank-accounts")
    assert r.status_code == 200
    ids = [item["id"] for item in r.json()["items"]]
    assert str(other_bank_account.id) not in ids

    # Not gettable directly
    r = await client.get(f"/api/trust/bank-accounts/{other_bank_account.id}")
    assert r.status_code == 404

    # Not reconcilable
    r = await client.post(
        f"/api/trust/bank-accounts/{other_bank_account.id}/reconcile",
        json={"bank_balance": "0.00"},
    )
    assert r.status_code == 404
