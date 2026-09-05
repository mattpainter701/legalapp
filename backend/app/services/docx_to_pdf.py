"""Bounded, fail-closed conversion of reviewed DOCX bytes to PDF.

LibreOffice runs as the container's non-root application user, receives no
shell input, uses a fresh private profile, and can only read/write a disposable
directory. The resulting PDF is parsed before it crosses the trust boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import shutil
import tempfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, ByteStringObject

from app.services.docx_templates import TemplateDocxError, validate_docx_package


class DocxToPdfError(RuntimeError):
    """A sanitized conversion or output-validation failure."""


async def docx_to_pdf_bytes(
    content: bytes,
    *,
    executable: str = "/usr/bin/libreoffice",
    timeout_seconds: float = 60.0,
    max_output_bytes: int = 50 * 1024 * 1024,
    max_pages: int = 1_000,
) -> bytes:
    """Convert safe DOCX bytes with a private headless LibreOffice profile."""

    if not 5 <= timeout_seconds <= 300:
        raise ValueError("conversion timeout must be between 5 and 300 seconds")
    if not 1 <= max_output_bytes <= 100 * 1024 * 1024:
        raise ValueError("conversion output bound is invalid")
    if not 1 <= max_pages <= 1_000:
        raise ValueError("conversion page bound is invalid")
    try:
        validate_docx_package(content)
    except TemplateDocxError as exc:
        raise DocxToPdfError("The Word document failed its safety check.") from exc

    converter = Path(executable)
    # Debian exposes the packaged launcher through /usr/bin/libreoffice. The
    # configured path is root-owned image configuration, not customer input,
    # so the distribution's launcher/symlink is safe to follow.
    if not converter.is_absolute() or not converter.is_file():
        raise DocxToPdfError("Word-to-PDF conversion is unavailable.")

    root = Path(tempfile.mkdtemp(prefix="lawhand-docx-pdf-"))
    try:
        root.chmod(0o700)
        source = root / "source.docx"
        source.write_bytes(content)
        source.chmod(0o600)
        profile = root / "profile"
        profile.mkdir(mode=0o700)
        environment = {
            "HOME": str(root),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            "TMPDIR": str(root),
        }
        process = await asyncio.create_subprocess_exec(
            str(converter),
            "--headless",
            "--safe-mode",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--nofirststartwizard",
            f"-env:UserInstallation={profile.as_uri()}",
            "--convert-to",
            "pdf:writer_pdf_Export",
            "--outdir",
            str(root),
            str(source),
            cwd=root,
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout_seconds
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise DocxToPdfError("Word-to-PDF conversion timed out.") from exc
        if process.returncode != 0:
            # Converter output can contain customer filenames and host paths;
            # never include it in the public exception.
            del stdout, stderr
            raise DocxToPdfError("The Word document could not be converted to PDF.")
        output = root / "source.pdf"
        if not output.is_file() or output.is_symlink():
            raise DocxToPdfError("The Word document did not produce a PDF.")
        size = output.stat().st_size
        if not 1 <= size <= max_output_bytes:
            raise DocxToPdfError("The converted PDF exceeds the permitted size.")
        converted = output.read_bytes()
        if not converted.startswith(b"%PDF-"):
            raise DocxToPdfError("The converted output is not a PDF.")
        try:
            reader = PdfReader(output, strict=True)
            if reader.is_encrypted or not 1 <= len(reader.pages) <= max_pages:
                raise ValueError("invalid PDF boundary")
            root_object = reader.trailer["/Root"]
            if any(key in root_object for key in ("/OpenAction", "/AA")):
                raise ValueError("active PDF output")
            names = root_object.get("/Names")
            if names and any(key in names for key in ("/JavaScript", "/EmbeddedFiles")):
                raise ValueError("active PDF output")

            # LibreOffice may stamp wall-clock metadata into otherwise identical
            # conversions. Re-serialise with fixed metadata and a source-derived
            # file identifier so preview and save produce byte-identical evidence.
            writer = PdfWriter(clone_from=reader)
            writer.metadata = {
                "/Producer": "LawHand bounded DOCX-to-PDF converter",
            }
            source_identifier = hashlib.sha256(content).digest()
            writer._ID = ArrayObject(  # noqa: SLF001 - pypdf has no public ID setter
                [
                    ByteStringObject(source_identifier),
                    ByteStringObject(source_identifier),
                ]
            )
            normalized = io.BytesIO()
            writer.write(normalized)
            result = normalized.getvalue()
            if len(result) > max_output_bytes:
                raise ValueError("normalized PDF boundary")
            normalized_reader = PdfReader(io.BytesIO(result), strict=True)
            if normalized_reader.is_encrypted or len(normalized_reader.pages) != len(
                reader.pages
            ):
                raise ValueError("normalized PDF validation")
        except Exception as exc:
            raise DocxToPdfError("The converted PDF failed validation.") from exc
        return result
    finally:
        shutil.rmtree(root, ignore_errors=True)
