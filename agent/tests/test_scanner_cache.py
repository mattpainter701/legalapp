from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

pytest.importorskip("smbclient")

from clarity_agent import smb_scanner  # noqa: E402
from clarity_agent.smb_scanner import SmbScanner, _normalized_extensions  # noqa: E402


class _FileEntry:
    name = "unchanged.txt"
    st_size = 12
    st_mtime = 1_725_542_400
    st_ctime = 1_725_542_400

    def is_dir(self):
        return False

    def is_file(self):
        return True


class _Ledger:
    async def get_dir_mtime(self, path):
        return "2026-08-25T00:00:00+00:00"

    async def get_file(self, path):
        return {
            "path": path,
            "share_id": "share-1",
            "filename": "unchanged.txt",
            "ext": ".txt",
            "content_hash": "cached",
            "size_bytes": 12,
            "modified_time": datetime.fromtimestamp(
                _FileEntry.st_mtime, tz=timezone.utc
            ).isoformat(),
            "is_deleted": False,
        }

    async def upsert_file(self, file):
        pass


@pytest.mark.asyncio
async def test_unchanged_directory_still_yields_cached_files(monkeypatch):
    monkeypatch.setattr(
        smb_scanner.smbclient,
        "stat",
        lambda path, **kwargs: SimpleNamespace(st_mtime=1_725_542_400),
    )
    monkeypatch.setattr(
        smb_scanner.smbclient, "scandir", lambda path, **kwargs: [_FileEntry()]
    )

    scanner = SmbScanner(SimpleNamespace(), _Ledger())
    walker = scanner._walk_directory(
        None,
        r"\\FS\Legal",
        allowed_extensions={".txt"},
        share_id="share-1",
    )
    files = [file async for file in walker]

    assert [file["path"] for file in files] == [r"\\FS\Legal\unchanged.txt"]
    assert not walker.errors


class _ChangedLedger(_Ledger):
    async def get_file(self, path):
        cached = await super().get_file(path)
        cached["size_bytes"] = 1
        return cached


class _ChangedEntry(_FileEntry):
    st_size = 99
    st_mtime = _FileEntry.st_mtime + 60


@pytest.mark.asyncio
async def test_unchanged_directory_detects_changed_child_metadata(monkeypatch):
    monkeypatch.setattr(
        smb_scanner.smbclient,
        "stat",
        lambda path, **kwargs: SimpleNamespace(st_mtime=1_725_542_400),
    )
    monkeypatch.setattr(
        smb_scanner.smbclient,
        "scandir",
        lambda path, **kwargs: [_ChangedEntry()],
    )

    scanner = SmbScanner(SimpleNamespace(), _ChangedLedger())
    monkeypatch.setattr(
        scanner,
        "_compute_short_hash",
        lambda session, path, operation_kwargs=None: _async_value("new"),
    )
    monkeypatch.setattr(
        scanner,
        "_extract_snippet",
        lambda session, path, max_chars=500, operation_kwargs=None: _async_value(
            "updated"
        ),
    )
    walker = scanner._walk_directory(
        None,
        r"\\FS\Legal",
        allowed_extensions={".txt"},
        share_id="share-1",
    )
    files = [file async for file in walker]

    assert files[0]["content_hash"] == "new"
    assert files[0]["size_bytes"] == 99


async def _async_value(value):
    return value


def test_configured_extensions_are_normalized_and_empty_stays_empty():
    assert _normalized_extensions(["PDF", " .DoCx "]) == {".pdf", ".docx"}
    assert _normalized_extensions([]) == set()


@pytest.mark.asyncio
async def test_change_detection_uses_size_and_mtime_not_only_prefix_hash():
    class ChangeLedger:
        async def get_all_paths(self, share_id):
            return {r"\\FS\Legal\changed.txt"}

        async def get_files(self, paths):
            return {
                r"\\FS\Legal\changed.txt": {
                    "path": r"\\FS\Legal\changed.txt",
                    "content_hash": "same-prefix",
                    "size_bytes": 10,
                    "modified_time": "2026-08-27T00:00:00+00:00",
                    "is_deleted": False,
                }
            }

    scanner = SmbScanner(SimpleNamespace(), ChangeLedger())
    current = {
        "path": r"\\FS\Legal\changed.txt",
        "content_hash": "same-prefix",
        "size_bytes": 20,
        "modified_time": "2026-08-28T00:00:00+00:00",
    }

    changes = await scanner._detect_changes("share-1", [current])

    assert changes.changed_files == [current]
    assert changes.unchanged_files == []
