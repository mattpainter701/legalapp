import uuid

import pytest
from pydantic import ValidationError

from app.models.contact import Contact
from app.routers.clients import _csv_row_payload, _csv_safe, _qbo_customer_payload
from app.schemas.client import ClientCreate


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


def test_client_csv_normalizes_aliases_and_consent():
    payload = _csv_row_payload(
        {
            "First_Name": " Jordan ",
            "Last_Name": " Rivera ",
            "Phone_1": "+1 701 555 0100",
            "DOB": "1985-04-12",
            "SMS_Opt_In": "yes",
            "Address_City": "Fargo",
        }
    )
    client = ClientCreate.model_validate(payload)

    assert client.phone == "+1 701 555 0100"
    assert client.date_of_birth.isoformat() == "1985-04-12"
    assert client.sms_opt_in is True
    assert client.address is not None
    assert client.address.city == "Fargo"


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
