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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bounds, message",
    [
        ({"timeout_seconds": 4.0}, "timeout must be between"),
        ({"timeout_seconds": 301.0}, "timeout must be between"),
        ({"max_output_bytes": 0}, "output bound is invalid"),
        ({"max_output_bytes": 200 * 1024 * 1024}, "output bound is invalid"),
        ({"max_pages": 0}, "page bound is invalid"),
        ({"max_pages": 1_001}, "page bound is invalid"),
    ],
)
async def test_docx_to_pdf_rejects_bounds_outside_the_permitted_range(bounds, message):
    """The caller-supplied limits are the safety envelope, so they are checked
    before any converter runs."""

    with pytest.raises(ValueError, match=message):
        await docx_to_pdf_bytes(_docx(), **bounds)


@pytest.mark.asyncio
async def test_docx_to_pdf_rejects_a_document_that_fails_its_safety_check():
    with pytest.raises(DocxToPdfError, match="failed its safety check"):
        await docx_to_pdf_bytes(b"not a docx package")


@pytest.mark.asyncio
async def test_docx_to_pdf_kills_and_fails_closed_when_the_converter_hangs(
    tmp_path, monkeypatch
):
    executable = tmp_path / "libreoffice"
    executable.write_text("converter")
    killed = {}

    class _HangingConverter:
        returncode = None

        async def communicate(self):
            if killed:
                return b"", b""
            raise TimeoutError

        def kill(self):
            killed["kill"] = True

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _HangingConverter()

    monkeypatch.setattr(
        "app.services.docx_to_pdf.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    with pytest.raises(DocxToPdfError, match="timed out"):
        await docx_to_pdf_bytes(_docx(), executable=str(executable))

    assert killed == {"kill": True}


@pytest.mark.asyncio
async def test_docx_to_pdf_never_leaks_converter_output_on_failure(
    tmp_path, monkeypatch
):
    executable = tmp_path / "libreoffice"
    executable.write_text("converter")

    class _FailedConverter:
        returncode = 1

        async def communicate(self):
            return b"/home/runner/secret-matter.docx", b"host path detail"

        def kill(self):
            pass

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FailedConverter()

    monkeypatch.setattr(
        "app.services.docx_to_pdf.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    with pytest.raises(DocxToPdfError) as error:
        await docx_to_pdf_bytes(_docx(), executable=str(executable))

    assert "could not be converted" in str(error.value)
    assert "secret-matter" not in str(error.value)
    assert "host path detail" not in str(error.value)


@pytest.mark.asyncio
async def test_docx_to_pdf_fails_closed_when_the_converter_writes_nothing(
    tmp_path, monkeypatch
):
    executable = tmp_path / "libreoffice"
    executable.write_text("converter")

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _CompletedConverter()

    monkeypatch.setattr(
        "app.services.docx_to_pdf.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    with pytest.raises(DocxToPdfError, match="did not produce a PDF"):
        await docx_to_pdf_bytes(_docx(), executable=str(executable))


@pytest.mark.asyncio
async def test_docx_to_pdf_rejects_output_beyond_the_size_bound(tmp_path, monkeypatch):
    executable = tmp_path / "libreoffice"
    executable.write_text("converter")

    async def fake_create_subprocess_exec(*args, **kwargs):
        output_dir = Path(args[args.index("--outdir") + 1])
        (output_dir / "source.pdf").write_bytes(_pdf())
        return _CompletedConverter()

    monkeypatch.setattr(
        "app.services.docx_to_pdf.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    with pytest.raises(DocxToPdfError, match="exceeds the permitted size"):
        await docx_to_pdf_bytes(
            _docx(), executable=str(executable), max_output_bytes=16
        )


@pytest.mark.asyncio
async def test_docx_to_pdf_rejects_output_beyond_the_page_bound(tmp_path, monkeypatch):
    executable = tmp_path / "libreoffice"
    executable.write_text("converter")

    def _two_page_pdf() -> bytes:
        output = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_blank_page(width=612, height=792)
        writer.write(output)
        return output.getvalue()

    async def fake_create_subprocess_exec(*args, **kwargs):
        output_dir = Path(args[args.index("--outdir") + 1])
        (output_dir / "source.pdf").write_bytes(_two_page_pdf())
        return _CompletedConverter()

    monkeypatch.setattr(
        "app.services.docx_to_pdf.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    with pytest.raises(DocxToPdfError, match="failed validation"):
        await docx_to_pdf_bytes(_docx(), executable=str(executable), max_pages=1)


@pytest.mark.asyncio
async def test_docx_to_pdf_rejects_active_content_in_the_converted_pdf(
    tmp_path, monkeypatch
):
    """A PDF that runs something on open must never cross the trust boundary,
    however it came to be produced."""

    from pypdf.generic import DictionaryObject, NameObject

    def _auto_action_pdf() -> bytes:
        output = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer._root_object[NameObject("/OpenAction")] = DictionaryObject()
        writer.write(output)
        return output.getvalue()

    executable = tmp_path / "libreoffice"
    executable.write_text("converter")

    async def fake_create_subprocess_exec(*args, **kwargs):
        output_dir = Path(args[args.index("--outdir") + 1])
        (output_dir / "source.pdf").write_bytes(_auto_action_pdf())
        return _CompletedConverter()

    monkeypatch.setattr(
        "app.services.docx_to_pdf.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    with pytest.raises(DocxToPdfError, match="failed validation"):
        await docx_to_pdf_bytes(_docx(), executable=str(executable))


@pytest.mark.asyncio
async def test_docx_to_pdf_is_byte_identical_for_the_same_source(tmp_path, monkeypatch):
    """Preview and save must produce the same bytes, so conversion is
    re-serialised with fixed metadata rather than LibreOffice's wall clock."""

    executable = tmp_path / "libreoffice"
    executable.write_text("converter")
    stamp = iter(("first", "second"))

    async def fake_create_subprocess_exec(*args, **kwargs):
        output = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_metadata({"/Producer": f"LibreOffice {next(stamp)}"})
        writer.write(output)
        output_dir = Path(args[args.index("--outdir") + 1])
        (output_dir / "source.pdf").write_bytes(output.getvalue())
        return _CompletedConverter()

    monkeypatch.setattr(
        "app.services.docx_to_pdf.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    source = _docx()
    first = await docx_to_pdf_bytes(source, executable=str(executable))
    second = await docx_to_pdf_bytes(source, executable=str(executable))

    assert first == second
