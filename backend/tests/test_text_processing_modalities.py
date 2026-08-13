from io import BytesIO

import pytest
from docx import Document
from reportlab.pdfgen import canvas

from app.utils.text_processing import extract_text


def _docx_bytes(text: str) -> bytes:
    output = BytesIO()
    document = Document()
    document.add_paragraph(text)
    document.save(output)
    return output.getvalue()


def _pdf_bytes(text: str) -> bytes:
    output = BytesIO()
    page = canvas.Canvas(output)
    page.drawString(72, 720, text)
    page.save()
    return output.getvalue()


@pytest.mark.parametrize(
    ("filename", "content_type", "payload", "expected"),
    [
        ("canary.txt", "text/plain", b"TXT_CANARY_8421", "TXT_CANARY_8421"),
        (
            "canary.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _docx_bytes("DOCX_CANARY_8421"),
            "DOCX_CANARY_8421",
        ),
        (
            "canary.pdf",
            "application/pdf",
            _pdf_bytes("PDF_CANARY_8421"),
            "PDF_CANARY_8421",
        ),
    ],
    ids=("txt", "docx", "pdf"),
)
def test_supported_document_text_modalities(
    filename: str, content_type: str, payload: bytes, expected: str
):
    assert expected in extract_text(payload, content_type, filename)


def test_legacy_doc_fails_with_conversion_guidance():
    with pytest.raises(ValueError, match="convert to DOCX, PDF, or TXT"):
        extract_text(b"legacy", "application/msword", "agreement.doc")
