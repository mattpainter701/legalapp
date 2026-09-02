from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfWriter

from search_node.config import Limits
from search_node.contracts import ExtractionMethod, TerminalStatus
from search_node.parser import ParseFailure, Parser


def parse(path: Path, limits: Limits | None = None):
    return Parser(limits or Limits()).parse(path)


def test_text_html_json_csv_and_xml_are_normalized(tmp_path: Path):
    samples = {
        "note.txt": (b"alpha\x00 beta", "Alpha"),
        "page.html": (
            b"<h1>Heading</h1><script>never executed</script><p>Body</p>",
            "Heading",
        ),
        "data.json": (b'{"matter":"Alpha","count":2}', "Alpha"),
        "table.csv": (b"name,value\nAlpha,2\n", "Alpha"),
        "tree.xml": (b"<root><name>Alpha</name><value>2</value></root>", "Alpha"),
    }
    for name, (payload, expected) in samples.items():
        path = tmp_path / name
        path.write_bytes(payload)
        sections, media_type, ocr_pages = parse(path)
        assert sections
        assert media_type
        assert ocr_pages == ()
        text = " ".join(item.text for item in sections).title()
        assert expected in text
        assert "Never Executed" not in text


def test_ooxml_extracts_document_xml_without_running_macros(tmp_path: Path):
    path = tmp_path / "agreement.docm"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="urn:test"><w:p><w:t>Settlement terms</w:t></w:p></w:document>',
        )
        archive.writestr("word/vbaProject.bin", b"not executable")
    sections, _, _ = parse(path)
    assert "Settlement terms" in " ".join(item.text for item in sections)
    assert all("vba" not in item.text.lower() for item in sections)


def test_eml_extracts_body_and_bounded_attachment(tmp_path: Path):
    path = tmp_path / "message.eml"
    path.write_bytes(
        b"From: a@example.test\r\nTo: b@example.test\r\nSubject: Notice\r\n"
        b"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=x\r\n\r\n"
        b"--x\r\nContent-Type: text/plain\r\n\r\nNative body\r\n"
        b"--x\r\nContent-Type: text/plain\r\nContent-Disposition: attachment; filename=note.txt\r\n\r\n"
        b"Attached text\r\n--x--\r\n"
    )
    sections, _, _ = parse(path)
    text = " ".join(item.text for item in sections)
    assert "Native body" in text
    assert "Attached text" in text
    attachment = next(item for item in sections if "Attached text" in item.text)
    assert attachment.method is ExtractionMethod.EMBEDDED


def test_encrypted_pdf_is_a_visible_terminal_state(tmp_path: Path):
    path = tmp_path / "encrypted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("secret")
    with path.open("wb") as output:
        writer.write(output)
    with pytest.raises(ParseFailure) as caught:
        parse(path)
    assert caught.value.status is TerminalStatus.ENCRYPTED
    assert caught.value.code == "encrypted-pdf"


def test_malformed_pdf_and_archive_are_corrupt(tmp_path: Path):
    for name, payload in (("bad.pdf", b"%PDF broken"), ("bad.zip", b"PK broken")):
        path = tmp_path / name
        path.write_bytes(payload)
        with pytest.raises(ParseFailure) as caught:
            parse(path)
        assert caught.value.status is TerminalStatus.CORRUPT


def test_safe_ci_archive_bomb_is_rejected_by_declared_size(tmp_path: Path):
    path = tmp_path / "bomb.zip"
    # This is only 1 MiB of repeated bytes on disk/in memory. It safely exceeds
    # the conservative compression-ratio gate without expanding a giant fixture.
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("repeat.txt", b"A" * (1024 * 1024))
    with pytest.raises(ParseFailure) as caught:
        parse(path, Limits(unpacked_bytes=2 * 1024 * 1024))
    assert caught.value.status is TerminalStatus.TOO_LARGE
    assert caught.value.code == "archive-compression-ratio"


def test_archive_traversal_and_depth_are_rejected(tmp_path: Path):
    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../escape.txt", "no")
    with pytest.raises(ParseFailure) as caught:
        parse(traversal)
    assert caught.value.code == "unsafe-archive-path"

    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as archive:
        archive.writestr("deep.txt", "bounded")
    outer = tmp_path / "nested.zip"
    with zipfile.ZipFile(outer, "w") as archive:
        archive.writestr("inner.zip", inner.getvalue())
    with pytest.raises(ParseFailure) as caught:
        parse(outer, Limits(archive_depth=1))
    assert caught.value.code == "max-archive-depth"


def test_doctype_is_disabled(tmp_path: Path):
    path = tmp_path / "entity.xml"
    path.write_text('<!DOCTYPE x [<!ENTITY y "boom">]><x>&y;</x>', encoding="utf-8")
    with pytest.raises(ParseFailure) as caught:
        parse(path)
    assert caught.value.code == "xml-doctype-disabled"


def test_phase_two_format_is_explicitly_unsupported_without_tika(tmp_path: Path):
    path = tmp_path / "legacy.msg"
    path.write_bytes(b"reviewed runtime required")
    with pytest.raises(ParseFailure) as caught:
        parse(path)
    assert caught.value.status is TerminalStatus.UNSUPPORTED
    assert caught.value.code == "tika-runtime-unavailable"
