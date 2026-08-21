"""Safe, exact-byte inspection for tenant-cloud DOCX working copies.

This gate is deliberately different from ``document_revision_engine``.  The
revision engine mutates a small, structurally simple DOCX subset, while this
module only snapshots a document the user already edited in their cloud
provider.  It therefore preserves ordinary hyperlinks, tracked revisions,
content controls, drawings, and other inert Word content byte-for-byte while
still rejecting executable/embedded content and hostile ZIP/XML packages.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import zipfile
from dataclasses import dataclass
from urllib.parse import urlparse
from xml.etree import ElementTree


MAX_DOCX_BYTES = 25 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 2_048
MAX_UNCOMPRESSED_BYTES = 125 * 1024 * 1024
MAX_SINGLE_PART_BYTES = 25 * 1024 * 1024
MAX_EXTRACTED_TEXT_CHARS = 2_000_000
DEFAULT_REVIEW_PREVIEW_CHARS = 50_000

_DOCX_MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document.main+xml"
)
_WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_OLE_COMPOUND_FILE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
_PREVIEW_MARKER = "\n\n[Preview truncated. Review the exact DOCX in tenant cloud.]"


class CloudDocxSnapshotError(ValueError):
    """A cloud working copy cannot safely become a review snapshot."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CloudDocxSnapshot:
    """Exact file evidence plus a bounded, explicitly non-canonical preview."""

    source_sha256: str
    source_size: int
    review_text: str
    preview_truncated: bool


def inspect_cloud_docx_snapshot(
    source: bytes,
    *,
    filename: str | None = None,
    max_preview_chars: int = DEFAULT_REVIEW_PREVIEW_CHARS,
) -> CloudDocxSnapshot:
    """Validate a DOCX for exact-byte adoption and extract review text.

    The returned text is only a convenience preview.  Approval remains bound
    to ``source_sha256`` and to provider read-back of the original bytes.
    """

    if type(source) is not bytes:
        raise CloudDocxSnapshotError(
            "invalid_source_type", "The cloud DOCX must be immutable bytes"
        )
    if not source:
        raise CloudDocxSnapshotError("empty_document", "The cloud DOCX is empty")
    if len(source) > MAX_DOCX_BYTES:
        raise CloudDocxSnapshotError(
            "document_too_large",
            f"The cloud DOCX exceeds the {MAX_DOCX_BYTES}-byte snapshot limit",
        )
    if filename is not None and not str(filename).casefold().endswith(".docx"):
        raise CloudDocxSnapshotError(
            "unsupported_extension", "Only standard .docx files can be adopted"
        )
    if max_preview_chars <= len(_PREVIEW_MARKER):
        raise CloudDocxSnapshotError(
            "invalid_preview_limit", "The DOCX preview limit is too small"
        )
    if source.startswith(_OLE_COMPOUND_FILE_MAGIC):
        raise CloudDocxSnapshotError(
            "encrypted_or_legacy_document",
            "Encrypted or legacy binary Word files cannot be adopted",
        )
    if not source.startswith(b"PK") or not zipfile.is_zipfile(io.BytesIO(source)):
        raise CloudDocxSnapshotError(
            "invalid_docx_package", "The cloud file is not a valid DOCX package"
        )

    try:
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            names = _validate_archive(archive)
            _validate_content_types(archive, names["[content_types].xml"])
            _validate_relationships(archive, names)
            _reject_active_word_content(archive, names)
            extracted = _extract_review_text(archive, names)
    except CloudDocxSnapshotError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise CloudDocxSnapshotError(
            "invalid_docx_package", "The cloud DOCX is damaged or unreadable"
        ) from exc

    if not extracted:
        extracted = (
            "[This DOCX has no extractable text. Review the exact file in tenant "
            "cloud.]"
        )
    truncated = len(extracted) > max_preview_chars
    if truncated:
        extracted = (
            extracted[: max_preview_chars - len(_PREVIEW_MARKER)].rstrip()
            + _PREVIEW_MARKER
        )
    return CloudDocxSnapshot(
        source_sha256=hashlib.sha256(source).hexdigest(),
        source_size=len(source),
        review_text=extracted,
        preview_truncated=truncated,
    )


def _validate_archive(archive: zipfile.ZipFile) -> dict[str, str]:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise CloudDocxSnapshotError(
            "package_too_complex", "The DOCX contains too many package parts"
        )
    total_uncompressed = 0
    names: dict[str, str] = {}
    for info in infos:
        normalized = _normalized_archive_name(info.filename)
        if info.is_dir():
            continue
        lowered = normalized.casefold()
        if lowered in names:
            raise CloudDocxSnapshotError(
                "duplicate_package_part",
                "The DOCX contains duplicate or case-conflicting parts",
            )
        names[lowered] = normalized
        if info.flag_bits & 0x1:
            raise CloudDocxSnapshotError(
                "encrypted_document", "Password-encrypted DOCX files cannot be adopted"
            )
        if info.file_size < 0 or info.file_size > MAX_SINGLE_PART_BYTES:
            raise CloudDocxSnapshotError(
                "package_part_too_large",
                f"DOCX part {normalized!r} exceeds the safe inspection limit",
            )
        total_uncompressed += info.file_size
        if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
            raise CloudDocxSnapshotError(
                "expanded_package_too_large",
                "The expanded DOCX exceeds the safe inspection limit",
            )

    required = {"[content_types].xml", "_rels/.rels", "word/document.xml"}
    if not required.issubset(names):
        raise CloudDocxSnapshotError(
            "incomplete_docx_package", "The DOCX is missing required package parts"
        )
    if {"encryptedpackage", "encryptioninfo"} & set(names):
        raise CloudDocxSnapshotError(
            "encrypted_document", "Password-encrypted DOCX files cannot be adopted"
        )
    dangerous_prefixes = (
        "word/vbaproject",
        "word/vbadata",
        "word/activex/",
        "word/embeddings/",
        "customui/",
    )
    for lowered in names:
        if lowered.startswith(dangerous_prefixes) or lowered.endswith(
            "/vbaproject.bin"
        ):
            raise CloudDocxSnapshotError(
                "active_or_embedded_content",
                "DOCX files with macros, ActiveX, or embedded packages cannot be adopted",
            )
    return names


def _normalized_archive_name(raw_name: str) -> str:
    name = str(raw_name or "")
    if "\\" in name or "\x00" in name:
        raise CloudDocxSnapshotError(
            "unsafe_package_path", "The DOCX contains an unsafe package path"
        )
    trimmed = name[:-1] if name.endswith("/") else name
    normalized = posixpath.normpath(trimmed)
    parts = trimmed.split("/")
    if (
        not trimmed
        or trimmed.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
        or normalized in {"", ".", ".."}
        or normalized.startswith("../")
        or ":" in normalized.split("/", 1)[0]
    ):
        raise CloudDocxSnapshotError(
            "unsafe_package_path", "The DOCX contains an unsafe package path"
        )
    return normalized


def _read_xml(archive: zipfile.ZipFile, part_name: str) -> ElementTree.Element:
    try:
        data = archive.read(part_name)
    except KeyError as exc:
        raise CloudDocxSnapshotError(
            "missing_ooxml_part", f"Required DOCX part {part_name!r} is missing"
        ) from exc
    lowered = data.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise CloudDocxSnapshotError(
            "unsafe_xml", f"DOCX part {part_name!r} contains unsafe XML declarations"
        )
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise CloudDocxSnapshotError(
            "invalid_ooxml", f"DOCX part {part_name!r} is malformed"
        ) from exc


def _validate_content_types(archive: zipfile.ZipFile, part_name: str) -> None:
    root = _read_xml(archive, part_name)
    content_types = {
        str(element.attrib.get("ContentType") or "").strip().casefold()
        for element in root.iter()
        if _local_name(element.tag) in {"Default", "Override"}
    }
    if _DOCX_MAIN_CONTENT_TYPE not in content_types:
        raise CloudDocxSnapshotError(
            "unsupported_word_package", "The package is not a standard DOCX file"
        )
    blocked = ("macroenabled", "vbaproject", "activex", "oleobject")
    if any(any(token in item for token in blocked) for item in content_types):
        raise CloudDocxSnapshotError(
            "active_or_embedded_content",
            "DOCX files with macros, ActiveX, or embedded packages cannot be adopted",
        )


def _validate_relationships(archive: zipfile.ZipFile, names: dict[str, str]) -> None:
    for lowered, part_name in names.items():
        if not lowered.endswith(".rels"):
            continue
        root = _read_xml(archive, part_name)
        for relation in root.iter():
            if _local_name(relation.tag) != "Relationship":
                continue
            relation_type = str(relation.attrib.get("Type") or "").casefold()
            target_mode = str(relation.attrib.get("TargetMode") or "").casefold()
            if (
                "vbaproject" in relation_type
                or "oleobject" in relation_type
                or relation_type.endswith("/package")
                or relation_type.endswith("/control")
                or relation_type.endswith("/attachedtemplate")
            ):
                raise CloudDocxSnapshotError(
                    "active_or_embedded_content",
                    "The DOCX contains an unsafe active or embedded relationship",
                )
            if target_mode == "external":
                if not relation_type.endswith("/hyperlink"):
                    raise CloudDocxSnapshotError(
                        "unsafe_external_relationship",
                        "Only ordinary web and email hyperlinks may be external",
                    )
                _validate_external_hyperlink(str(relation.attrib.get("Target") or ""))


def _validate_external_hyperlink(target: str) -> None:
    if len(target) > 2_048 or any(ord(char) < 0x20 for char in target):
        raise CloudDocxSnapshotError(
            "unsafe_hyperlink", "The DOCX contains an unsafe external hyperlink"
        )
    parsed = urlparse(target)
    scheme = parsed.scheme.casefold()
    if scheme in {"http", "https"} and parsed.netloc:
        return
    if scheme == "mailto" and parsed.path:
        return
    raise CloudDocxSnapshotError(
        "unsafe_hyperlink", "The DOCX contains an unsafe external hyperlink"
    )


def _reject_active_word_content(
    archive: zipfile.ZipFile, names: dict[str, str]
) -> None:
    for lowered, part_name in names.items():
        if not lowered.startswith("word/") or not lowered.endswith(".xml"):
            continue
        root = _read_xml(archive, part_name)
        for element in root.iter():
            if _local_name(element.tag).casefold() in {
                "oleobject",
                "object",
                "control",
            }:
                raise CloudDocxSnapshotError(
                    "active_or_embedded_content",
                    "The DOCX contains unsafe active or embedded content",
                )


def _extract_review_text(archive: zipfile.ZipFile, names: dict[str, str]) -> str:
    ordered_parts = [names["word/document.xml"]]
    ordered_parts.extend(
        names[name]
        for name in sorted(names)
        if (
            name.startswith("word/header")
            or name.startswith("word/footer")
            or name in {"word/footnotes.xml", "word/endnotes.xml"}
        )
        and name.endswith(".xml")
    )
    lines: list[str] = []
    total_chars = 0
    for part_name in dict.fromkeys(ordered_parts):
        root = _read_xml(archive, part_name)
        for element in root.iter():
            if element.tag != f"{{{_WORD_NAMESPACE}}}p":
                continue
            text = _text_for_paragraph(element).strip()
            if not text:
                continue
            total_chars += len(text) + 1
            if total_chars > MAX_EXTRACTED_TEXT_CHARS:
                raise CloudDocxSnapshotError(
                    "document_text_too_large",
                    "The DOCX contains too much text for safe review extraction",
                )
            lines.append(text)
    return "\n".join(lines).strip()


def _text_for_paragraph(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []

    def visit(element: ElementTree.Element) -> None:
        for child in element:
            if child is not paragraph and child.tag == f"{{{_WORD_NAMESPACE}}}p":
                continue
            local = _local_name(child.tag)
            if child.tag == f"{{{_WORD_NAMESPACE}}}t":
                parts.append(child.text or "")
            elif child.tag == f"{{{_WORD_NAMESPACE}}}tab":
                parts.append("\t")
            elif child.tag in {
                f"{{{_WORD_NAMESPACE}}}br",
                f"{{{_WORD_NAMESPACE}}}cr",
            }:
                parts.append("\n")
            elif local != "delText":
                visit(child)

    visit(paragraph)
    return "".join(parts)


def _local_name(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1].rsplit(":", 1)[-1]


__all__ = [
    "CloudDocxSnapshot",
    "CloudDocxSnapshotError",
    "DEFAULT_REVIEW_PREVIEW_CHARS",
    "MAX_DOCX_BYTES",
    "inspect_cloud_docx_snapshot",
]
