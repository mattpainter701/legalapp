"""Focused image-source intake tests (no OCR engine/model required)."""

from io import BytesIO

import pytest
from PIL import Image

from app.services.template_intake import (
    TemplateImageError,
    analyze_template_upload,
    prepare_template_source,
)
from app.services.template_ocr import OcrLine, reconstruct_ocr_lines


def _image_bytes(*, fmt="PNG", size=(240, 120), orientation=None) -> bytes:
    image = Image.new("RGB", size, "white")
    output = BytesIO()
    kwargs = {}
    if orientation is not None:
        exif = image.getexif()
        exif[274] = orientation
        kwargs["exif"] = exif.tobytes()
    image.save(output, format=fmt, **kwargs)
    return output.getvalue()


def test_prepare_png_returns_deterministic_pdf_and_safe_contract():
    source = _image_bytes()
    first = prepare_template_source(
        file_bytes=source, filename="filled-form.png", content_type="image/png"
    )
    second = prepare_template_source(
        file_bytes=source, filename="filled-form.png", content_type="image/png"
    )

    assert first.normalized is True
    assert first.filename == "filled-form.pdf"
    assert first.content_type == "application/pdf"
    assert first.format == "pdf"
    assert first.source_bytes.startswith(b"%PDF-")
    assert first.source_bytes == second.source_bytes


def test_prepare_jpeg_honors_exif_orientation():
    # Orientation 6 swaps the visual width/height after transpose.
    source = _image_bytes(fmt="JPEG", size=(120, 240), orientation=6)
    prepared = prepare_template_source(
        file_bytes=source, filename="photo.jpg", content_type="image/jpeg"
    )
    assert prepared.source_bytes.startswith(b"%PDF-")
    # pypdf reads the canonical page geometry in points (150 DPI).
    from pypdf import PdfReader

    page = PdfReader(BytesIO(prepared.source_bytes)).pages[0]
    assert float(page.mediabox.width) == pytest.approx(240 * 72 / 150, abs=0.2)
    assert float(page.mediabox.height) == pytest.approx(120 * 72 / 150, abs=0.2)


def test_prepare_rejects_corrupt_image_with_actionable_error():
    with pytest.raises(TemplateImageError, match="clear PNG, JPEG, TIFF, or WebP"):
        prepare_template_source(
            file_bytes=b"not-an-image", filename="scan.webp", content_type="image/webp"
        )


def test_analysis_keeps_normalized_source_private_and_api_safe(monkeypatch):
    # Avoid importing/running RapidOCR: feed the analyzer a deterministic empty
    # OCR result and verify image preparation still reaches the normal PDF path.
    from app.services import template_intake
    from app.services.template_ocr import PdfOcrResult

    monkeypatch.setattr(
        template_intake,
        "ocr_pdf",
        lambda _content: PdfOcrResult("", (), 1, 1, 0.0, False),
    )
    analysis = analyze_template_upload(
        file_bytes=_image_bytes(), filename="scan.png", content_type="image/png"
    )
    payload = analysis.as_dict()
    assert analysis._normalized_source_content_type == "application/pdf"
    assert analysis._normalized_source_filename == "scan.pdf"
    assert analysis._normalized_source_bytes.startswith(b"%PDF-")
    assert "_normalized_source_bytes" not in payload
    assert payload["format"] == "pdf"


def test_reconstruct_ocr_lines_joins_same_row_label_and_value_with_union_box():
    lines = reconstruct_ocr_lines(
        (
            OcrLine(0, "Applicant Name:", 0.95, (40, 700, 150, 720)),
            OcrLine(0, "Ada Lovelace", 0.72, (160, 700, 280, 720)),
            OcrLine(0, "Case Number:", 0.9, (40, 650, 130, 670)),
            OcrLine(0, "CV-42", 0.8, (140, 650, 190, 670)),
        )
    )
    assert [line.text for line in lines] == [
        "Applicant Name: Ada Lovelace",
        "Case Number: CV-42",
    ]
    assert lines[0].rect == (40, 700, 280, 720)
    assert lines[0].score == pytest.approx(0.72)


def test_reconstruct_ocr_lines_recovers_common_labels_when_ocr_drops_colons():
    lines = reconstruct_ocr_lines(
        (
            OcrLine(0, "Date of birth", 0.96, (40, 700, 130, 720)),
            OcrLine(0, "12/10/1815", 0.71, (145, 700, 225, 720)),
            OcrLine(0, "Mailing address", 0.94, (40, 650, 145, 670)),
            OcrLine(0, "123 Main Street", 0.76, (160, 650, 270, 670)),
        )
    )

    assert [line.text for line in lines] == [
        "Date of birth: 12/10/1815",
        "Mailing address: 123 Main Street",
    ]


def test_text_layer_form_with_page_image_runs_ocr_for_handwritten_values(monkeypatch):
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    from app.services import template_intake
    from app.services.template_ocr import PdfOcrResult

    source = BytesIO()
    pdf = canvas.Canvas(source, pagesize=(612, 792))
    pdf.drawImage(
        ImageReader(Image.open(BytesIO(_image_bytes(size=(1224, 1584))))),
        0,
        0,
        612,
        792,
    )
    pdf.drawString(40, 730, "Applicant Name:")
    pdf.drawString(40, 690, "Date:")
    pdf.save()
    calls = []

    def fake_ocr(content):
        calls.append(content)
        return PdfOcrResult(
            "Applicant Name: Ada Lovelace\nDate: August 25, 2026",
            (
                OcrLine(0, "Applicant Name:", 0.95, (40, 720, 150, 740)),
                OcrLine(0, "Ada Lovelace", 0.78, (160, 720, 280, 740)),
                OcrLine(0, "Date:", 0.95, (40, 680, 90, 700)),
                OcrLine(0, "August 25, 2026", 0.81, (100, 680, 220, 700)),
            ),
            1,
            1,
            0.865,
            False,
        )

    monkeypatch.setattr(template_intake, "ocr_pdf", fake_ocr)
    analysis = analyze_template_upload(
        file_bytes=source.getvalue(),
        filename="filled-form.pdf",
        content_type="application/pdf",
    )

    assert calls
    fields = {field["name"]: field for field in analysis.variable_schema["fields"]}
    assert fields["client_name"]["pdf_overlay"]["source_kind"] == "ocr"
    assert "Applicant Name: {{client_name}}" in analysis.body


def test_large_page_image_detector_ignores_logo_graphics():
    from pypdf import PdfReader
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    from app.services.template_intake import _pdf_has_large_page_image

    source = BytesIO()
    pdf = canvas.Canvas(source, pagesize=(612, 792))
    pdf.drawImage(
        ImageReader(Image.open(BytesIO(_image_bytes(size=(1200, 1600))))),
        0,
        0,
        612,
        792,
    )
    pdf.save()
    assert _pdf_has_large_page_image(PdfReader(BytesIO(source.getvalue()))) is True

    logo_source = BytesIO()
    logo_pdf = canvas.Canvas(logo_source, pagesize=(612, 792))
    logo_pdf.drawImage(
        ImageReader(Image.open(BytesIO(_image_bytes(size=(1200, 240))))),
        40,
        700,
        240,
        48,
    )
    logo_pdf.save()
    assert _pdf_has_large_page_image(PdfReader(BytesIO(logo_source.getvalue()))) is False


def test_mixed_fillable_scan_merges_unique_ocr_fields_without_acro_overlap(monkeypatch):
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    from app.services import template_intake
    from app.services.template_ocr import PdfOcrResult

    source = BytesIO()
    pdf = canvas.Canvas(source, pagesize=(612, 792))
    pdf.drawImage(
        ImageReader(Image.open(BytesIO(_image_bytes(size=(1224, 1584))))),
        0,
        0,
        612,
        792,
    )
    pdf.drawString(40, 730, "Applicant Name:")
    pdf.drawString(40, 680, "Address:")
    pdf.acroForm.textfield(name="Applicant", x=40, y=710, width=180, height=18)
    pdf.save()

    monkeypatch.setattr(
        template_intake,
        "ocr_pdf",
        lambda _content: PdfOcrResult(
            "Applicant Name: Ada Lovelace\nAddress: 123 Main Street",
            (
                OcrLine(0, "Applicant Name:", 0.95, (40, 710, 150, 730)),
                OcrLine(0, "Ada Lovelace", 0.8, (160, 710, 280, 730)),
                OcrLine(0, "Address:", 0.92, (40, 660, 100, 680)),
                OcrLine(0, "123 Main Street", 0.78, (110, 660, 250, 680)),
            ),
            1,
            1,
            0.8625,
            False,
        ),
    )
    analysis = analyze_template_upload(
        file_bytes=source.getvalue(), filename="mixed.pdf", content_type="application/pdf"
    )
    fields = analysis.variable_schema["fields"]
    names = [field["name"] for field in fields]
    assert analysis.variable_schema["source"] == "pdf_acroform_ocr"
    assert analysis.variable_schema["detection"]["method"] == "fillable_pdf_ocr"
    assert names.count("applicant") == 1
    assert "client_street" in names
    assert "Client Street: {{client_street}}" in analysis.body
