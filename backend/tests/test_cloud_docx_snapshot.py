from __future__ import annotations

import hashlib
import io
import zipfile
from xml.etree import ElementTree

import pytest
from docx import Document

from app.services.cloud_docx_snapshot import (
    CloudDocxSnapshotError,
    inspect_cloud_docx_snapshot,
)


_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _docx_bytes(text: str = "A reviewable legal draft") -> bytes:
    document = Document()
    document.add_paragraph(text)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _rewrite_docx(
    source: bytes,
    *,
    replacements: dict[str, bytes] | None = None,
    additions: dict[str, bytes] | None = None,
) -> bytes:
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as current,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as rewritten,
    ):
        for info in current.infolist():
            data = (replacements or {}).get(info.filename, current.read(info.filename))
            rewritten.writestr(info, data)
        for name, data in (additions or {}).items():
            rewritten.writestr(name, data)
    return output.getvalue()


def _with_external_relationship(
    source: bytes, *, relation_type: str, target: str
) -> bytes:
    relation_path = "word/_rels/document.xml.rels"
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        root = ElementTree.fromstring(archive.read(relation_path))
    ElementTree.SubElement(
        root,
        f"{{{_REL_NS}}}Relationship",
        {
            "Id": "rIdLawHandTest",
            "Type": relation_type,
            "Target": target,
            "TargetMode": "External",
        },
    )
    return _rewrite_docx(
        source,
        replacements={
            relation_path: ElementTree.tostring(
                root, encoding="utf-8", xml_declaration=True
            )
        },
    )


def test_snapshot_preserves_exact_bytes_and_extracts_review_text() -> None:
    source = _docx_bytes("Crucial case-specific language")

    snapshot = inspect_cloud_docx_snapshot(source, filename="draft.docx")

    assert snapshot.source_sha256 == hashlib.sha256(source).hexdigest()
    assert snapshot.source_size == len(source)
    assert snapshot.review_text == "Crucial case-specific language"
    assert snapshot.preview_truncated is False


def test_snapshot_permits_ordinary_https_hyperlinks() -> None:
    source = _with_external_relationship(
        _docx_bytes(),
        relation_type=(
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
            "hyperlink"
        ),
        target="https://www.courtlistener.com/opinion/123/example/",
    )

    snapshot = inspect_cloud_docx_snapshot(source)

    assert "reviewable legal draft" in snapshot.review_text


@pytest.mark.parametrize(
    "part_name",
    ["word/vbaProject.bin", "word/activeX/activeX1.bin", "word/embeddings/item.bin"],
)
def test_snapshot_rejects_active_or_embedded_payloads(part_name: str) -> None:
    source = _rewrite_docx(_docx_bytes(), additions={part_name: b"payload"})

    with pytest.raises(CloudDocxSnapshotError) as exc_info:
        inspect_cloud_docx_snapshot(source)

    assert exc_info.value.code == "active_or_embedded_content"


def test_snapshot_rejects_external_templates_but_not_hyperlinks() -> None:
    source = _with_external_relationship(
        _docx_bytes(),
        relation_type=(
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
            "attachedTemplate"
        ),
        target="https://attacker.example/template.dotm",
    )

    with pytest.raises(CloudDocxSnapshotError) as exc_info:
        inspect_cloud_docx_snapshot(source)

    assert exc_info.value.code == "active_or_embedded_content"


def test_snapshot_rejects_unsafe_archive_paths() -> None:
    source = _rewrite_docx(_docx_bytes(), additions={"../outside.bin": b"payload"})

    with pytest.raises(CloudDocxSnapshotError) as exc_info:
        inspect_cloud_docx_snapshot(source)

    assert exc_info.value.code == "unsafe_package_path"


def test_snapshot_marks_a_bounded_preview_as_truncated() -> None:
    source = _docx_bytes("X" * 1_000)

    snapshot = inspect_cloud_docx_snapshot(source, max_preview_chars=200)

    assert len(snapshot.review_text) <= 200
    assert snapshot.preview_truncated is True
    assert "Preview truncated" in snapshot.review_text
