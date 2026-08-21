import csv
import io
import uuid

import pytest
from sqlalchemy import select

from app.models.contact import Contact
from app.models.tenant import Tenant


@pytest.mark.asyncio
async def test_client_crm_create_update_summary_and_consent(client, db_session):
    response = await client.post(
        "/api/clients",
        json={
            "client_number": "CL-1042",
            "first_name": "Jordan",
            "last_name": "Rivera",
            "preferred_name": "Jordy",
            "date_of_birth": "1985-04-12",
            "email": "jordan@example.com",
            "phone": "+1 701 555 0100",
            "secondary_phone": "+1 701 555 0101",
            "address": {
                "street": "12 Main St",
                "city": "Fargo",
                "state": "ND",
                "zip": "58102",
                "country": "US",
            },
            "emergency_contact": {
                "name": "Alex Rivera",
                "relationship": "Spouse",
                "phone": "+1 701 555 0199",
            },
            "preferred_contact_method": "sms",
            "sms_opt_in": True,
            "preferred_payment_method": "check",
            "billing_delivery_method": "portal",
            "payment_terms_days": 15,
            "notes": "Internal representation note",
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["display_name"] == "Jordan Rivera"
    assert created["client_status"] == "active"
    assert created["sms_opt_in"] is True
    assert created["sms_opt_in_at"] is not None
    assert created["emergency_contact"]["name"] == "Alex Rivera"

    updated = await client.patch(
        f"/api/clients/{created['id']}",
        json={"client_status": "former", "sms_opt_in": False},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["contact_type"] == "client"
    assert updated.json()["sms_opt_in_at"] is None

    summary = await client.get("/api/clients/summary")
    assert summary.status_code == 200
    assert summary.json() == {
        "total": 1,
        "active": 0,
        "prospects": 0,
        "inactive": 0,
        "former": 1,
        "sms_opted_in": 0,
    }


@pytest.mark.asyncio
async def test_client_number_is_unique_within_tenant(client):
    payload = {"client_number": "CL-7", "first_name": "First", "last_name": "Client"}
    first = await client.post("/api/clients", json=payload)
    assert first.status_code == 201
    duplicate = await client.post(
        "/api/clients",
        json={"client_number": "CL-7", "first_name": "Second", "last_name": "Client"},
    )
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_client_payload_cannot_override_tenant(client):
    response = await client.post(
        "/api/clients",
        json={
            "tenant_id": str(uuid.uuid4()),
            "first_name": "Mallory",
            "last_name": "Tenant Override",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_client_routes_fail_closed_across_tenants(
    client, db_session, test_tenant
):
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other Firm",
        domain="other-client-firm.example",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(other_tenant)
    await db_session.flush()
    other_client = Contact(
        tenant_id=other_tenant.id,
        contact_type="client",
        client_status="active",
        first_name="Private",
        last_name="Client",
    )
    db_session.add(other_client)
    await db_session.commit()

    response = await client.get(f"/api/clients/{other_client.id}")
    assert response.status_code == 404
    listed = await client.get("/api/clients")
    assert listed.status_code == 200
    assert all(
        item["tenant_id"] == str(test_tenant.id) for item in listed.json()["items"]
    )


@pytest.mark.asyncio
async def test_client_csv_import_export_and_formula_hardening(client, db_session):
    csv_body = io.StringIO()
    writer = csv.DictWriter(
        csv_body,
        fieldnames=[
            "client_number",
            "first_name",
            "last_name",
            "email",
            "sms_opt_in",
            "preferred_payment_method",
            "internal_notes",
        ],
    )
    writer.writeheader()
    writer.writerow(
        {
            "client_number": "CL-CSV-1",
            "first_name": '=HYPERLINK("https://example.invalid")',
            "last_name": "Imported",
            "email": "imported@example.com",
            "sms_opt_in": "yes",
            "preferred_payment_method": "ach",
            "internal_notes": "Imported securely",
        }
    )
    imported = await client.post(
        "/api/clients/import.csv",
        files={"file": ("clients.csv", csv_body.getvalue(), "text/csv")},
        data={"update_existing": "false"},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json() == {"created": 1, "updated": 0, "skipped": 0, "errors": []}

    stored = (
        await db_session.execute(
            select(Contact).where(Contact.client_number == "CL-CSV-1")
        )
    ).scalar_one()
    assert stored.sms_opt_in is True
    assert stored.notes == "Imported securely"

    exported = await client.get("/api/clients/export.csv")
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(exported.text)))
    assert rows[0]["first_name"].startswith("'=")
    assert rows[0]["qbo_customer_id"] == ""


@pytest.mark.asyncio
async def test_client_import_updates_existing_by_email(client):
    created = await client.post(
        "/api/clients",
        json={
            "first_name": "Taylor",
            "last_name": "Old",
            "email": "taylor@example.com",
        },
    )
    assert created.status_code == 201
    csv_text = "first_name,last_name,email,preferred_language\nTaylor,New,taylor@example.com,Spanish\n"
    response = await client.post(
        "/api/clients/import.csv",
        files={"file": ("clients.csv", csv_text, "text/csv")},
        data={"update_existing": "true"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["updated"] == 1
    detail = await client.get(f"/api/clients/{created.json()['id']}")
    assert detail.json()["last_name"] == "New"
    assert detail.json()["preferred_language"] == "Spanish"


@pytest.mark.asyncio
async def test_client_external_ids_require_finance_access(
    client, db_session, test_user
):
    test_user.role = "attorney"
    await db_session.commit()
    response = await client.post(
        "/api/clients",
        json={
            "first_name": "Sam",
            "last_name": "Non Finance",
            "qbo_customer_id": "123",
        },
    )
    assert response.status_code == 403
