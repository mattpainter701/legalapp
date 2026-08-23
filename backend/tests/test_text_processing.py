from io import BytesIO

from docx import Document
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.utils.text_processing import extract_text_from_path


def test_extract_text_from_path_reads_docx_without_a_bytes_copy(tmp_path):
    path = tmp_path / "sample.docx"
    document = Document()
    document.add_paragraph("Path-backed Word extraction")
    document.save(path)

    extracted = extract_text_from_path(
        path,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        path.name,
    )

    assert extracted == "Path-backed Word extraction"


def test_extract_text_from_path_reads_pdf_without_a_bytes_copy(tmp_path):
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    pdf.drawString(72, 720, "Path-backed PDF extraction")
    pdf.save()
    path = tmp_path / "sample.pdf"
    path.write_bytes(output.getvalue())

    extracted = extract_text_from_path(path, "application/pdf", path.name)

    assert "Path-backed PDF extraction" in extracted
