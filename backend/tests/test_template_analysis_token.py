import copy
import json
import uuid

import pytest
from fastapi import HTTPException

from app.routers import document_templates
from app.services.pdf_templates import (
    TemplatePdfError,
    discover_pdf_fields,
    discover_pdf_overlay_fields,
    fill_pdf_template,
)


def _analysis_payload() -> dict:
    return {
        "title": "Client intake",
        "format": "pdf",
        "body": "Applicant: {{client_name}}",
        "body_preview": "Applicant: {{client_name}}",
        "extracted_text": "Applicant: Ada Lovelace",
        "suggested_variable_schema": {
            "version": 1,
            "source": "pdf_ocr_overlay",
            "fields": [],
        },
        "detected_branding_profile": {},
        "warnings": [],
    }


def test_analysis_token_round_trip_is_bound_to_source_user_and_tenant():
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    source = b"exact uploaded bytes"
    payload = _analysis_payload()

    token = document_templates._issue_analysis_token(
        analysis=payload,
        file_bytes=source,
        filename="filled-form.png",
        tenant_id=tenant_id,
        user_id=user_id,
    )

    recovered = document_templates._analysis_from_token(
        token,
        file_bytes=source,
        filename="filled-form.png",
        tenant_id=tenant_id,
        user_id=user_id,
    )

    assert recovered == payload


@pytest.mark.parametrize(
    ("override", "value"),
    [
        ("file_bytes", b"different bytes"),
        ("filename", "different.png"),
        ("tenant_id", uuid.uuid4()),
        ("user_id", uuid.uuid4()),
    ],
)
def test_analysis_token_rejects_a_changed_binding(override, value):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    source = b"exact uploaded bytes"
    token = document_templates._issue_analysis_token(
        analysis=_analysis_payload(),
        file_bytes=source,
        filename="filled-form.png",
        tenant_id=tenant_id,
        user_id=user_id,
    )
    request = {
        "file_bytes": source,
        "filename": "filled-form.png",
        "tenant_id": tenant_id,
        "user_id": user_id,
    }
    request[override] = value

    with pytest.raises(HTTPException) as exc_info:
        document_templates._analysis_from_token(token, **request)

    assert exc_info.value.status_code == 409
    assert "Analyze the source again" in exc_info.value.detail


def test_analysis_token_rejects_tampering():
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    token = document_templates._issue_analysis_token(
        analysis=_analysis_payload(),
        file_bytes=b"source",
        filename="filled-form.pdf",
        tenant_id=tenant_id,
        user_id=user_id,
    )
    replacement = "A" if token[-1] != "A" else "B"

    with pytest.raises(HTTPException) as exc_info:
        document_templates._analysis_from_token(
            token[:-1] + replacement,
            file_bytes=b"source",
            filename="filled-form.pdf",
            tenant_id=tenant_id,
            user_id=user_id,
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_create_handoff_reuses_signed_analysis_without_running_ocr_again(
    monkeypatch,
):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    source = b"%PDF-1.7 already validated source"
    payload = _analysis_payload()
    token = document_templates._issue_analysis_token(
        analysis=payload,
        file_bytes=source,
        filename="filled-form.pdf",
        tenant_id=tenant_id,
        user_id=user_id,
    )

    def unexpected_analysis(**_kwargs):
        raise AssertionError("the reviewed source must not be analyzed twice")

    monkeypatch.setattr(
        document_templates,
        "analyze_template_upload",
        unexpected_analysis,
    )

    analysis = await document_templates._analysis_for_template_create(
        file_bytes=source,
        filename="filled-form.pdf",
        content_type="application/pdf",
        requested_title="Reviewed handwritten intake",
        analysis_token=token,
        tenant_id=tenant_id,
        user_id=user_id,
    )

    assert analysis.title == "Reviewed handwritten intake"
    assert analysis.variable_schema == payload["suggested_variable_schema"]
    assert analysis._normalized_source_bytes == source
    assert analysis._normalized_source_filename == "filled-form.pdf"


def _mixed_pdf_source() -> bytes:
    from io import BytesIO

    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    pdf.drawString(72, 720, "Client:")
    pdf.acroForm.textfield(
        name="ClientName", x=125, y=705, width=220, height=24
    )
    pdf.drawString(72, 660, "Case: CV-OLD")
    pdf.save()
    return output.getvalue()


def _mixed_variable_schema() -> dict:
    overlay = {
        "page": 1,
        "rect": [105, 648, 225, 666],
        "font_size": 11,
        "erase_source": True,
        "source_text": "CV-OLD",
        "source_kind": "ocr",
    }
    return {
        "version": 1,
        "source": "pdf_acroform_ocr",
        "pages": [{"page": 1, "width": 612, "height": 792, "rotation": 0}],
        "fields": [
            {
                "name": "client_name",
                "label": "Client",
                "pdf_field_name": "ClientName",
                "pdf_source_key": "acroform:ClientName",
                "field_type": "text",
                "required": False,
                "page": 1,
                "rect": [125, 705, 345, 729],
            },
            {
                "name": "case_number",
                "label": "Case number",
                "pdf_source_key": "overlay:test-case-number",
                "pdf_overlay": overlay,
                "pdf_overlays": [overlay],
                "field_type": "text",
                "required": True,
                "page": 1,
                "rect": overlay["rect"],
            },
        ],
    }


def test_reviewed_mixed_schema_preserves_each_authoritative_mapping():
    discovered = _mixed_variable_schema()
    reviewed = copy.deepcopy(discovered)
    reviewed["fields"][0]["name"] = "party_name"
    reviewed["fields"][1]["name"] = "docket_number"
    reviewed["fields"][1]["pdf_overlay"]["rect"] = [72, 700, 220, 716]

    validated = document_templates._reviewed_variable_schema(
        json.dumps(reviewed), discovered
    )

    assert [field["name"] for field in validated["fields"]] == [
        "party_name",
        "docket_number",
    ]
    assert validated["fields"][0]["pdf_field_name"] == "ClientName"
    assert validated["fields"][1]["pdf_overlay"]["rect"] == [72.0, 700.0, 220.0, 716.0]
    assert validated["fields"][1]["pdf_overlay"]["source_rect"] == discovered["fields"][1]["pdf_overlay"]["rect"]


def test_reviewed_overlay_included_flag_must_be_a_boolean():
    discovered = _mixed_variable_schema()
    reviewed = copy.deepcopy(discovered)
    reviewed["fields"][1]["included"] = "false"

    with pytest.raises(HTTPException, match="included must be boolean"):
        document_templates._reviewed_variable_schema(
            json.dumps(reviewed), discovered
        )


def test_reviewed_schema_accepts_manual_field_when_detection_is_empty():
    manual_key = "manual:" + str(uuid.uuid4())
    discovered = {
        "version": 1,
        "source": "pdf_ocr_overlay",
        "pages": [{"page": 1, "width": 612, "height": 792, "rotation": 0}],
        "fields": [],
    }
    reviewed = {
        "version": 1,
        "fields": [{
            "name": "handwritten_name",
            "label": "Name",
            "pdf_source_key": manual_key,
            "pdf_overlay": {"page": 1, "rect": [72, 700, 260, 724]},
            "field_type": "text",
            "required": True,
            "multiline": False,
        }],
    }
    validated = document_templates._reviewed_variable_schema(
        json.dumps(reviewed), discovered
    )
    field = validated["fields"][0]
    assert field["pdf_source_key"] == manual_key
    assert field["pdf_overlay"]["source_kind"] == "manual"
    assert field["required"] is True
    assert validated["pages"] == discovered["pages"]


def test_reviewed_manual_field_requires_signed_page_metadata():
    key = "manual:" + str(uuid.uuid4())
    with pytest.raises(HTTPException, match="signed page bounds"):
        document_templates._reviewed_variable_schema(
            json.dumps({"fields": [{
                "name": "manual_name",
                "pdf_source_key": key,
                "pdf_overlay": {"page": 1, "rect": [72, 700, 260, 724]},
            }]}),
            {"fields": [], "pages": []},
        )


def test_mixed_pdf_renderer_fills_acroform_and_ocr_overlay_together():
    from io import BytesIO

    from pypdf import PdfReader

    rendered = fill_pdf_template(
        _mixed_pdf_source(),
        variable_schema=_mixed_variable_schema(),
        variables={"client_name": "Grace Hopper", "case_number": "CV-2027-9"},
        flatten=True,
        enforce_required=True,
    )

    text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(rendered)).pages)
    assert "Grace Hopper" in text
    assert "CV-2027-9" in text


def test_mixed_pdf_renderer_requires_review_of_overlay_fields():
    with pytest.raises(TemplatePdfError, match="case_number"):
        fill_pdf_template(
            _mixed_pdf_source(),
            variable_schema=_mixed_variable_schema(),
            variables={"client_name": "Grace Hopper"},
            flatten=True,
            enforce_required=True,
        )


def test_excluded_acroform_field_rejects_input_and_clears_sample_value():
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(BytesIO(_mixed_pdf_source())))
    for page in writer.pages:
        writer.update_page_form_field_values(
            page,
            {"ClientName": "SAMPLE PRIVILEGED NAME"},
            auto_regenerate=True,
        )
    populated = BytesIO()
    writer.write(populated)
    source = populated.getvalue()
    schema = {"fields": discover_pdf_fields(source)}
    schema["fields"][0]["included"] = False

    with pytest.raises(TemplatePdfError, match="Unknown PDF template variable"):
        fill_pdf_template(
            source,
            variable_schema=schema,
            variables={"client_name": "attacker supplied value"},
            flatten=False,
        )

    rendered = fill_pdf_template(
        source,
        variable_schema=schema,
        variables={},
        flatten=False,
        enforce_required=True,
    )
    stored = PdfReader(BytesIO(rendered)).get_fields()["ClientName"]
    assert "SAMPLE PRIVILEGED NAME" not in str(stored.get("/V") or "")


def test_excluded_required_overlay_is_cleaned_without_becoming_an_input():
    from io import BytesIO

    from pypdf import PdfReader

    schema = _mixed_variable_schema()
    schema["fields"][1]["included"] = False
    rendered = fill_pdf_template(
        _mixed_pdf_source(),
        variable_schema=schema,
        variables={"client_name": ""},
        flatten=True,
        enforce_required=True,
    )
    text = PdfReader(BytesIO(rendered)).pages[0].extract_text() or ""
    assert "CV-OLD" not in text


def test_manual_signature_needs_no_input_and_required_checkbox_must_be_true():
    from io import BytesIO

    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    pdf.showPage()
    pdf.save()
    source = output.getvalue()

    signature_overlay = {
        "page": 1,
        "rect": [72, 640, 260, 676],
        "source_kind": "manual",
        "erase_source": False,
    }
    signature_schema = {"fields": [{
        "name": "client_signature",
        "pdf_source_key": f"manual:{uuid.uuid4()}",
        "field_type": "signature",
        "required": True,
        "pdf_overlay": signature_overlay,
    }]}
    assert fill_pdf_template(
        source,
        variable_schema=signature_schema,
        variables={},
        flatten=True,
        enforce_required=True,
    ).startswith(b"%PDF")

    checkbox_overlay = {
        "page": 1,
        "rect": [72, 600, 92, 620],
        "source_kind": "manual",
        "erase_source": False,
    }
    checkbox_schema = {"fields": [{
        "name": "approved",
        "pdf_source_key": f"manual:{uuid.uuid4()}",
        "field_type": "checkbox",
        "required": True,
        "pdf_overlay": checkbox_overlay,
    }]}
    with pytest.raises(TemplatePdfError, match="Required PDF field"):
        fill_pdf_template(
            source,
            variable_schema=checkbox_schema,
            variables={"approved": "false"},
            flatten=True,
            enforce_required=True,
        )
    assert fill_pdf_template(
        source,
        variable_schema=checkbox_schema,
        variables={"approved": "true"},
        flatten=True,
        enforce_required=True,
    ).startswith(b"%PDF")


@pytest.mark.parametrize(
    "overlay",
    [
        {"page": "not-a-page", "rect": [72, 700, 260, 724]},
        [{"page": 1, "rect": [72, 700, 260, 724]}, {"page": 1, "rect": [80, 650, 200, 674]}],
    ],
)
def test_malformed_manual_overlay_returns_422(overlay):
    key = f"manual:{uuid.uuid4()}"
    reviewed = {
        "fields": [{
            "name": "manual_name",
            "pdf_source_key": key,
            "pdf_overlays": overlay,
            "field_type": "text",
            "required": False,
            "multiline": False,
            "included": True,
        }],
    }
    with pytest.raises(HTTPException) as exc_info:
        document_templates._reviewed_variable_schema(
            json.dumps(reviewed),
            {
                "fields": [],
                "pages": [{"page": 1, "width": 612, "height": 792, "rotation": 0}],
            },
        )
    assert exc_info.value.status_code == 422


def test_unreadable_handwriting_fallback_uses_bounded_nonduplicated_redaction():
    from io import BytesIO

    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    pdf.showPage()
    pdf.save()
    label = {
        "page_index": 0,
        "text": "Applicant Name:",
        "x": 72,
        "y": 700,
        "font_size": 11,
        "text_width": 90,
        "source_kind": "ocr",
        "ocr_score": 0.92,
    }
    next_field = {
        "page_index": 0,
        "text": "Date:",
        "x": 310,
        "y": 700,
        "font_size": 11,
        "text_width": 28,
        "source_kind": "ocr",
        "ocr_score": 0.95,
    }

    unreadable = discover_pdf_overlay_fields(
        output.getvalue(), [], fragments=[label, next_field]
    )
    applicant = next(
        field for field in unreadable if field["name"] == "client_name"
    )
    left, _bottom, right, _top = applicant["pdf_overlay"]["rect"]
    assert 24 <= right - left <= 216
    assert right <= next_field["x"] - 4

    recognized = discover_pdf_overlay_fields(
        output.getvalue(),
        [
            {
                "name": "client_name",
                "label": "Client name",
                "source_text": "Ada Lovelace",
                "confidence": 0.72,
            }
        ],
        fragments=[
            label,
            {
                "page_index": 0,
                "text": "Ada Lovelace",
                "x": 170,
                "y": 700,
                "font_size": 11,
                "text_width": 74,
                "source_kind": "ocr",
                "ocr_score": 0.74,
            },
        ],
    )
    applicant = next(
        field for field in recognized if field["name"] == "client_name"
    )
    assert len(applicant["pdf_overlays"]) == 1
    assert applicant["pdf_overlay"]["source_text"] == "Ada Lovelace"
