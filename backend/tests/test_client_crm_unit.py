import io
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError

from app.models.contact import Contact
from app.models.operator_audit import OperatorAuditLog
from app.routers.clients import (
    MAX_CSV_BYTES,
    _csv_row_payload,
    _csv_safe,
    _parse_bool,
    _qbo_customer_payload,
    _validate_client_name,
    archive_client,
    client_matters,
    client_related_contacts,
    client_summary,
    create_client,
    import_clients_csv,
    list_clients,
    sync_client_quickbooks,
    update_client,
)
from app.schemas.client import ClientCreate, ClientUpdate


class FakeResult:
    def __init__(self, *, scalar=None, rows=None, one=None):
        self.scalar = scalar
        self.rows = rows or []
        self.row = one

    def scalar_one(self):
        return self.scalar

    def scalar_one_or_none(self):
        return self.scalar

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def one(self):
        return self.row


def fake_db(*execute_results, billing_tier=None):
    return SimpleNamespace(
        execute=AsyncMock(side_effect=execute_results),
        # Scalar reads are keyed separately so they do not consume an
        # ``execute`` side effect. The routers use one to read the tenant's
        # billing tier; None keeps the fake on the ordinary customer path.
        scalar=AsyncMock(return_value=billing_tier),
        add=MagicMock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )


def _qbo_request(*, plan="full-platform", billing_tier="payg"):
    return SimpleNamespace(
        state=SimpleNamespace(
            signed_plan=plan,
            signed_billing_tier=billing_tier,
            request_id="unit-request-id",
        ),
        headers={"x-idempotency-key": "unit-operation-id"},
        client=None,
    )


def test_client_schema_rejects_mass_assignment_and_requires_a_name():
    with pytest.raises(ValidationError):
        ClientCreate.model_validate(
            {
                "tenant_id": str(uuid.uuid4()),
                "first_name": "Attempted",
                "last_name": "Override",
            }
        )
    with pytest.raises(ValidationError):
        ClientCreate(entity_type="organization")

    assert ClientCreate(first_name="Jordan").entity_type == "person"
    assert (
        ClientCreate(
            entity_type="organization", organization_name="Northstar LLC"
        ).organization_name
        == "Northstar LLC"
    )


def test_client_csv_normalizes_aliases_and_consent():
    payload = _csv_row_payload(
        {
            "First_Name": " Jordan ",
            "Last_Name": " Rivera ",
            "Phone_1": "+1 701 555 0100",
            "DOB": "1985-04-12",
            "Client_Since": "2021-03-01",
            "SMS_Opt_In": "yes",
            "Address_City": "Fargo",
            "Preferred_Contact_Window": "Weekdays after 3 p.m.",
            "Preferred_Contact_Timezone": "America/Chicago",
        }
    )
    client = ClientCreate.model_validate(payload)

    assert client.phone == "+1 701 555 0100"
    assert client.date_of_birth.isoformat() == "1985-04-12"
    assert client.client_since.isoformat() == "2021-03-01"
    assert client.preferred_contact_window == "Weekdays after 3 p.m."
    assert client.preferred_contact_timezone == "America/Chicago"
    assert client.sms_opt_in is True
    assert client.address is not None
    assert client.address.city == "Fargo"

    extended = _csv_row_payload(
        {
            "organization_name": "Northstar LLC",
            "payment_terms_days": "45",
            "email_opt_in": "no",
            "tags": "priority; estate ",
            "emergency_contact_name": "Morgan",
        }
    )
    assert extended["entity_type"] == "organization"
    assert extended["payment_terms_days"] == 45
    assert extended["email_opt_in"] is False
    assert extended["tags"] == ["priority", "estate"]
    assert extended["emergency_contact"]["name"] == "Morgan"

    assert _parse_bool("0", "sms_opt_in") is False
    with pytest.raises(ValueError):
        _parse_bool("sometimes", "sms_opt_in")


def test_client_export_blocks_spreadsheet_formula_execution():
    assert _csv_safe('=HYPERLINK("https://example.com")').startswith("'=")
    assert _csv_safe("Jordan") == "Jordan"


def test_qbo_payload_excludes_internal_and_sensitive_fields():
    contact = Contact(
        tenant_id=uuid.uuid4(),
        contact_type="client",
        entity_type="person",
        first_name="Jordan",
        last_name="Rivera",
        email="jordan@example.com",
        phone="+1 701 555 0100",
        notes="Firm-only strategy",
        billing_notes="Do not share",
        emergency_contact={"name": "Alex Rivera"},
        address={"street": "12 Main St", "city": "Fargo", "state": "ND"},
    )

    payload = _qbo_customer_payload(contact)
    serialized = str(payload)

    assert payload["DisplayName"] == "Jordan Rivera"
    assert payload["PrimaryEmailAddr"]["Address"] == "jordan@example.com"
    assert payload["BillAddr"]["City"] == "Fargo"
    assert "Firm-only strategy" not in serialized
    assert "Do not share" not in serialized
    assert "Alex Rivera" not in serialized
    assert None not in payload["BillAddr"].values()

    organization = Contact(
        tenant_id=uuid.uuid4(),
        contact_type="client",
        entity_type="organization",
        organization_name="Northstar Legal",
    )
    assert _qbo_customer_payload(organization)["CompanyName"] == "Northstar Legal"


def test_client_name_validation_rejects_erased_names():
    with pytest.raises(HTTPException, match="organization_name"):
        _validate_client_name(
            SimpleNamespace(entity_type="organization", organization_name=None)
        )
    with pytest.raises(HTTPException, match="first_name"):
        _validate_client_name(
            SimpleNamespace(entity_type="person", first_name=None, last_name=None)
        )


@pytest.mark.asyncio
async def test_list_and_summary_cover_all_filters_and_sorts():
    tenant_id = uuid.uuid4()
    user = SimpleNamespace(tenant_id=tenant_id)
    db = fake_db(
        FakeResult(scalar=0),
        FakeResult(rows=[]),
        FakeResult(scalar=0),
        FakeResult(rows=[]),
        FakeResult(scalar=0),
        FakeResult(rows=[]),
        FakeResult(one=(6, 3, 1, 1, 1, 2)),
    )
    with patch(
        "app.routers.clients.set_tenant_context", new_callable=AsyncMock
    ) as tenant_context:
        filtered = await list_clients(
            q="Rivera",
            status="active",
            entity_type="person",
            sms_opt_in=True,
            active_only=True,
            sort="recently-contacted",
            limit=25,
            offset=0,
            current_user=user,
            db=db,
        )
        newest = await list_clients(
            active_only=False,
            sort="newest",
            limit=25,
            offset=0,
            current_user=user,
            db=db,
        )
        alphabetical = await list_clients(
            active_only=False,
            sort="name",
            limit=25,
            offset=0,
            current_user=user,
            db=db,
        )
        summary = await client_summary(current_user=user, db=db)

    assert filtered.total == newest.total == alphabetical.total == 0
    assert summary.model_dump() == {
        "total": 6,
        "active": 3,
        "prospects": 1,
        "inactive": 1,
        "former": 1,
        "sms_opted_in": 2,
    }
    assert tenant_context.await_count == 4


@pytest.mark.asyncio
async def test_create_update_archive_and_linked_matters():
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    client_id = uuid.uuid4()
    admin = SimpleNamespace(tenant_id=tenant_id, id=user_id, role="admin")
    db = fake_db(FakeResult(rows=[]))

    with (
        patch("app.routers.clients.set_tenant_context", new_callable=AsyncMock),
        patch(
            "app.routers.clients._ensure_unique_client_number",
            new_callable=AsyncMock,
        ) as unique_number,
        patch("app.routers.clients._client_response", return_value={"saved": True}),
    ):
        created = await create_client(
            ClientCreate(
                first_name="Jordan",
                client_number="CL-1",
                sms_opt_in=True,
                qbo_customer_id="123",
            ),
            current_user=admin,
            db=db,
        )
    assert created == {"saved": True}
    assert db.add.call_count == 1
    unique_number.assert_awaited_once()

    non_finance = SimpleNamespace(tenant_id=tenant_id, id=user_id, role="attorney")
    with (
        patch("app.routers.clients.set_tenant_context", new_callable=AsyncMock),
        pytest.raises(HTTPException, match="finance access"),
    ):
        await create_client(
            ClientCreate(first_name="Jordan", qbo_customer_id="blocked"),
            current_user=non_finance,
            db=db,
        )

    contact = SimpleNamespace(
        id=client_id,
        entity_type="person",
        first_name="Jordan",
        last_name="Rivera",
        client_status="active",
        contact_type="client",
        sms_opt_in=False,
        sms_opt_in_at=None,
        is_active=True,
    )
    with (
        patch("app.routers.clients.set_tenant_context", new_callable=AsyncMock),
        patch(
            "app.routers.clients._load_client",
            new_callable=AsyncMock,
            return_value=contact,
        ),
        patch(
            "app.routers.clients._ensure_unique_client_number",
            new_callable=AsyncMock,
        ) as unique_update,
        patch("app.routers.clients._client_response", return_value={"updated": True}),
    ):
        updated = await update_client(
            client_id,
            ClientUpdate(
                client_number="CL-2",
                sms_opt_in=True,
                client_status="prospect",
                qbo_customer_id="456",
            ),
            current_user=admin,
            db=db,
        )
    assert updated == {"updated": True}
    assert contact.contact_type == "prospect"
    assert contact.sms_opt_in_at is not None
    unique_update.assert_awaited_once()

    matter = SimpleNamespace(
        id=uuid.uuid4(),
        matter_name="Estate plan",
        matter_type="estate",
        status="active",
        jurisdiction="ND",
        created_at=datetime.now(timezone.utc),
    )
    matter_db = fake_db(FakeResult(rows=[matter]))
    with (
        patch("app.routers.clients.set_tenant_context", new_callable=AsyncMock),
        patch(
            "app.routers.clients._load_client",
            new_callable=AsyncMock,
            return_value=contact,
        ),
    ):
        linked = await client_matters(client_id, current_user=admin, db=matter_db)
        await archive_client(client_id, admin=admin, db=matter_db)
    assert linked[0]["matter_name"] == "Estate plan"
    assert contact.is_active is False
    assert contact.client_status == "inactive"


@pytest.mark.asyncio
async def test_related_contacts_stay_under_the_canonical_client_account():
    tenant_id = uuid.uuid4()
    client_id = uuid.uuid4()
    user = SimpleNamespace(tenant_id=tenant_id)
    related = Contact(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        entity_type="person",
        contact_type="client_contact",
        client_account_id=client_id,
        first_name="Avery",
        last_name="Nguyen",
        email="avery@example.invalid",
        client_contact_role="Chief Operating Officer",
        is_primary_client_contact=True,
        client_contact_authorization="Authorized for routine instructions.",
    )
    db = fake_db(FakeResult(rows=[related]))
    with (
        patch("app.routers.clients.set_tenant_context", new_callable=AsyncMock),
        patch(
            "app.routers.clients._load_client",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(id=client_id),
        ),
    ):
        result = await client_related_contacts(client_id, current_user=user, db=db)

    assert [contact.display_name for contact in result] == ["Avery Nguyen"]
    assert result[0].client_contact_role == "Chief Operating Officer"
    assert result[0].is_primary_client_contact is True


@pytest.mark.asyncio
async def test_import_rejects_unsafe_files_before_database_writes():
    admin = SimpleNamespace(tenant_id=uuid.uuid4(), id=uuid.uuid4())
    db = fake_db()

    with pytest.raises(HTTPException, match=".csv"):
        await import_clients_csv(
            UploadFile(filename="clients.txt", file=io.BytesIO(b"name")),
            admin=admin,
            db=db,
        )
    with pytest.raises(HTTPException, match="5 MB"):
        await import_clients_csv(
            UploadFile(
                filename="clients.csv", file=io.BytesIO(b"x" * (MAX_CSV_BYTES + 1))
            ),
            admin=admin,
            db=db,
        )
    with pytest.raises(HTTPException, match="UTF-8"):
        await import_clients_csv(
            UploadFile(filename="clients.csv", file=io.BytesIO(b"\xff\xfe")),
            admin=admin,
            db=db,
        )
    with (
        patch("app.routers.clients.set_tenant_context", new_callable=AsyncMock),
        pytest.raises(HTTPException, match="header row"),
    ):
        await import_clients_csv(
            UploadFile(filename="clients.csv", file=io.BytesIO(b"")),
            admin=admin,
            db=db,
        )


@pytest.mark.asyncio
async def test_quickbooks_sync_updates_provider_mapping_without_sensitive_fields():
    tenant_id = uuid.uuid4()
    client_id = uuid.uuid4()
    admin = SimpleNamespace(tenant_id=tenant_id, id=uuid.uuid4(), role="admin")
    contact = Contact(
        id=client_id,
        tenant_id=tenant_id,
        contact_type="client",
        entity_type="person",
        first_name="Jordan",
        last_name="Rivera",
        qbo_customer_id="123",
        notes="private",
    )
    db = fake_db()
    service = MagicMock()
    service._api_url.side_effect = lambda *parts: "/".join(parts)
    service._safe_qbo_string.return_value = "Jordan Rivera"
    service._request = AsyncMock(
        side_effect=[
            {"Customer": {"Id": "123", "SyncToken": "2"}},
            {"Customer": {"Id": "123", "SyncToken": "3"}},
        ]
    )
    integration = SimpleNamespace(qbo_realm_id="realm-1", sandbox_mode=True)

    with (
        patch("app.routers.clients.set_tenant_context", new_callable=AsyncMock),
        patch(
            "app.routers.clients._load_client",
            new_callable=AsyncMock,
            return_value=contact,
        ),
        patch(
            "app.routers.qbo._get_fresh_qbo_token",
            new_callable=AsyncMock,
            return_value="access-token",
        ),
        patch(
            "app.routers.qbo._get_qbo_integration",
            new_callable=AsyncMock,
            return_value=integration,
        ),
        patch("app.services.qbo_sync.QBOSyncService", return_value=service),
    ):
        result = await sync_client_quickbooks(
            client_id, request=_qbo_request(), admin=admin, db=db
        )

    assert result.qbo_customer_id == "123"
    assert contact.qbo_sync_token == "3"
    posted_payload = service._request.await_args_list[-1].kwargs["json_data"]
    assert posted_payload["sparse"] is True
    assert "private" not in str(posted_payload)

    with (
        patch("app.routers.clients.set_tenant_context", new_callable=AsyncMock),
        patch(
            "app.routers.clients._load_client",
            new_callable=AsyncMock,
            return_value=contact,
        ),
        patch(
            "app.routers.qbo._get_fresh_qbo_token",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.routers.qbo._get_qbo_integration",
            new_callable=AsyncMock,
            return_value=integration,
        ),
        pytest.raises(HTTPException, match="not connected"),
    ):
        await sync_client_quickbooks(
            client_id, request=_qbo_request(), admin=admin, db=db
        )


@pytest.mark.asyncio
async def test_quickbooks_sync_simulates_for_a_demo_tenant_without_provider_lookups():
    """Called directly so the demo branch is measured, not just exercised.

    tests/test_clients_crm.py drives this over HTTP; this covers the same
    branch at the unit level and pins the simulated payload.
    """
    tenant_id = uuid.uuid4()
    client_id = uuid.uuid4()
    admin = SimpleNamespace(tenant_id=tenant_id, id=uuid.uuid4(), role="admin")
    contact = Contact(
        id=client_id,
        tenant_id=tenant_id,
        contact_type="client",
        entity_type="person",
        first_name="Sky",
        last_name="Nolan",
    )
    db = fake_db(billing_tier="demo")

    def _fail(*args, **kwargs):
        raise AssertionError("demo tenant attempted a QuickBooks provider call")

    with (
        patch("app.routers.clients.set_tenant_context", new_callable=AsyncMock),
        patch(
            "app.routers.clients._load_client",
            new_callable=AsyncMock,
            return_value=contact,
        ),
        patch("app.routers.qbo._get_fresh_qbo_token", _fail),
        patch("app.routers.qbo._get_qbo_integration", _fail),
    ):
        result = await sync_client_quickbooks(
            client_id,
            request=_qbo_request(plan="demo", billing_tier="demo"),
            admin=admin,
            db=db,
        )

    assert result.status == "demo_simulated"
    assert result.is_simulated is True
    assert result.qbo_customer_id == f"DEMO-{client_id.hex.upper()}"
    assert "QuickBooks was not contacted" in result.detail

    assert contact.qbo_customer_id is None
    assert contact.qbo_sync_token is None
    assert contact.qbo_synced_at is None

    audit = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], OperatorAuditLog)
    )
    assert audit.action == "client.quickbooks_sync_simulated"
    assert audit.metadata_json["provider_contacted"] is False
    assert audit.metadata_json["simulated_customer_id"] == result.qbo_customer_id
    assert audit.metadata_json["operation_id"] == "unit-operation-id"


@pytest.mark.asyncio
async def test_quickbooks_sync_fails_closed_when_signed_claims_disagree():
    tenant_id = uuid.uuid4()
    client_id = uuid.uuid4()
    admin = SimpleNamespace(tenant_id=tenant_id, id=uuid.uuid4(), role="admin")
    contact = Contact(
        id=client_id,
        tenant_id=tenant_id,
        contact_type="client",
        entity_type="person",
        first_name="Sky",
        last_name="Nolan",
    )
    db = fake_db(billing_tier="payg")

    def _fail(*args, **kwargs):
        raise AssertionError("claim mismatch reached a QuickBooks provider call")

    with (
        patch("app.routers.clients.set_tenant_context", new_callable=AsyncMock),
        patch(
            "app.routers.clients._load_client",
            new_callable=AsyncMock,
            return_value=contact,
        ),
        patch("app.routers.qbo._get_fresh_qbo_token", _fail),
        patch("app.routers.qbo._get_qbo_integration", _fail),
        pytest.raises(HTTPException) as exc_info,
    ):
        await sync_client_quickbooks(
            client_id,
            request=_qbo_request(plan="demo", billing_tier="demo"),
            admin=admin,
            db=db,
        )

    assert exc_info.value.status_code == 403
    assert "sign in again" in exc_info.value.detail
    audit = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], OperatorAuditLog)
    )
    assert audit.action == "client.quickbooks_sync_denied"
    assert audit.metadata_json["reason"] == "signed_claim_mismatch"
