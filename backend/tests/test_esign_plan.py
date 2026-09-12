"""Where the portal puts signature fields, over generated PDFs."""

import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import intake_starter_pack as pack
from app.services.esign.placement import PlacementError
from app.services.esign.plan import (
    FieldValueError,
    SignerRef,
    assign_roles,
    build_plan,
    initials_for,
    manifest,
    plan_request_placements,
    saved_values_from_signers,
    validate_field_values,
)
from tests.esign_pdf_fixtures import (
    acroform_pdf,
    blank_pdf,
    flat_agreement_pdf,
    label_below_rule_pdf,
)

CLIENT = SignerRef("s-client", "Jane Client", "client", 0)
ATTORNEY = SignerRef("s-attorney", "Ann Attorney", "attorney", 1)
STARTER_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "generate_intake_starter_pdfs.py"
)


def _by_kind(plan, kind):
    return [f for f in plan.fields if f.kind == kind]


def test_acroform_widgets_become_the_manifest_and_the_sig_widget_takes_the_role():
    plan = build_plan(acroform_pdf(), signers=[CLIENT])

    assert plan.fill_supported is True
    kinds = {f.field_id: f.kind for f in plan.fields}
    assert kinds == {
        "acroform:client_name": "text",
        "acroform:agree": "checkbox",
        "acroform:state": "choice",
        "acroform:plan": "radio",
        "acroform:client_signature": "signature",
    }
    name = next(f for f in plan.fields if f.field_id == "acroform:client_name")
    assert name.label == "Client name" and name.required is True
    assert name.page == 1 and name.rect == (150.0, 690.0, 350.0, 710.0)
    radio = next(f for f in plan.fields if f.kind == "radio")
    assert radio.options == ["A", "B"] and len(radio.widgets) == 2
    signature = plan.signature_fields[0]
    assert signature.role == "client" and signature.source == "acroform"
    assert signature.rect == (72.0, 170.0, 300.0, 200.0)
    # A widget already in the PDF needs no persisted placement.
    assert plan.positioned_fields() == []
    assert plan.summary() == {
        "fill_supported": True,
        "signature_fields_count": 1,
        "placement_source": "acroform",
        "input_fields_count": 4,
    }


def test_printed_signature_lines_are_detected_and_dated_for_one_signer():
    plan = build_plan(flat_agreement_pdf(), signers=[CLIENT])

    signatures = _by_kind(plan, "signature")
    dates = _by_kind(plan, "date")
    # The sentence that merely mentions signing and the "Name:" blank are not
    # signature lines; the attorney's line stays blank for a client-only send.
    assert [f.label for f in signatures] == ["Client Signature"]
    signature = signatures[0]
    assert signature.field_id == "auto:sig:1" and signature.source == "detected"
    assert signature.role == "client" and signature.page == 1
    x0, y0, x1, y1 = signature.rect
    assert 150 < x0 < 165 and y0 == pytest.approx(196.0)
    assert x1 - x0 == pytest.approx(200.0) and y1 - y0 == pytest.approx(28.0)
    assert len(dates) == 1 and dates[0].field_id == "auto:date:1"
    assert dates[0].role == "client" and dates[0].rect[0] > signature.rect[2]
    persisted = plan.positioned_fields()
    assert [item["field_id"] for item in persisted] == ["auto:sig:1", "auto:date:1"]
    assert all(item["source_sha256"] == plan.source_sha256 for item in persisted)
    assert all(item["page_width"] == 612.0 for item in persisted)


def test_two_signers_take_the_lines_printed_for_their_parties():
    plan = build_plan(flat_agreement_pdf(), signers=[CLIENT, ATTORNEY])

    roles = {f.label: f.role for f in _by_kind(plan, "signature")}
    assert roles == {"Client Signature": "client", "Attorney signature": "attorney"}
    attorney = next(f for f in plan.signature_fields if f.role == "attorney")
    # The label sits above a ruled line; the box covers the line, not the label.
    assert attorney.rect == (72.0, 138.0, 300.0, 166.0)
    assert plan.placement_source == "detected"


def test_a_label_printed_under_the_ruled_line_places_the_box_on_the_line():
    plan = build_plan(label_below_rule_pdf(), signers=[CLIENT])

    signatures = _by_kind(plan, "signature")
    dates = _by_kind(plan, "date")
    assert len(signatures) == 1 and len(dates) == 1
    assert signatures[0].rect == (54.0, 526.5, 294.0, 554.5)
    assert dates[0].rect == (324.0, 526.5, 454.0, 550.5)


@pytest.fixture(scope="module")
def starter_generator():
    spec = importlib.util.spec_from_file_location("intake_starter_pdfs", STARTER_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_firms_own_questionnaire_gets_one_signature_and_one_date(
    starter_generator, tmp_path
):
    """The starter pack is the primary real-world input: label 9pt under the rule."""
    practice = pack.practices()[0]
    path = starter_generator.render_questionnaire(practice, tmp_path / "q.pdf")
    source = path.read_bytes()

    plan = build_plan(source, signers=[CLIENT])

    signatures = _by_kind(plan, "signature")
    dates = _by_kind(plan, "date")
    assert len(signatures) == 1 and len(dates) == 1
    last_page = len(plan.pages)
    assert signatures[0].page == last_page and dates[0].page == last_page
    margin = starter_generator.MARGIN
    x0, y0, x1, y1 = signatures[0].rect
    # The ruled line runs MARGIN..MARGIN+240; the box covers it and sits on it.
    assert x0 == pytest.approx(margin) and x1 == pytest.approx(margin + 240)
    assert y0 < signatures[0].rect[3] and y1 - y0 == pytest.approx(28.0)
    dx0, _dy0, dx1, _dy1 = dates[0].rect
    assert dx0 == pytest.approx(margin + 270) and dx1 == pytest.approx(margin + 400)
    assert dx0 > x1
    assert abs(dates[0].rect[1] - y0) < 1
    # The form's own answer boxes are fillable inputs, none of them signatures.
    assert plan.summary()["input_fields_count"] == len(
        pack.questionnaire(practice.slug)
    )


def test_the_fee_agreement_signature_lines_each_go_to_their_party(
    starter_generator, tmp_path
):
    path = starter_generator.render_document(pack.FEE_AGREEMENT, tmp_path / "fee.pdf")
    plan = build_plan(path.read_bytes(), signers=[CLIENT])

    signatures = _by_kind(plan, "signature")
    assert signatures and all(f.role == "client" for f in signatures)
    assert all(f.source == "detected" for f in signatures)


def test_blank_document_falls_back_to_a_block_per_role_on_the_last_page():
    plan = build_plan(blank_pdf(pages=2), signers=[CLIENT, ATTORNEY])

    assert plan.placement_source == "fallback"
    signatures = _by_kind(plan, "signature")
    dates = _by_kind(plan, "date")
    assert [f.role for f in signatures] == ["client", "attorney"]
    assert all(f.page == 2 for f in signatures + dates)
    assert [f.field_id for f in signatures] == ["auto:sig:1", "auto:sig:2"]
    assert [f.field_id for f in dates] == ["auto:date:1", "auto:date:2"]
    # Blocks stack upward from the bottom margin.
    assert signatures[0].rect[1] < signatures[1].rect[1]
    assert dates[0].rect[0] > signatures[0].rect[2]


def test_staff_placements_are_kept_and_missing_roles_are_still_covered():
    source = blank_pdf(pages=1)
    digest = hashlib.sha256(source).hexdigest()
    placed = [
        {
            "field_id": "client-signature",
            "field_type": "signature",
            "role": "client",
            "page": 1,
            "rect": [72, 100, 216, 136],
            "page_width": 612,
            "page_height": 792,
            "source_sha256": digest,
        }
    ]
    plan = build_plan(source, signers=[CLIENT, ATTORNEY], positioned_fields=placed)

    by_role = {f.role: f for f in _by_kind(plan, "signature")}
    assert by_role["client"].field_id == "client-signature"
    assert by_role["client"].source == "placed"
    assert by_role["attorney"].source == "fallback"
    assert plan.placement_source == "mixed"


def test_unreadable_bytes_disable_filling_but_never_raise():
    plan = build_plan(b"not a pdf", signers=[CLIENT])

    assert plan.fill_supported is False
    assert plan.fields == [] and plan.positioned_fields() == []
    assert plan.summary()["signature_fields_count"] == 0


def test_unlabelled_lines_are_dealt_to_signers_in_order():
    fields = [
        SimpleNamespace(
            kind="signature",
            role=None,
            label="",
            pdf_field_name=None,
            is_signature_kind=True,
        )
        for _ in range(3)
    ]
    assign_roles(fields, [CLIENT, ATTORNEY])
    assert [f.role for f in fields] == ["client", "attorney", "client"]


def test_manifest_marks_ownership_and_echoes_earlier_signers_values():
    plan = build_plan(acroform_pdf(), signers=[CLIENT, ATTORNEY])
    saved = {"acroform:client_name": ("Jane Client", "s-client")}

    entries = {
        item["field_id"]: item
        for item in manifest(
            plan,
            signers=[CLIENT, ATTORNEY],
            acting_signer_id="s-attorney",
            saved_values=saved,
        )
    }
    assert entries["acroform:client_name"]["value"] == "Jane Client"
    assert entries["acroform:client_name"]["mine"] is False
    assert entries["acroform:state"]["mine"] is True
    assert entries["acroform:state"]["value"] == ""
    # The signature widget matched the client by name; the attorney does not own it.
    assert entries["acroform:client_signature"]["role"] == "client"
    assert entries["acroform:client_signature"]["mine"] is False
    assert "value" not in entries["acroform:client_signature"]
    staff_view = manifest(
        plan, signers=[CLIENT], acting_signer_id=None, include_mine=False
    )
    assert all("mine" not in item for item in staff_view)


def test_field_values_are_validated_against_the_plan():
    plan = build_plan(acroform_pdf(), signers=[CLIENT])

    with pytest.raises(FieldValueError) as unknown:
        validate_field_values(
            plan,
            {"acroform:client_name": "Jane", "acroform:agree": "true", "nope": "x"},
            acting_signer_id="s-client",
        )
    assert any("Unknown field" in problem for problem in unknown.value.problems)

    with pytest.raises(FieldValueError) as missing:
        validate_field_values(plan, {}, acting_signer_id="s-client")
    assert missing.value.problems == [
        "Client name is required",
        "agree must be checked",
        "plan is required",
    ]

    with pytest.raises(FieldValueError, match="one of its options"):
        validate_field_values(
            plan,
            {
                "acroform:client_name": "Jane",
                "acroform:agree": "true",
                "acroform:state": "CA",
            },
            acting_signer_id="s-client",
        )

    cleaned = validate_field_values(
        plan,
        {
            "acroform:client_name": "Jane",
            "acroform:agree": "TRUE",
            "acroform:state": "OK",
            "acroform:plan": "B",
        },
        acting_signer_id="s-client",
    )
    assert cleaned["acroform:agree"] == "true"

    # A value another signer already gave is locked for later signers, but a
    # required field they answered is not demanded again.
    saved = {
        "acroform:client_name": ("Jane", "s-client"),
        "acroform:agree": ("true", "s-client"),
        "acroform:plan": ("A", "s-client"),
    }
    with pytest.raises(FieldValueError, match="another signer"):
        validate_field_values(
            plan,
            {"acroform:client_name": "Someone else"},
            acting_signer_id="s-attorney",
            saved_values=saved,
        )
    assert (
        validate_field_values(
            plan, {}, acting_signer_id="s-attorney", saved_values=saved
        )
        == {}
    )

    with pytest.raises(FieldValueError, match="At most"):
        validate_field_values(
            plan,
            {f"acroform:x{i}": "v" for i in range(201)},
            acting_signer_id="s-client",
        )


def test_saved_values_come_from_the_earliest_signer_and_initials_are_derived():
    first = SimpleNamespace(id="a", sign_order=0, field_values={"f": "one", "g": ""})
    second = SimpleNamespace(id="b", sign_order=1, field_values={"f": "two", "g": "x"})
    assert saved_values_from_signers([second, first]) == {
        "f": ("one", "a"),
        "g": ("x", "b"),
    }
    assert initials_for("Jane Q. Client") == "JQC"
    assert initials_for("mary-anne smith") == "MAS"
    assert initials_for("") == ""


def test_plan_request_placements_persists_placements_and_summary():
    source = flat_agreement_pdf()
    request = SimpleNamespace(
        source_document_sha256=hashlib.sha256(source).hexdigest(),
        positioned_fields=None,
        signing_plan=None,
    )
    signers = [SimpleNamespace(name="Jane Client", role="client", sign_order=0)]

    plan = plan_request_placements(request, source, signers=signers, placements=[])

    assert plan.placement_source == "detected"
    assert [item["field_type"] for item in request.positioned_fields] == [
        "signature",
        "date",
    ]
    assert request.signing_plan["signature_fields_count"] == 2

    duplicate_roles = signers + [
        SimpleNamespace(name="Twin", role="client", sign_order=1)
    ]
    with pytest.raises(PlacementError, match="unique"):
        plan_request_placements(
            request,
            source,
            signers=duplicate_roles,
            placements=[{"field_id": "x", "field_type": "signature", "role": "client"}],
        )
