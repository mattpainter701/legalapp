"""Focused regression coverage for source-backed AcroForm rendering."""

from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    DictionaryObject,
    NameObject,
    NumberObject,
    RectangleObject,
    TextStringObject,
)
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.services import pdf_templates as pdf_template_service
from app.services.pdf_templates import (
    TemplatePdfError,
    discover_pdf_fields,
    fill_pdf_template,
)


def _multiline_pdf(*, width: float = 220, height: float = 80) -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    pdf.drawString(72, 740, "Narrative")
    pdf.acroForm.textfield(
        name="narrative",
        x=72,
        y=600,
        width=width,
        height=height,
        fieldFlags="multiline",
    )
    pdf.save()
    return output.getvalue()


def _repeated_field_pdf() -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    for page_number in (1, 2):
        pdf.drawString(72, 740, f"Page {page_number}")
        pdf.acroForm.textfield(
            name="case_number",
            x=140,
            y=700,
            width=180,
            height=24,
        )
        pdf.showPage()
    pdf.save()
    return output.getvalue()


def _write(writer: PdfWriter) -> bytes:
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _clone(source: bytes) -> PdfWriter:
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(BytesIO(source)))
    return writer


def _schema(source: bytes) -> dict:
    return {"fields": discover_pdf_fields(source)}


def test_pdf_flatten_wraps_long_text_and_rejects_overlong_text() -> None:
    source = _multiline_pdf()
    value = (
        "This carefully reviewed client narrative wraps across several lines "
        "without losing any words."
    )

    flattened = fill_pdf_template(
        source,
        variable_schema=_schema(source),
        variables={"narrative": value},
        flatten=True,
    )
    extracted_lines = [
        line
        for line in (
            PdfReader(BytesIO(flattened)).pages[0].extract_text() or ""
        ).splitlines()
        if line and line != "Narrative"
    ]
    assert len(extracted_lines) >= 2
    assert " ".join(extracted_lines) == value

    with pytest.raises(TemplatePdfError) as exc_info:
        fill_pdf_template(
            source,
            variable_schema=_schema(source),
            variables={"narrative": " ".join(["overlong"] * 500)},
            flatten=True,
        )
    assert str(exc_info.value) == (
        "Value for PDF field 'narrative' does not fit; shorten it or enlarge "
        "the field in the source PDF, then re-upload."
    )


def test_pdf_flatten_retries_smaller_font_before_rejecting_narrow_field() -> None:
    source = _multiline_pdf(width=18, height=40)

    flattened = fill_pdf_template(
        source,
        variable_schema=_schema(source),
        variables={"narrative": "ABCDEF"},
        flatten=True,
    )

    extracted = PdfReader(BytesIO(flattened)).pages[0].extract_text() or ""
    rendered_value = "".join(
        line for line in extracted.splitlines() if line and line != "Narrative"
    )
    assert rendered_value == "ABCDEF"


def test_pdf_flatten_bounds_work_for_ten_thousand_character_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from reportlab.pdfbase import pdfmetrics

    source = _multiline_pdf(width=18, height=40)
    schema = _schema(source)
    original_string_width = pdfmetrics.stringWidth
    call_count = 0
    call_limit = 1_000

    def counted_string_width(*args, **kwargs) -> float:
        nonlocal call_count
        call_count += 1
        if call_count > call_limit:
            raise AssertionError(
                "PDF wrapping exceeded its bounded string-width probe budget"
            )
        return original_string_width(*args, **kwargs)

    monkeypatch.setattr(pdfmetrics, "stringWidth", counted_string_width)

    with pytest.raises(TemplatePdfError) as exc_info:
        fill_pdf_template(
            source,
            variable_schema=schema,
            variables={"narrative": "A" * 10_000},
            flatten=True,
        )

    assert "does not fit; shorten it or enlarge the field" in str(exc_info.value)
    assert call_count <= call_limit


def test_pdf_flatten_bounds_huge_widget_height_and_suffix_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from reportlab.pdfbase import pdfmetrics

    source = _multiline_pdf(width=18, height=10_000_000)
    schema = _schema(source)
    original_string_width = pdfmetrics.stringWidth
    call_count = 0
    call_limit = 15_000

    def counted_string_width(*args, **kwargs) -> float:
        nonlocal call_count
        call_count += 1
        if call_count > call_limit:
            raise AssertionError(
                "Huge PDF geometry bypassed the rendered-line work bound"
            )
        return original_string_width(*args, **kwargs)

    monkeypatch.setattr(pdfmetrics, "stringWidth", counted_string_width)

    with pytest.raises(TemplatePdfError, match="does not fit; shorten it"):
        fill_pdf_template(
            source,
            variable_schema=schema,
            variables={"narrative": "A" * 10_000},
            flatten=True,
        )

    assert call_count <= call_limit


def test_pdf_fill_rejects_over_cap_value_before_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from reportlab.pdfbase import pdfmetrics

    source = _multiline_pdf(width=18, height=10_000_000)
    schema = _schema(source)
    call_count = 0

    def unexpected_string_width(*args, **kwargs) -> float:
        nonlocal call_count
        call_count += 1
        raise AssertionError("Over-cap PDF value reached the layout engine")

    monkeypatch.setattr(pdfmetrics, "stringWidth", unexpected_string_width)

    with pytest.raises(TemplatePdfError, match="10,000-character limit"):
        fill_pdf_template(
            source,
            variable_schema=schema,
            variables={"narrative": "A" * 10_001},
            flatten=True,
        )

    assert call_count == 0


def test_pdf_flatten_shares_width_probe_budget_across_widgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _repeated_field_pdf()
    monkeypatch.setattr(
        pdf_template_service,
        "_MAX_PDF_WIDTH_PROBES_PER_RENDER",
        1,
    )

    with pytest.raises(TemplatePdfError, match="requires too much layout work"):
        fill_pdf_template(
            source,
            variable_schema=_schema(source),
            variables={"case_number": "A"},
            flatten=True,
        )


def test_pdf_flatten_preserves_rotated_and_cropped_repeated_field_pages() -> None:
    writer = _clone(_repeated_field_pdf())
    writer.pages[0].rotate(90)
    writer.pages[1].cropbox = RectangleObject([50, 650, 400, 770])
    source = _write(writer)

    fields = discover_pdf_fields(source)
    assert [field["pdf_field_name"] for field in fields] == ["case_number"]

    flattened = fill_pdf_template(
        source,
        variable_schema={"fields": fields},
        variables={"case_number": "CV-2026-0042"},
        flatten=True,
    )
    reader = PdfReader(BytesIO(flattened))

    assert len(reader.pages) == 2
    assert reader.get_fields() is None
    assert reader.pages[0].rotation == 90
    assert [float(value) for value in reader.pages[1].cropbox] == [
        50.0,
        650.0,
        400.0,
        770.0,
    ]
    assert all("CV-2026-0042" in (page.extract_text() or "") for page in reader.pages)


@pytest.mark.parametrize(
    ("unsafe_content", "message"),
    [
        ("xfa", r"Active PDF content \(/XFA\) is not allowed"),
        (
            "embedded_attachment",
            r"Active PDF content \(/EmbeddedFiles\) is not allowed",
        ),
        ("automatic_action", r"Active PDF content \(/OpenAction\) is not allowed"),
        ("external_uri", r"PDF action /URI is not allowed"),
    ],
)
def test_pdf_discovery_rejects_unsupported_active_content(
    unsafe_content: str, message: str
) -> None:
    writer = _clone(_multiline_pdf())
    if unsafe_content == "xfa":
        acroform = writer._root_object["/AcroForm"].get_object()
        acroform[NameObject("/XFA")] = TextStringObject("unsupported-xfa")
    elif unsafe_content == "embedded_attachment":
        writer.add_attachment("embedded.txt", b"not allowed in a template")
    elif unsafe_content == "automatic_action":
        writer._root_object[NameObject("/OpenAction")] = DictionaryObject(
            {NameObject("/S"): NameObject("/GoTo")}
        )
    else:
        writer.add_uri(
            0,
            "https://attacker.invalid/collect",
            RectangleObject([72, 700, 240, 724]),
        )

    with pytest.raises(TemplatePdfError, match=message):
        discover_pdf_fields(_write(writer))


def test_pdf_discovery_rejects_rotated_widget_appearance() -> None:
    writer = _clone(_multiline_pdf())
    widget = writer.pages[0]["/Annots"][0].get_object()
    widget[NameObject("/MK")] = DictionaryObject({NameObject("/R"): NumberObject(90)})

    with pytest.raises(TemplatePdfError, match="rotated widget appearance"):
        discover_pdf_fields(_write(writer))
