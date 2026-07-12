import uuid
from io import BytesIO
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.routers import document_templates
from app.services.template_intake import analyze_template_upload
from app.services.pdf_templates import (
    TemplatePdfError,
    discover_pdf_fields,
    fill_pdf_template,
)


def _fillable_pdf() -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    pdf.drawString(72, 720, "Client:")
    pdf.acroForm.textfield(name="Client Name", x=120, y=705, width=250, height=24)
    pdf.drawString(72, 680, "Approved:")
    pdf.acroForm.checkbox(name="Approved", x=130, y=670, size=15, fieldFlags="")
    pdf.drawString(72, 645, "Notes:")
    pdf.acroForm.textfield(
        name="Notes",
        x=120,
        y=585,
        width=250,
        height=50,
        fieldFlags="multiline",
    )
    pdf.save()
    return output.getvalue()


def _special_fields_pdf() -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    pdf.drawString(72, 720, "Forum:")
    pdf.acroForm.choice(
        name="forum",
        value="State",
        options=[("State Court", "State"), ("Federal Court", "Federal")],
        x=130,
        y=705,
        width=180,
        height=24,
    )
    pdf.drawString(72, 670, "Filing type:")
    pdf.acroForm.radio(
        name="filing_type", value="Complaint", selected=True, x=150, y=660
    )
    pdf.acroForm.radio(name="filing_type", value="Motion", selected=False, x=270, y=660)
    pdf.save()
    return output.getvalue()


def _required_pdf() -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    pdf.drawString(72, 740, "Required client review")
    pdf.acroForm.textfield(
        name="client_name",
        x=120,
        y=705,
        width=250,
        height=24,
        fieldFlags="required",
    )
    pdf.acroForm.checkbox(name="accepted", x=120, y=670, size=15, fieldFlags="required")
    pdf.save()
    return output.getvalue()


async def _grant_manage_documents(db_session, test_tenant, test_user) -> None:
    from app.models.rbac import Role, UserRole

    role = Role(
        tenant_id=test_tenant.id,
        name="Document managers",
        capabilities=["manage_documents"],
    )
    db_session.add(role)
    await db_session.flush()
    db_session.add(
        UserRole(
            user_id=test_user.id,
            role_id=role.id,
            tenant_id=test_tenant.id,
            source="manual",
        )
    )
    await db_session.commit()


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


def test_pdf_analysis_discovers_acroform_fields_without_losing_mapping():
    analysis = analyze_template_upload(
        file_bytes=_fillable_pdf(),
        filename="client-intake.pdf",
        content_type="application/pdf",
    )
    fields = {field["name"]: field for field in analysis.variable_schema["fields"]}
    assert fields["client_name"]["pdf_field_name"] == "Client Name"
    assert fields["approved"]["field_type"] == "checkbox"
    assert fields["notes"]["multiline"] is True
    assert "{{client_name}}" in analysis.body
    assert analysis.format == "pdf"


def test_pdf_discovery_and_fill_support_radio_choice_pairs_and_hierarchy():
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DictionaryObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )

    source = _special_fields_pdf()
    fields = {field["name"]: field for field in discover_pdf_fields(source)}
    assert fields["filing_type"]["field_type"] == "radio"
    assert fields["filing_type"]["options"] == ["Complaint", "Motion"]
    assert fields["forum"]["options"] == [
        {"value": "State", "label": "State Court"},
        {"value": "Federal", "label": "Federal Court"},
    ]

    schema = {"fields": list(fields.values())}
    flattened = fill_pdf_template(
        source,
        variable_schema=schema,
        variables={"forum": "Federal", "filing_type": "Motion"},
        flatten=True,
    )
    flattened_reader = PdfReader(BytesIO(flattened))
    assert "Federal Court" in (flattened_reader.pages[0].extract_text() or "")
    assert flattened_reader.get_fields() is None

    editable = fill_pdf_template(
        source,
        variable_schema=schema,
        variables={"forum": "Federal", "filing_type": "Motion"},
        flatten=False,
    )
    editable_fields = PdfReader(BytesIO(editable)).get_fields()
    assert str(editable_fields["forum"]["/V"]) == "Federal"
    assert str(editable_fields["filing_type"]["/V"]) == "/Motion"

    base = BytesIO()
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(base, pagesize=letter)
    pdf.drawString(72, 740, "Party")
    pdf.acroForm.textfield(name="name", x=150, y=705, width=240, height=24)
    pdf.save()
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(BytesIO(base.getvalue())))
    acroform = writer._root_object["/AcroForm"].get_object()
    child_ref = acroform["/Fields"][0]
    child = child_ref.get_object()
    parent = DictionaryObject(
        {
            NameObject("/T"): TextStringObject("party"),
            NameObject("/Kids"): ArrayObject([child_ref]),
        }
    )
    parent_ref = writer._add_object(parent)
    child[NameObject("/Parent")] = parent_ref
    acroform[NameObject("/Fields")] = ArrayObject([parent_ref])
    hierarchical = BytesIO()
    writer.write(hierarchical)
    hierarchical_fields = discover_pdf_fields(hierarchical.getvalue())
    assert [field["pdf_field_name"] for field in hierarchical_fields] == ["party.name"]

    push_writer = PdfWriter()
    push_writer.clone_document_from_reader(PdfReader(BytesIO(_fillable_pdf())))
    push_field = next(
        annotation.get_object()
        for page in push_writer.pages
        for annotation in (page.get("/Annots") or [])
        if str(annotation.get_object().get("/T")) == "Approved"
    )
    push_field[NameObject("/Ff")] = NumberObject(
        int(push_field.get("/Ff", 0) or 0) | (1 << 16)
    )
    push_source = BytesIO()
    push_writer.write(push_source)
    push_names = {
        field["pdf_field_name"] for field in discover_pdf_fields(push_source.getvalue())
    }
    assert "Approved" not in push_names


def test_pdf_fill_and_flatten_preserves_page_and_removes_form_widgets():
    from pypdf import PdfReader

    source = _fillable_pdf()
    schema = {"fields": discover_pdf_fields(source)}
    output = fill_pdf_template(
        source,
        variable_schema=schema,
        variables={"client_name": "Ada Lovelace", "approved": "yes"},
        flatten=True,
    )
    reader = PdfReader(BytesIO(output))
    assert len(reader.pages) == 1
    assert reader.get_fields() is None
    text = reader.pages[0].extract_text()
    for source_label in ("Client:", "Approved:", "Notes:"):
        assert source_label in text
    assert "Ada Lovelace" in text
    assert "X" in text


def test_pdf_parser_rejects_non_pdf_and_encrypted_pdf():
    import pytest
    from pypdf import PdfWriter

    with pytest.raises(TemplatePdfError, match="not a valid PDF"):
        discover_pdf_fields(b"not-pdf")

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("secret")
    encrypted = BytesIO()
    writer.write(encrypted)
    with pytest.raises(TemplatePdfError, match="Password-protected"):
        discover_pdf_fields(encrypted.getvalue())


def test_pdf_parser_rejects_javascript_active_content():
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(BytesIO(_fillable_pdf())))
    writer.add_js("app.alert('unsafe')")
    active = BytesIO()
    writer.write(active)

    with pytest.raises(TemplatePdfError, match="Active PDF content"):
        discover_pdf_fields(active.getvalue())


def test_pdf_render_fails_instead_of_corrupting_unsupported_glyphs():
    source = _fillable_pdf()
    schema = {"fields": discover_pdf_fields(source)}
    with pytest.raises(TemplatePdfError, match="cannot safely display"):
        fill_pdf_template(
            source,
            variable_schema=schema,
            variables={"client_name": "Client \U0001f4bc"},
            flatten=True,
        )

    for unsupported_text in ("مرحبا", "नमस्ते", "שלום"):
        with pytest.raises(TemplatePdfError, match="cannot safely shape"):
            fill_pdf_template(
                source,
                variable_schema=schema,
                variables={"client_name": unsupported_text},
                flatten=True,
            )


def test_pdf_final_render_requires_explicit_review_and_true_required_checkbox():
    source = _required_pdf()
    schema = {"fields": discover_pdf_fields(source)}

    # Preview is intentionally permissive so the user can inspect incomplete work.
    fill_pdf_template(
        source,
        variable_schema=schema,
        variables={},
        flatten=True,
        enforce_required=False,
    )
    with pytest.raises(TemplatePdfError, match="Review every PDF field"):
        fill_pdf_template(
            source,
            variable_schema=schema,
            variables={"client_name": "Ada"},
            flatten=True,
            enforce_required=True,
        )
    with pytest.raises(TemplatePdfError, match="empty or unchecked"):
        fill_pdf_template(
            source,
            variable_schema=schema,
            variables={"client_name": "Ada", "accepted": "false"},
            flatten=True,
            enforce_required=True,
        )
    fill_pdf_template(
        source,
        variable_schema=schema,
        variables={"client_name": "Ada", "accepted": "true"},
        flatten=True,
        enforce_required=True,
    )
    with pytest.raises(TemplatePdfError, match="cannot safely display"):
        fill_pdf_template(
            source,
            variable_schema=schema,
            variables={"client_name": "Client \U0001f4bc"},
            flatten=False,
        )


def test_pdf_final_render_clears_sample_values_and_leaves_signature_blank():
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, NumberObject

    source = _fillable_pdf()
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(BytesIO(source)))
    for page in writer.pages:
        writer.update_page_form_field_values(
            page,
            {
                "Client Name": "SAMPLE CLIENT SECRET",
                "Approved": "/Yes",
                "Notes": "sample privileged narrative",
            },
            auto_regenerate=True,
        )
    populated = BytesIO()
    writer.write(populated)
    populated_source = populated.getvalue()
    populated_schema = {"fields": discover_pdf_fields(populated_source)}
    with pytest.raises(TemplatePdfError, match="Review every PDF field"):
        fill_pdf_template(
            populated_source,
            variable_schema=populated_schema,
            variables={"client_name": "Final Client", "approved": "false"},
            flatten=True,
            enforce_required=True,
        )
    cleared = fill_pdf_template(
        populated_source,
        variable_schema=populated_schema,
        variables={
            "client_name": "Final Client",
            "approved": "false",
            "notes": "",
        },
        flatten=True,
        enforce_required=True,
    )
    cleared_text = PdfReader(BytesIO(cleared)).pages[0].extract_text() or ""
    assert "Final Client" in cleared_text
    assert "SAMPLE CLIENT SECRET" not in cleared_text
    assert "sample privileged narrative" not in cleared_text

    signature_writer = PdfWriter()
    signature_writer.clone_document_from_reader(PdfReader(BytesIO(source)))
    signature_field = next(
        annotation.get_object()
        for page in signature_writer.pages
        for annotation in (page.get("/Annots") or [])
        if str(annotation.get_object().get("/T")) == "Client Name"
    )
    signature_field[NameObject("/FT")] = NameObject("/Sig")
    signature_field[NameObject("/Ff")] = NumberObject(2)
    signature_source = BytesIO()
    signature_writer.write(signature_source)
    signature_schema = {"fields": discover_pdf_fields(signature_source.getvalue())}
    signature_meta = {field["name"]: field for field in signature_schema["fields"]}
    assert signature_meta["client_name"]["field_type"] == "signature"
    signed_later = fill_pdf_template(
        signature_source.getvalue(),
        variable_schema=signature_schema,
        variables={"approved": "false", "notes": ""},
        flatten=True,
        enforce_required=True,
    )
    signed_later_reader = PdfReader(BytesIO(signed_later))
    assert signed_later_reader.get_fields() is None
    assert "SAMPLE CLIENT SECRET" not in (
        signed_later_reader.pages[0].extract_text() or ""
    )


def test_pdf_text_extraction_honors_page_and_character_caps():
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    from app.utils.text_processing import extract_text_from_pdf

    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    for page_number in range(1, 4):
        pdf.drawString(72, 720, f"PAGE-{page_number} " + "x" * 50)
        pdf.showPage()
    pdf.save()

    first_two = extract_text_from_pdf(output.getvalue(), max_pages=2)
    assert "PAGE-1" in first_two
    assert "PAGE-2" in first_two
    assert "PAGE-3" not in first_two
    assert len(extract_text_from_pdf(output.getvalue(), max_chars=25)) <= 25


def test_pdf_intake_analysis_does_not_extract_past_page_cap():
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    for page_number in range(1, 56):
        pdf.drawString(72, 720, f"INTAKE-PAGE-{page_number:02d}")
        pdf.showPage()
    pdf.save()

    analysis = analyze_template_upload(
        file_bytes=output.getvalue(),
        filename="large.pdf",
        content_type="application/pdf",
    )
    assert "INTAKE-PAGE-50" in analysis.extracted_text
    assert "INTAKE-PAGE-51" not in analysis.extracted_text


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


@pytest.mark.asyncio
async def test_pdf_upload_preview_and_matter_render_are_binary_and_unique(
    client, db_session, test_tenant, test_user, tmp_path, monkeypatch
):
    from pathlib import Path

    from pypdf import PdfReader
    from sqlalchemy import select

    from app.models.document_template import DocumentTemplate
    from app.models.matter_document import MatterDocument
    from app.models.plugin import Matter, MatterEvent
    from app.services import matter_file_store as matter_store_module

    monkeypatch.setattr(document_templates.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(matter_store_module.settings, "UPLOAD_DIR", str(tmp_path))
    await _grant_manage_documents(db_session, test_tenant, test_user)

    response = await client.post(
        "/api/templates/intake/create",
        files={"file": ("client-intake.pdf", _fillable_pdf(), "application/pdf")},
        data={"title": "../../Client Intake", "category": "other"},
    )
    assert response.status_code == 201, response.text
    template_id = response.json()["id"]
    assert response.json()["format"] == "pdf"
    assert response.json()["source_filename"] == "client-intake.pdf"
    assert "source_storage_path" not in response.json()

    template = await db_session.scalar(
        select(DocumentTemplate).where(DocumentTemplate.id == uuid.UUID(template_id))
    )
    source_path = Path(template.source_storage_path).resolve()
    assert source_path.is_relative_to(tmp_path.resolve())
    import hashlib

    assert (
        hashlib.sha256(source_path.read_bytes()).hexdigest()
        == response.json()["source_sha256"]
    )
    source_response = await client.get(f"/api/templates/{template_id}/source")
    assert source_response.status_code == 200
    assert source_response.headers["cache-control"] == "private, no-store"
    assert hashlib.sha256(source_response.content).hexdigest() == template.source_sha256

    preview = await client.post(
        f"/api/templates/{template_id}/render-file",
        json={"variables": {"client_name": "Ada Lovelace", "approved": "yes"}},
    )
    assert preview.status_code == 200, preview.text
    assert preview.headers["content-type"].startswith("application/pdf")
    preview_reader = PdfReader(BytesIO(preview.content))
    assert preview_reader.get_fields() is None
    preview_text = preview_reader.pages[0].extract_text()
    assert "Client:" in preview_text
    assert "Ada Lovelace" in preview_text

    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug="pdf-incident-matter",
        matter_name="PDF Incident Matter",
        matter_type="general",
    )
    db_session.add(matter)
    await db_session.commit()

    inactive_save = await client.post(
        f"/api/templates/{template_id}/render",
        json={
            "variables": {
                "client_name": "Ada Lovelace",
                "approved": "yes",
                "notes": "",
            },
            "matter_id": str(matter.id),
        },
    )
    assert inactive_save.status_code == 409

    activated = await client.patch(
        f"/api/templates/{template_id}", json={"is_active": True}
    )
    assert activated.status_code == 200, activated.text

    filenames = []
    for _ in range(2):
        saved = await client.post(
            f"/api/templates/{template_id}/render",
            json={
                "variables": {
                    "client_name": "Ada Lovelace",
                    "approved": "yes",
                    "notes": "",
                },
                "matter_id": str(matter.id),
            },
        )
        assert saved.status_code == 200, saved.text
        payload = saved.json()
        assert payload["output_format"] == "pdf"
        assert payload["output_filename"].endswith(".pdf")
        assert payload["download_url"].endswith("/download")
        assert payload["storage_backend"] == "local"
        assert payload.get("storage_warning") is None
        filenames.append(payload["output_filename"])
    assert filenames[0] != filenames[1]

    documents = list(
        (
            await db_session.scalars(
                select(MatterDocument).where(MatterDocument.matter_id == matter.id)
            )
        ).all()
    )
    assert len(documents) == 2
    assert documents[0].storage_path != documents[1].storage_path
    assert all(Path(document.storage_path).is_file() for document in documents)
    events = list(
        (
            await db_session.scalars(
                select(MatterEvent).where(
                    MatterEvent.matter_id == matter.id,
                    MatterEvent.event_type == "document_generated",
                )
            )
        ).all()
    )
    assert len(events) == 2
    assert events[0].metadata_json["template_source_sha256"] == template.source_sha256
    assert events[0].metadata_json["filled_variables"] == ["approved", "client_name"]
    assert "Ada Lovelace" not in str(events[0].metadata_json)


@pytest.mark.asyncio
async def test_pdf_intake_rejects_empty_non_form_and_active_documents(
    client, db_session, test_tenant, test_user
):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from pypdf import PdfReader, PdfWriter

    await _grant_manage_documents(db_session, test_tenant, test_user)
    empty = await client.post(
        "/api/templates/intake/analyze",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert empty.status_code == 400

    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    pdf.drawString(72, 720, "Static agreement without fields")
    pdf.save()
    non_form = output.getvalue()
    analysis = await client.post(
        "/api/templates/intake/analyze",
        files={"file": ("static.pdf", non_form, "application/pdf")},
    )
    assert analysis.status_code == 200
    assert any(
        "no fillable AcroForm" in warning for warning in analysis.json()["warnings"]
    )
    create = await client.post(
        "/api/templates/intake/create",
        files={"file": ("static.pdf", non_form, "application/pdf")},
    )
    assert create.status_code == 422

    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(BytesIO(_fillable_pdf())))
    writer.add_js("app.alert('unsafe')")
    active_output = BytesIO()
    writer.write(active_output)
    active = await client.post(
        "/api/templates/intake/analyze",
        files={"file": ("active.pdf", active_output.getvalue(), "application/pdf")},
    )
    assert active.status_code == 422
    assert "Active PDF content" in active.json()["detail"]


@pytest.mark.asyncio
async def test_pdf_patch_revalidates_field_map_source_and_activation(
    client, db_session, test_tenant, test_user, tmp_path, monkeypatch
):
    import copy

    from sqlalchemy import select

    from app.models.document_template import DocumentTemplate

    monkeypatch.setattr(document_templates.settings, "UPLOAD_DIR", str(tmp_path))
    await _grant_manage_documents(db_session, test_tenant, test_user)
    created = await client.post(
        "/api/templates/intake/create",
        files={"file": ("mapped.pdf", _fillable_pdf(), "application/pdf")},
    )
    assert created.status_code == 201, created.text
    template_id = created.json()["id"]
    valid_schema = created.json()["variable_schema"]

    duplicate_mapping = copy.deepcopy(valid_schema)
    duplicate_mapping["fields"][1]["pdf_field_name"] = duplicate_mapping["fields"][0][
        "pdf_field_name"
    ]
    duplicate = await client.patch(
        f"/api/templates/{template_id}",
        json={"variable_schema": duplicate_mapping},
    )
    assert duplicate.status_code == 422
    assert "Duplicate PDF field mapping" in duplicate.json()["detail"]

    phantom = copy.deepcopy(valid_schema)
    phantom["fields"].append({"name": "phantom", "label": "Phantom"})
    unmapped = await client.patch(
        f"/api/templates/{template_id}", json={"variable_schema": phantom}
    )
    assert unmapped.status_code == 422
    assert "missing pdf_field_name" in unmapped.json()["detail"]

    phantom_body = await client.patch(
        f"/api/templates/{template_id}", json={"body": "{{phantom}}"}
    )
    assert phantom_body.status_code == 422
    assert "without AcroForm mappings" in phantom_body.json()["detail"]

    blocked_activation = await client.patch(
        f"/api/templates/{template_id}", json={"is_active": True}
    )
    assert blocked_activation.status_code == 409
    assert "Preview this PDF successfully" in blocked_activation.json()["detail"]

    preview = await client.post(
        f"/api/templates/{template_id}/render-file", json={"variables": {}}
    )
    assert preview.status_code == 200, preview.text

    activated = await client.patch(
        f"/api/templates/{template_id}", json={"is_active": True}
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["is_active"] is True
    assert activated.json()["last_test_rendered_at"] is not None
    assert activated.json()["approved_at"] is not None
    assert activated.json()["approved_by_user_id"] == str(test_user.id)

    combined_edit_activation = await client.patch(
        f"/api/templates/{template_id}",
        json={"body": "{{client_name}}", "is_active": True},
    )
    assert combined_edit_activation.status_code == 409
    assert (
        "Preview the updated PDF successfully"
        in combined_edit_activation.json()["detail"]
    )

    metadata_only = await client.patch(
        f"/api/templates/{template_id}", json={"title": "Mapped PDF v2"}
    )
    assert metadata_only.status_code == 200, metadata_only.text
    assert metadata_only.json()["is_active"] is True
    assert metadata_only.json()["approved_at"] == activated.json()["approved_at"]

    edited_contract = await client.patch(
        f"/api/templates/{template_id}", json={"body": "{{client_name}}"}
    )
    assert edited_contract.status_code == 200, edited_contract.text
    assert edited_contract.json()["is_active"] is False
    assert edited_contract.json()["last_test_rendered_at"] is None
    assert edited_contract.json()["approved_at"] is None
    assert edited_contract.json()["approved_by_user_id"] is None

    edited_activation = await client.patch(
        f"/api/templates/{template_id}", json={"is_active": True}
    )
    assert edited_activation.status_code == 409
    assert "Preview this PDF successfully" in edited_activation.json()["detail"]

    second_preview = await client.post(
        f"/api/templates/{template_id}/render-file", json={"variables": {}}
    )
    assert second_preview.status_code == 200, second_preview.text
    reactivated = await client.patch(
        f"/api/templates/{template_id}", json={"is_active": True}
    )
    assert reactivated.status_code == 200, reactivated.text
    assert reactivated.json()["is_active"] is True

    template = await db_session.scalar(
        select(DocumentTemplate).where(DocumentTemplate.id == uuid.UUID(template_id))
    )
    template.source_sha256 = None
    await db_session.commit()
    missing_integrity = await client.patch(
        f"/api/templates/{template_id}", json={"is_active": True}
    )
    assert missing_integrity.status_code == 409
    assert "integrity check" in missing_integrity.json()["detail"]


@pytest.mark.asyncio
async def test_pdf_creation_rejects_unmapped_reviewed_body_and_json_shortcut(
    client, db_session, test_tenant, test_user, tmp_path, monkeypatch
):
    monkeypatch.setattr(document_templates.settings, "UPLOAD_DIR", str(tmp_path))
    await _grant_manage_documents(db_session, test_tenant, test_user)
    phantom_body = await client.post(
        "/api/templates/intake/create",
        files={"file": ("mapped.pdf", _fillable_pdf(), "application/pdf")},
        data={"reviewed_body": "Matter: {{phantom}}"},
    )
    assert phantom_body.status_code == 422
    assert "without AcroForm mappings" in phantom_body.json()["detail"]

    shortcut = await client.post(
        "/api/templates",
        json={
            "title": "Unsafe PDF shortcut",
            "body": "{{client_name}}",
            "format": "pdf",
            "variable_schema": {
                "fields": [{"name": "client_name", "pdf_field_name": "client_name"}]
            },
        },
    )
    assert shortcut.status_code == 422
    assert "multipart" in shortcut.json()["detail"]


@pytest.mark.asyncio
async def test_document_template_sensitive_routes_require_manage_documents(
    client, db_session, test_tenant, test_user
):
    from app.models.document_template import DocumentTemplate
    from app.models.rbac import Role, UserRole

    limited_role = Role(
        tenant_id=test_tenant.id,
        name="Matter-only staff",
        capabilities=["manage_matters"],
    )
    db_session.add(limited_role)
    await db_session.flush()
    db_session.add(
        UserRole(
            user_id=test_user.id,
            role_id=limited_role.id,
            tenant_id=test_tenant.id,
            source="manual",
        )
    )
    template = DocumentTemplate(
        tenant_id=test_tenant.id,
        title="Restricted template",
        body="Hello {{client_name}}",
        category="other",
        format="markdown",
        is_active=True,
    )
    db_session.add(template)
    await db_session.commit()

    listed = await client.get("/api/templates")
    assert listed.status_code == 200
    mutation = await client.post(
        "/api/templates",
        json={"title": "Denied", "body": "Body", "format": "markdown"},
    )
    source = await client.get(f"/api/templates/{template.id}/source")
    rendered = await client.post(
        f"/api/templates/{template.id}/render",
        json={"variables": {"client_name": "Ada"}},
    )
    assert mutation.status_code == 403
    assert source.status_code == 403
    assert rendered.status_code == 403
    assert all(
        response.json()["detail"] == "Missing capability: manage_documents"
        for response in (mutation, source, rendered)
    )


@pytest.mark.asyncio
async def test_pdf_mime_normalization_blocks_active_extensionless_uploads(client):
    from pypdf import PdfReader, PdfWriter

    benign = await client.post(
        "/api/templates/intake/analyze",
        files={"file": ("upload", _fillable_pdf(), "Application/PDF; charset=binary")},
    )
    assert benign.status_code == 200, benign.text
    assert benign.json()["format"] == "pdf"

    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(BytesIO(_fillable_pdf())))
    writer.add_js("app.alert('unsafe')")
    active = BytesIO()
    writer.write(active)
    rejected = await client.post(
        "/api/templates/intake/analyze",
        files={
            "file": (
                "upload",
                active.getvalue(),
                "Application/PDF; charset=binary",
            )
        },
    )
    assert rejected.status_code == 422
    assert "Active PDF content" in rejected.json()["detail"]

    disguised = await client.post(
        "/api/templates/intake/analyze",
        files={"file": ("upload.txt", active.getvalue(), "text/plain")},
    )
    assert disguised.status_code == 422
    assert "Active PDF content" in disguised.json()["detail"]
