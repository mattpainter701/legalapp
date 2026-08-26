import asyncio
import time

import pytest

from clarity_agent import smb_reader
from clarity_agent.smb_reader import ContentResult, SmbReader


@pytest.mark.asyncio
async def test_reader_file_io_does_not_block_event_loop(monkeypatch):
    def slow_read(*_args):
        time.sleep(0.08)
        return b"hello", False

    async def fake_extract(*_args):
        return "hello"

    monkeypatch.setattr(smb_reader, "_read_content_sync", slow_read)
    monkeypatch.setattr(SmbReader, "extract_text", fake_extract)

    task = asyncio.create_task(SmbReader().read_content(None, "\\\\FS\\Legal\\a.txt"))
    await asyncio.sleep(0.01)

    assert not task.done()
    result = await task
    assert isinstance(result, ContentResult)
    assert result.content == "hello"
