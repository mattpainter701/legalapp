"""Tests for firm branding API + branded PDF trust statement (Task 1303)."""

import uuid

import pytest

from app.models.plugin import Matter

pytestmark = pytest.mark.asyncio


def _make_matter(tenant_id, user_id, name="Branding Case") -> Matter:
    return Matter(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        slug=name.lower().replace(" ", "-"),
        matter_name=name,
        matter_type="general",
        status="open",
    )


async def test_get_branding_falls_back_to_tenant_name(client, test_tenant, test_user):
    r = await client.get("/api/firm/branding")
    assert r.status_code == 200, r.text
    data = r.json()
    # firm_name unset on TenantSettings → falls back to the tenant's name.
    assert data["firm_name"] == test_tenant.name


async def test_put_branding_persists(client, test_tenant, test_user):
    payload = {
        "firm_name": "Painter & Associates LLP",
        "firm_address": "123 Main St, Fargo, ND 58102",
        "firm_phone": "701-555-0100",
        "firm_pdf_footer": "Client trust funds held in accordance with IOLTA rules.",
    }
    r = await client.put("/api/firm/branding", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["firm_name"] == "Painter & Associates LLP"

    r2 = await client.get("/api/firm/branding")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["firm_name"] == "Painter & Associates LLP"
    assert body["firm_address"] == "123 Main St, Fargo, ND 58102"
    assert body["firm_phone"] == "701-555-0100"
    assert body["firm_pdf_footer"].startswith("Client trust funds")


async def test_ledger_statement_pdf_export(client, db_session, test_tenant, test_user):
    matter = _make_matter(test_tenant.id, test_user.id, name="PDF Statement Case")
    db_session.add(matter)
    await db_session.commit()

    acct = await client.post(
        "/api/trust/accounts",
        json={"matter_id": str(matter.id), "account_name": "PDF Trust Ledger"},
    )
    assert acct.status_code == 201, acct.text
    acct_id = acct.json()["id"]

    dep = await client.post(
        "/api/trust/transactions",
        json={
            "trust_account_id": acct_id,
            "transaction_type": "deposit",
            "amount": "1000.00",
            "description": "Initial retainer",
        },
    )
    assert dep.status_code == 201, dep.text

    dis = await client.post(
        "/api/trust/transactions",
        json={
            "trust_account_id": acct_id,
            "transaction_type": "disbursement",
            "amount": "250.00",
            "description": "Court filing fee",
        },
    )
    assert dis.status_code == 201, dis.text

    r = await client.get(f"/api/trust/accounts/{acct_id}/statement?format=pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content[:4] == b"%PDF"
