import pytest
from app.schemas.matter_document import MatterDocumentResponse

from app.services.esign.placement import (
    PlacementError,
    from_dropbox_coordinates,
    to_dropbox_form_field,
    validate_placements,
    template_positioned_fields,
)


SHA = "a" * 64


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
    field = validate_placements([_field()], source_sha256=SHA, signer_roles={"client"})[0]
    payload = to_dropbox_form_field(field, signer_index=0)
    assert payload["page"] == 1
    assert payload["x"] == 72
    assert payload["y"] == 656
    assert payload["width"] == 162
    assert payload["height"] == 40
    assert from_dropbox_coordinates(
        x=payload["x"], y=payload["y"], width=payload["width"],
        height=payload["height"], page_width=612, page_height=792,
    ) == pytest.approx((72, 100, 216, 136))


def test_stale_digest_and_unresolved_role_fail_closed():
    with pytest.raises(PlacementError, match="stale"):
        validate_placements([_field(source_sha256="b" * 64)], source_sha256=SHA, signer_roles={"client"})
    with pytest.raises(PlacementError, match="has no signer"):
        validate_placements([_field(role="attorney")], source_sha256=SHA, signer_roles={"client"})


def test_geometry_outside_generated_page_is_rejected():
    with pytest.raises(PlacementError, match="outside"):
        validate_placements([_field(rect=[600, 100, 620, 136])], source_sha256=SHA, signer_roles={"client"})


def test_existing_documents_without_manifest_serialize_as_empty_list():
    assert MatterDocumentResponse.model_validate({
        "id": "00000000-0000-0000-0000-000000000001", "tenant_id": "00000000-0000-0000-0000-000000000002", "matter_id": "00000000-0000-0000-0000-000000000003",
        "filename": "old.pdf", "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z", "uploaded_by_user_id": None, "content_type": "application/pdf", "file_size": 10, "description": None, "document_category": "generated",
        "positioned_fields": None,
    }).positioned_fields == []


def test_published_pdf_template_descriptor_preserves_repeated_role_fields():
    descriptor = template_positioned_fields({
        "pages": [{"page": 1, "width": 612, "height": 792}],
        "fields": [{"name": "client_sig", "field_type": "signature", "signer_role": "client", "pdf_overlays": [
            {"page": 1, "rect": [10, 10, 100, 40]}, {"page": 1, "rect": [200, 10, 290, 40]}
        ]}],
    }, source_sha256=SHA)
    assert [item["field_id"] for item in descriptor] == ["client_sig-0", "client_sig-1"]
    assert all(item["source_sha256"] == SHA for item in descriptor)


def test_template_descriptor_requires_role_and_final_geometry():
    with pytest.raises(PlacementError, match="require final PDF placement|requires a role"):
        template_positioned_fields({"pages": [], "fields": [{"field_type": "signature", "name": "sig"}]}, source_sha256=SHA)
