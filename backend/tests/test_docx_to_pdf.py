from io import BytesIO
from pathlib import Path

import pytest
from docx import Document
from pypdf import PdfWriter

from app.services.docx_to_pdf import DocxToPdfError, docx_to_pdf_bytes


def _docx() -> bytes:
    output = BytesIO()
    document = Document()
    document.add_paragraph("Agreement")
    document.save(output)
    return output.getvalue()


def _pdf() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(output)
    return output.getvalue()


class _CompletedConverter:
    returncode = 0

    async def communicate(self):
        return b"converted", b""

    def kill(self):
        self.returncode = -9


@pytest.mark.asyncio
async def test_docx_to_pdf_runs_converter_without_shell_and_validates_output(
    tmp_path, monkeypatch
):
    executable = tmp_path / "libreoffice"
    executable.write_text("converter")
    observed = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        output_dir = Path(args[args.index("--outdir") + 1])
        (output_dir / "source.pdf").write_bytes(_pdf())
        return _CompletedConverter()

    monkeypatch.setattr(
        "app.services.docx_to_pdf.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    result = await docx_to_pdf_bytes(_docx(), executable=str(executable))

    assert result.startswith(b"%PDF-")
    assert "--safe-mode" in observed["args"]
    assert observed["kwargs"]["stdin"] is not None
    assert "shell" not in observed["kwargs"]
    assert observed["kwargs"]["env"]["PATH"] == "/usr/bin:/bin"


@pytest.mark.asyncio
async def test_docx_to_pdf_fails_closed_when_converter_is_unavailable(tmp_path):
    with pytest.raises(DocxToPdfError, match="conversion is unavailable"):
        await docx_to_pdf_bytes(
            _docx(), executable=str(tmp_path / "missing-libreoffice")
        )


@pytest.mark.asyncio
async def test_docx_to_pdf_rejects_non_pdf_converter_output(tmp_path, monkeypatch):
    executable = tmp_path / "libreoffice"
    executable.write_text("converter")

    async def fake_create_subprocess_exec(*args, **kwargs):
        output_dir = Path(args[args.index("--outdir") + 1])
        (output_dir / "source.pdf").write_bytes(b"not a PDF")
        return _CompletedConverter()

    monkeypatch.setattr(
        "app.services.docx_to_pdf.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    with pytest.raises(DocxToPdfError, match="not a PDF"):
        await docx_to_pdf_bytes(_docx(), executable=str(executable))
