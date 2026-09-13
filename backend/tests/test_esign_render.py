"""The executed copy: filled values, stamped signatures, nothing left editable."""

from io import BytesIO

import pytest
from pypdf import PdfReader

from app.services.esign.plan import SignerRef, build_plan
from app.services.esign.render import Stamp, render_executed_pdf
from app.services.pdf_templates import TemplatePdfError, _open_pdf, _widgets
from tests.esign_pdf_fixtures import acroform_pdf, flat_agreement_pdf

CLIENT = SignerRef("s-client", "Jane Client", "client", 0)
CAPTION = "Signed electronically 2026-09-12T10:00:00Z · 0123abcd"


def _text(content: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(content)).pages)


def test_acroform_values_and_signature_are_painted_and_the_form_is_flattened():
    source = acroform_pdf()
    plan = build_plan(source, signers=[CLIENT])
    signature = plan.signature_fields[0]

    output = render_executed_pdf(
        source,
        field_values={
            "acroform:client_name": "Jane Q. Client",
            "acroform:agree": "true",
            "acroform:state": "OK",
            "acroform:plan": "B",
            "auto:ignored": "not an acroform id",
        },
        stamps=[
            Stamp(
                signature.page, signature.rect, "signature", "Jane Q. Client", CAPTION
            )
        ],
    )

    reader = _open_pdf(output)
    assert _widgets(reader) == []
    assert "/AcroForm" not in reader.trailer["/Root"]
    assert reader.get_fields() in (None, {})
    text = _text(output)
    assert "Jane Q. Client" in text
    assert "OK" in text
    assert "Signed electronically" in text
    assert "0123abcd" in text


def test_flat_pdf_gets_date_and_initials_stamps_without_a_form_pass():
    source = flat_agreement_pdf()

    output = render_executed_pdf(
        source,
        field_values={},
        stamps=[
            Stamp(1, (156.0, 196.0, 356.0, 224.0), "signature", "Jane Client", CAPTION),
            Stamp(1, (389.0, 196.0, 463.0, 220.0), "date", "September 12, 2026"),
            Stamp(1, (72.0, 138.0, 300.0, 166.0), "initials", "JC", None),
        ],
    )

    text = _text(output)
    assert "Jane Client" in text
    assert "September 12, 2026" in text
    assert "JC" in text
    assert "Client Signature" in text  # the original page content survives


def test_a_short_box_drops_the_caption_and_a_wide_name_still_fits():
    output = render_executed_pdf(
        flat_agreement_pdf(),
        field_values={},
        stamps=[
            Stamp(
                1,
                (72.0, 400.0, 172.0, 418.0),
                "signature",
                "Bartholomew Montgomery-Featherstonehaugh",
                CAPTION,
            )
        ],
    )

    text = _text(output)
    assert "Bartholomew Montgomery-Featherstonehaugh" in text
    assert "Signed electronically" not in text


def test_names_outside_cp1252_render_through_the_unicode_fallback():
    output = render_executed_pdf(
        flat_agreement_pdf(),
        field_values={},
        stamps=[
            Stamp(
                1, (72.0, 400.0, 300.0, 430.0), "signature", "Nguyễn Văn Ánh", CAPTION
            )
        ],
    )

    assert len(_open_pdf(output).pages) == 1


def test_stamps_on_a_missing_page_are_rejected():
    with pytest.raises(TemplatePdfError, match="page"):
        render_executed_pdf(
            flat_agreement_pdf(),
            field_values={},
            stamps=[Stamp(4, (72.0, 400.0, 300.0, 430.0), "signature", "Jane", None)],
        )


def test_values_that_cannot_fit_their_widget_are_reported_not_truncated():
    with pytest.raises(TemplatePdfError, match="does not fit"):
        render_executed_pdf(
            acroform_pdf(),
            field_values={"acroform:client_name": "x" * 5000},
            stamps=[],
        )


def _signature_png(width=300, height=90) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.line(
        [(10, 70), (90, 20), (150, 75), (280, 15)], fill=(20, 20, 80, 255), width=5
    )
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _images_on_page(page) -> list:
    resources = page.get("/Resources") or {}
    xobjects = resources.get("/XObject") or {}
    return [
        name
        for name, ref in xobjects.items()
        if ref.get_object().get("/Subtype") == "/Image"
    ]


def test_a_drawn_signature_is_painted_as_an_image_with_the_caption():
    source = flat_agreement_pdf()
    output = render_executed_pdf(
        source,
        field_values={},
        stamps=[
            Stamp(
                1,
                (156.0, 196.0, 356.0, 224.0),
                "signature",
                "Jane Client",
                CAPTION,
                image=_signature_png(),
            ),
            Stamp(1, (400.0, 196.0, 500.0, 224.0), "date", "September 12, 2026"),
        ],
    )

    reader = _open_pdf(output)
    assert _images_on_page(reader.pages[0])
    text = _text(output)
    # The drawing replaces the typed name on the line; the evidence caption stays.
    assert "Jane Client" not in text
    assert "Signed electronically" in text
    assert "September 12, 2026" in text


def test_initials_keep_the_typed_form_even_when_a_drawing_exists():
    source = flat_agreement_pdf()
    output = render_executed_pdf(
        source,
        field_values={},
        stamps=[
            Stamp(
                1,
                (400.0, 300.0, 460.0, 330.0),
                "initials",
                "JC",
                None,
                image=_signature_png(),
            )
        ],
    )
    reader = _open_pdf(output)
    assert not _images_on_page(reader.pages[0])
    assert "JC" in _text(output)
