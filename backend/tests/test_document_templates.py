import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.routers import document_templates
from app.services.template_intake import analyze_template_upload


def test_render_template_preserves_unknown_variables():
    rendered = document_templates.render_template(
        "Dear {{ client_name }}, matter {{case_number}} remains {{unknown}}.",
        {"client_name": "Ada Lovelace", "case_number": "2026-CV-100"},
    )

    assert rendered == "Dear Ada Lovelace, matter 2026-CV-100 remains {{unknown}}."


def test_extract_template_variables_preserves_first_seen_order():
    variables = document_templates.extract_template_variables(
        "{{client_name}} {{ case_number }} {{client_name}} {{ attorney_email }}"
    )

    assert variables == ["client_name", "case_number", "attorney_email"]


def test_upload_analysis_detects_fields_and_letterhead_from_text():
    sample = b"""Painter Legal PLLC
123 Main Street
Fargo, ND 58102
(701) 555-0100

July 8, 2026

Dear Ada Lovelace,

Re: Estate Administration

Case No. PB-2026-10

Fee: $2,500.00
"""

    analysis = analyze_template_upload(
        file_bytes=sample,
        filename="fee-agreement.txt",
        content_type="text/plain",
    )

    field_names = {field["name"] for field in analysis.variable_schema["fields"]}
    assert "client_name" in field_names
    assert "matter_name" in field_names
    assert "case_number" in field_names
    assert "fee_amount" in field_names
    assert analysis.branding_profile["letterhead_detected"] is True
    assert "{{client_name}}" in analysis.body


def test_extract_schema_variables_when_body_has_no_placeholders():
    template = SimpleNamespace(
        variable_schema={
            "fields": [
                {"name": "client_name"},
                {"name": "case_number"},
                {"name": "client_name"},
            ]
        }
    )

    variables = document_templates.extract_schema_variables(template)

    assert variables == ["client_name", "case_number"]


@pytest.mark.asyncio
async def test_build_variable_suggestions_from_matter_client_and_current_user(
    monkeypatch,
):
    client_id = uuid.uuid4()
    attorney_id = uuid.uuid4()
    matter_id = uuid.uuid4()
    user_id = uuid.uuid4()

    matter = SimpleNamespace(
        id=matter_id,
        matter_name="Estate of Lovelace",
        matter_type="probate",
        description=None,
        status="open",
        stage="intake",
        jurisdiction="North Dakota",
        case_number="PB-2026-10",
        court="Cass County District Court",
        judge=None,
        billing_method="hourly",
        billing_cycle="monthly",
        hourly_rate=Decimal("250.00"),
        budget_amount=None,
        counterparty=None,
        client=SimpleNamespace(
            id=client_id,
            display_name="Ada Lovelace",
            email="ada@example.com",
            phone="555-0100",
            address={"city": "Fargo", "state": "ND", "zip": "58102"},
        ),
        attorney_of_record=SimpleNamespace(
            id=attorney_id,
            full_name="Grace Hopper",
            email="grace@example.com",
        ),
    )

    async def fake_load_matter_context(**kwargs):
        return matter

    monkeypatch.setattr(
        document_templates, "_load_matter_context", fake_load_matter_context
    )

    template = SimpleNamespace(
        id=uuid.uuid4(),
        body=(
            "{{client_name}} {{case_number}} {{attorney_email}} "
            "{{current_user_email}} {{missing_fact}}"
        ),
    )
    current_user = SimpleNamespace(
        id=user_id,
        full_name="Test Attorney",
        email="test@example.com",
    )

    _, suggestions = await document_templates.build_variable_suggestions(
        template=template,
        requested_variables=None,
        matter_id=str(matter_id),
        tenant_id=uuid.uuid4(),
        current_user=current_user,
        db=SimpleNamespace(),
    )

    by_variable = {item.variable: item for item in suggestions}

    assert by_variable["client_name"].suggested_value == "Ada Lovelace"
    assert by_variable["client_name"].source_type == "contact"
    assert by_variable["case_number"].suggested_value == "PB-2026-10"
    assert by_variable["attorney_email"].suggested_value == "grace@example.com"
    assert by_variable["current_user_email"].suggested_value == "test@example.com"
    assert by_variable["missing_fact"].suggested_value is None
    assert by_variable["missing_fact"].review_required is True
