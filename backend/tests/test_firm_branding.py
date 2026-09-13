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


async def test_put_branding_renames_the_tenant(client, db_session, test_tenant):
    r = await client.put("/api/firm/branding", json={"tenant_name": "Painter Law Group"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant_name"] == "Painter Law Group"
    # No firm_name override is set, so the renamed tenant is what the rest of
    # the app (portal emails, invoice PDFs, template fields) now renders.
    assert body["firm_name"] == "Painter Law Group"

    await db_session.refresh(test_tenant)
    assert test_tenant.name == "Painter Law Group"


async def test_firm_name_override_wins_over_tenant_name(client):
    r = await client.put(
        "/api/firm/branding",
        json={"tenant_name": "Acme", "firm_name": "Acme & Partners LLP"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant_name"] == "Acme"
    assert body["firm_name"] == "Acme & Partners LLP"


async def test_blank_firm_name_restores_the_tenant_fallback(client, test_tenant):
    await client.put("/api/firm/branding", json={"firm_name": "Temporary Name"})
    r = await client.put("/api/firm/branding", json={"firm_name": "   "})
    assert r.status_code == 200, r.text
    assert r.json()["firm_name"] == test_tenant.name


async def test_blank_tenant_name_is_rejected(client):
    r = await client.put("/api/firm/branding", json={"tenant_name": "  "})
    assert r.status_code == 422, r.text


async def test_currency_is_normalized_and_validated(client):
    ok = await client.put("/api/firm/branding", json={"firm_currency": "gbp"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["firm_currency"] == "GBP"

    bad = await client.put("/api/firm/branding", json={"firm_currency": "pounds"})
    assert bad.status_code == 422, bad.text


async def test_branding_exposes_tenant_domain(client, test_tenant):
    r = await client.get("/api/firm/branding")
    assert r.status_code == 200, r.text
    assert r.json()["tenant_domain"] == test_tenant.domain


async def test_overlong_values_are_rejected_before_the_database(client):
    # firm_name is String(300): a wider value used to reach Postgres and fail
    # there as a 500 rather than a 422 naming the field.
    r = await client.put("/api/firm/branding", json={"firm_name": "x" * 301})
    assert r.status_code == 422, r.text

    ok = await client.put("/api/firm/branding", json={"firm_name": "x" * 300})
    assert ok.status_code == 200, ok.text

    # The address and PDF footer are Text columns, so length is not capped.
    long_address = await client.put(
        "/api/firm/branding", json={"firm_address": "a" * 2000}
    )
    assert long_address.status_code == 200, long_address.text
