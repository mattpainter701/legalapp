from __future__ import annotations

import io
import zipfile

import pytest

import app.services.cloud_docx_snapshot as snapshot_module
from app.services.cloud_docx_snapshot import (
    CloudDocxSnapshotError,
    inspect_cloud_docx_snapshot,
)
from tests.test_cloud_docx_snapshot import (
    _docx_bytes,
    _rewrite_docx,
    _with_external_relationship,
)


def _assert_code(code, source, **kwargs):
    with pytest.raises(CloudDocxSnapshotError) as exc_info:
        inspect_cloud_docx_snapshot(source, **kwargs)
    assert exc_info.value.code == code


def test_snapshot_rejects_invalid_input_envelopes(monkeypatch):
    _assert_code("invalid_source_type", bytearray(b"PK"))
    _assert_code("empty_document", b"")
    monkeypatch.setattr(snapshot_module, "MAX_DOCX_BYTES", 3)
    _assert_code("document_too_large", b"PKxx")
    monkeypatch.setattr(snapshot_module, "MAX_DOCX_BYTES", 25 * 1024 * 1024)
    _assert_code("unsupported_extension", _docx_bytes(), filename="draft.doc")
    _assert_code("invalid_preview_limit", _docx_bytes(), max_preview_chars=10)
    _assert_code(
        "encrypted_or_legacy_document",
        snapshot_module._OLE_COMPOUND_FILE_MAGIC + b"legacy",
    )
    _assert_code("invalid_docx_package", b"PK-not-a-zip")


def test_snapshot_reports_no_extractable_text():
    source = _docx_bytes("")

    result = inspect_cloud_docx_snapshot(source)

    assert "no extractable text" in result.review_text
    assert result.preview_truncated is False


def test_snapshot_rejects_excessive_or_duplicate_package_parts(monkeypatch):
    source = _docx_bytes()
    monkeypatch.setattr(snapshot_module, "MAX_ARCHIVE_ENTRIES", 1)
    _assert_code("package_too_complex", source)
    monkeypatch.setattr(snapshot_module, "MAX_ARCHIVE_ENTRIES", 2_048)

    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        duplicate = archive.read("word/document.xml")
    duplicate_source = _rewrite_docx(source, additions={"WORD/DOCUMENT.XML": duplicate})
    _assert_code("duplicate_package_part", duplicate_source)


def test_snapshot_rejects_missing_or_encrypted_package_markers():
    incomplete = io.BytesIO()
    with zipfile.ZipFile(incomplete, "w") as archive:
        archive.writestr("word/document.xml", "<document/>")
    _assert_code("incomplete_docx_package", incomplete.getvalue())

    encrypted_marker = _rewrite_docx(
        _docx_bytes(), additions={"EncryptionInfo": b"marker"}
    )
    _assert_code("encrypted_document", encrypted_marker)


@pytest.mark.parametrize(
    ("replacement", "code"),
    [
        (b"<!DOCTYPE x [<!ENTITY y 'z'>]><x/>", "unsafe_xml"),
        (b"<w:document", "invalid_ooxml"),
    ],
)
def test_snapshot_rejects_unsafe_or_malformed_word_xml(replacement, code):
    source = _rewrite_docx(
        _docx_bytes(), replacements={"word/document.xml": replacement}
    )

    _assert_code(code, source)


def test_snapshot_rejects_nonstandard_and_macro_content_types():
    ordinary_xml = (
        b'<?xml version="1.0"?><Types '
        b'xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        b'<Default Extension="xml" ContentType="application/xml"/></Types>'
    )
    nonstandard = _rewrite_docx(
        _docx_bytes(), replacements={"[Content_Types].xml": ordinary_xml}
    )
    _assert_code("unsupported_word_package", nonstandard)

    macro_xml = (
        b'<?xml version="1.0"?><Types '
        b'xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        b'<Override PartName="/word/document.xml" '
        b'ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/>'
        b'<Default Extension="xml" ContentType="application/xml"/></Types>'
    )
    macro = _rewrite_docx(
        _docx_bytes(), replacements={"[Content_Types].xml": macro_xml}
    )
    _assert_code("unsupported_word_package", macro)


def test_snapshot_rejects_external_nonlinks_and_unsafe_hyperlinks():
    external_package = _with_external_relationship(
        _docx_bytes(),
        relation_type="https://schemas.example/relationships/image",
        target="https://attacker.example/payload",
    )
    _assert_code("unsafe_external_relationship", external_package)

    unsafe_link = _with_external_relationship(
        _docx_bytes(),
        relation_type=(
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
            "hyperlink"
        ),
        target="javascript:alert(1)",
    )
    _assert_code("unsafe_hyperlink", unsafe_link)

    mailto = _with_external_relationship(
        _docx_bytes("Email client"),
        relation_type=(
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
            "hyperlink"
        ),
        target="mailto:client@example.com",
    )
    assert "Email client" in inspect_cloud_docx_snapshot(mailto).review_text


def test_snapshot_rejects_active_xml_elements_and_oversized_review_text(monkeypatch):
    active_xml = (
        b'<?xml version="1.0"?><w:document '
        b'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b"<w:body><w:object/></w:body></w:document>"
    )
    active = _rewrite_docx(
        _docx_bytes(), replacements={"word/document.xml": active_xml}
    )
    _assert_code("active_or_embedded_content", active)

    monkeypatch.setattr(snapshot_module, "MAX_EXTRACTED_TEXT_CHARS", 10)
    _assert_code("document_text_too_large", _docx_bytes("X" * 20))


def test_snapshot_extracts_tabs_breaks_and_ignores_deleted_text():
    word_xml = (
        b'<?xml version="1.0"?><w:document '
        b'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b"<w:body><w:p><w:r><w:t>A</w:t><w:tab/><w:t>B</w:t><w:br/>"
        b"<w:delText>deleted</w:delText><w:t>C</w:t></w:r></w:p></w:body>"
        b"</w:document>"
    )
    source = _rewrite_docx(_docx_bytes(), replacements={"word/document.xml": word_xml})

    result = inspect_cloud_docx_snapshot(source)

    assert result.review_text == "A\tB\nC"
