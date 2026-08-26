import io
import sys
import types

import pytest


class _File:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return io.BytesIO(self.data)

    def __exit__(self, *args):
        return False


@pytest.fixture
def reader(monkeypatch):
    fake = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "smbclient", fake)
    from clarity_agent.smb_reader import SmbReader

    return SmbReader(), fake


@pytest.mark.asyncio
async def test_pdf_reads_complete_source_then_caps_returned_text(reader, monkeypatch):
    smb_reader, smb = reader
    source = b"complete-pdf" + b"x" * 100
    smb.open_file = lambda path, mode="rb": _File(source)

    async def extract(path, content):
        assert content == source
        return "A" * 50

    monkeypatch.setattr(smb_reader, "extract_text", extract)
    result = await smb_reader.read_content(None, r"\\FS\Legal\a.pdf", max_bytes=10)

    assert result.error is None
    assert result.content == "A" * 10
    assert result.truncated is True


@pytest.mark.asyncio
async def test_structured_file_over_source_cap_is_explicit_error(reader):
    smb_reader, smb = reader
    smb.open_file = lambda path, mode="rb": _File(b"0123456789")

    result = await smb_reader.read_content(
        None, r"\\FS\Legal\a.pdf", max_source_bytes=5
    )

    assert result.content == ""
    assert "structured extraction limit" in result.error


@pytest.mark.asyncio
async def test_image_only_pdf_is_not_reported_ready_empty(reader, monkeypatch):
    smb_reader, smb = reader
    smb.open_file = lambda path, mode="rb": _File(b"pdf")

    async def extract(path, content):
        return ""

    monkeypatch.setattr(smb_reader, "extract_text", extract)
    result = await smb_reader.read_content(None, r"\\FS\Legal\scan.pdf")

    assert result.content == ""
    assert result.error == "No extractable text found in file"


@pytest.mark.asyncio
async def test_text_file_preserves_safe_streaming_truncation(reader, monkeypatch):
    smb_reader, smb = reader
    smb.open_file = lambda path, mode="rb": _File(b"abcdef")

    result = await smb_reader.read_content(None, r"\\FS\Legal\notes.txt", max_bytes=3)

    assert result.error is None
    assert result.content == "abc"
    assert result.truncated is True
