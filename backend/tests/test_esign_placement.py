import pytest
import hashlib
from io import BytesIO
from pypdf import PdfWriter
from pypdf.generic import FloatObject, NameObject, RectangleObject
from app.schemas.matter_document import MatterDocumentResponse

from app.services.esign.placement import (
    PlacementError,
    from_dropbox_coordinates,
    to_dropbox_form_field,
    validate_placements,
    template_positioned_fields,
    signing_template_fields,
    generated_signing_metadata,
    validate_pdf_geometry,
)


SHA = "a" * 64


def _pdf(width=612, height=792, rotation=0, crop=None, unit=None):
    writer = PdfWriter()
    page = writer.add_blank_page(width=width, height=height)
    if rotation:
        page.rotate(rotation)
    if crop:
        page.cropbox = RectangleObject(crop)
    if unit:
        page[NameObject("/UserUnit")] = FloatObject(unit)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _field(**overrides):
    value = {
        "field_id": "client-signature",
        "field_type": "signature",
        "role": "client",
        "page": 1,
        "rect": [72, 100, 216, 136],
        "page_width": 612,
        "page_height": 792,
        "source_sha256": SHA,
    }
    value.update(overrides)
    return value


def test_pdf_bottom_left_round_trips_through_dropbox_top_left_coordinates():
    field = validate_placements([_field()], source_sha256=SHA, signer_roles={"client"})[
        0
    ]
    payload = to_dropbox_form_field(field, signer_index=0)
    assert payload["page"] == 1
    assert payload["x"] == 72
    assert payload["y"] == 656
    assert payload["width"] == 162
    assert payload["height"] == 40
    assert from_dropbox_coordinates(
        x=payload["x"],
        y=payload["y"],
        width=payload["width"],
        height=payload["height"],
        page_width=612,
        page_height=792,
    ) == pytest.approx((72, 100, 216, 136))


def test_stale_digest_and_unresolved_role_fail_closed():
    with pytest.raises(PlacementError, match="stale"):
        validate_placements(
            [_field(source_sha256="b" * 64)], source_sha256=SHA, signer_roles={"client"}
        )
    with pytest.raises(PlacementError, match="has no signer"):
        validate_placements(
            [_field(role="attorney")], source_sha256=SHA, signer_roles={"client"}
        )


def test_geometry_outside_generated_page_is_rejected():
    with pytest.raises(PlacementError, match="outside"):
        validate_placements(
            [_field(rect=[600, 100, 620, 136])],
            source_sha256=SHA,
            signer_roles={"client"},
        )


def test_existing_documents_without_manifest_serialize_as_empty_list():
    assert (
        MatterDocumentResponse.model_validate(
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "tenant_id": "00000000-0000-0000-0000-000000000002",
                "matter_id": "00000000-0000-0000-0000-000000000003",
                "filename": "old.pdf",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "uploaded_by_user_id": None,
                "content_type": "application/pdf",
                "file_size": 10,
                "description": None,
                "document_category": "generated",
                "positioned_fields": None,
            }
        ).positioned_fields
        == []
    )


def test_published_pdf_template_descriptor_preserves_repeated_role_fields():
    source = _pdf()
    descriptor = template_positioned_fields(
        {
            "pages": [{"page": 1, "width": 612, "height": 792}],
            "fields": [
                {
                    "name": "client_sig",
                    "field_type": "signature",
                    "signer_role": "client",
                    "pdf_overlays": [
                        {"page": 1, "rect": [10, 10, 100, 40]},
                        {"page": 1, "rect": [200, 10, 290, 40]},
                    ],
                }
            ],
        },
        source=source,
    )
    assert [item["field_id"] for item in descriptor] == ["field-0-0", "field-0-1"]
    assert all(
        item["source_sha256"] == hashlib.sha256(source).hexdigest()
        for item in descriptor
    )


def test_template_descriptor_requires_role_and_final_geometry():
    with pytest.raises(PlacementError, match="requires a signer role"):
        template_positioned_fields(
            {"pages": [], "fields": [{"field_type": "signature", "name": "sig"}]},
            source=_pdf(),
        )


def test_ordinary_dates_and_excluded_signature_fields_do_not_require_signing():
    schema = {
        "fields": [
            {"field_type": "date"},
            {"field_type": "signature", "included": False},
        ]
    }
    assert signing_template_fields(schema) == []
    assert template_positioned_fields(schema, source=b"not needed") == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"page": 0},
        {"page": True},
        {"page": 1.5},
        {"rect": [0, 0, float("inf"), 10]},
        {"rect": [0, 0, 0.1, 10]},
        {"field_type": "unknown"},
        {"field_id": ""},
        {"source_sha256": "invalid"},
        {"page_width": None},
        {"rect": [0, 1]},
    ],
)
def test_invalid_manifest_cannot_be_sent(overrides):
    with pytest.raises(PlacementError):
        validate_placements(
            [_field(**overrides)], source_sha256=SHA, signer_roles={"client"}
        )


@pytest.mark.parametrize(
    "options",
    [
        {"width": 595},
        {"rotation": 90},
        {"crop": [10, 10, 612, 792]},
        {"crop": [0, 0, 600, 790]},
        {"unit": 2},
    ],
)
def test_unsupported_pdf_geometry_fails_closed(options):
    fields = validate_placements([_field()], source_sha256=SHA, signer_roles={"client"})
    with pytest.raises(PlacementError):
        validate_pdf_geometry(_pdf(**options), fields)


def test_page_count_and_dimensions_are_verified_from_actual_pdf():
    for override in [{"page": 2}, {"page_width": 600}]:
        fields = validate_placements(
            [_field(**override)], source_sha256=SHA, signer_roles={"client"}
        )
        with pytest.raises(PlacementError):
            validate_pdf_geometry(_pdf(), fields)
    with pytest.raises(PlacementError):
        validate_pdf_geometry(b"not pdf", [])


def test_final_pdf_review_requires_all_original_signer_roles():
    with pytest.raises(PlacementError, match="every role"):
        validate_placements(
            [_field()],
            source_sha256=SHA,
            signer_roles={"client", "witness"},
            required_roles=["client", "witness"],
        )
    fields = validate_placements(
        [_field(), _field(field_id="witness", role="witness")],
        source_sha256=SHA,
        signer_roles={"client", "witness"},
        required_roles=["client", "witness"],
    )
    assert [field.role for field in fields] == ["client", "witness"]


@pytest.mark.parametrize(
    "source_format,width,role,has_positions",
    [
        ("pdf", 612, "client", True),
        ("pdf", 595, "client", False),
        ("pdf", 612, "", False),
        ("docx", 612, "client", False),
    ],
)
def test_generation_keeps_documents_usable_and_requires_review_when_positions_cannot_transfer(
    source_format, width, role, has_positions
):
    schema = {
        "fields": [
            {
                "field_type": "signature",
                "signer_role": role,
                "pdf_overlay": {"page": 1, "rect": [72, 100, 216, 136]},
            }
        ]
    }
    positions, roles, required = generated_signing_metadata(
        schema, source=_pdf(width=width), template_format=source_format
    )
    assert bool(positions) is has_positions
    assert roles == ([role] if role else [])
    assert required is True
    assert generated_signing_metadata(
        {"fields": [{"field_type": "date"}]},
        source=b"ordinary document",
        template_format=source_format,
    ) == ([], [], False)
