from types import SimpleNamespace
from pathlib import Path

import httpx
import pytest

pytest.importorskip("smbclient")

from clarity_agent import __main__ as agent_main  # noqa: E402


class _Ledger:
    def __init__(self):
        self.upserts = []
        self.deleted = []

    async def upsert_files(self, files):
        self.upserts.extend(files)

    async def assign_source_identity(self, file):
        file["source_id"] = file.get("source_id") or f"source:{file['path']}"
        file["file_revision"] = file.get("file_revision") or "revision"
        return file

    async def mark_deleted_paths(self, paths):
        self.deleted.extend(paths)


class _Scanner:
    def __init__(self, result):
        self.result = result

    async def scan_share(self, share, file_extensions=None):
        return self.result


class _Client:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    async def sync(self, files, deletions, share_id):
        if self.error:
            raise self.error
        return self.response

    async def report_scan_status(self, *args, **kwargs):
        return {}


class _FailingIndex:
    async def enqueue_many(self, files, only_if_missing=False):
        raise OSError("disk full")

    async def delete(self, paths):
        raise AssertionError("delete must not be reached")


def _file(path):
    return {
        "path": path,
        "filename": path.rsplit("\\", 1)[-1],
        "ext": ".txt",
        "mime_type": "text/plain",
        "snippet": "text",
        "size_bytes": 4,
        "modified_time": "2026-08-25T00:00:00+00:00",
        "content_hash": path,
    }


@pytest.mark.asyncio
async def test_rejected_file_is_not_ledger_acknowledged(monkeypatch):
    monkeypatch.setattr(agent_main, "SYNC_BATCH_SIZE", 100)
    ledger = _Ledger()
    result = SimpleNamespace(
        new_files=[_file(r"\\FS\Legal\ok.txt"), _file(r"\\FS\Legal\bad.txt")],
        changed_files=[],
        deleted_files=[],
        unchanged_files=[],
        errors=[],
    )
    client = _Client(
        response={
            "synced": 1,
            "deleted": 0,
            "errors": [{"path": r"\\FS\Legal\bad.txt", "error": "cap"}],
        }
    )

    outcome = await agent_main._scan_share(
        {"share_id": "share-1", "server": "FS", "share": "Legal"},
        ledger,
        client,
        _Scanner(result),
    )

    assert [f["path"] for f in ledger.upserts] == [r"\\FS\Legal\ok.txt"]
    assert outcome["status"] == "partial"


@pytest.mark.asyncio
async def test_failed_deletion_sync_does_not_mark_local_ledger(monkeypatch):
    monkeypatch.setattr(agent_main, "SYNC_BATCH_SIZE", 100)
    ledger = _Ledger()
    result = SimpleNamespace(
        new_files=[],
        changed_files=[],
        deleted_files=[r"\\FS\Legal\gone.txt"],
        unchanged_files=[],
        errors=[],
    )

    outcome = await agent_main._scan_share(
        {"share_id": "share-1", "server": "FS", "share": "Legal"},
        ledger,
        _Client(error=RuntimeError("offline")),
        _Scanner(result),
    )

    assert ledger.deleted == []
    assert outcome["status"] == "failed"
    assert outcome["error"] == "Sync failed: RuntimeError"


@pytest.mark.asyncio
async def test_sync_validation_detail_is_reported_without_request_body(monkeypatch):
    monkeypatch.setattr(agent_main, "SYNC_BATCH_SIZE", 100)
    request = httpx.Request("POST", "https://getlawhand.com/api/v1/smb/agents/a/sync")
    response = httpx.Response(
        422, request=request, json={"detail": "modified_time: invalid datetime"}
    )
    ledger = _Ledger()
    result = SimpleNamespace(
        new_files=[_file(r"\\FS\Legal\bad.txt")],
        changed_files=[],
        deleted_files=[],
        unchanged_files=[],
        errors=[],
    )

    outcome = await agent_main._scan_share(
        {"share_id": "share-1", "server": "FS", "share": "Legal"},
        ledger,
        _Client(error=httpx.HTTPStatusError("bad", request=request, response=response)),
        _Scanner(result),
    )

    assert outcome["status"] == "failed"
    assert outcome["error"] == "Sync failed: HTTP 422: modified_time: invalid datetime"
    assert "bad.txt" not in outcome["error"]


@pytest.mark.asyncio
async def test_optional_index_failure_does_not_block_saas_sync(monkeypatch):
    monkeypatch.setattr(agent_main, "SYNC_BATCH_SIZE", 100)
    ledger = _Ledger()
    result = SimpleNamespace(
        new_files=[_file(r"\\FS\Legal\ok.txt")],
        changed_files=[],
        deleted_files=[],
        unchanged_files=[],
        errors=[],
    )

    outcome = await agent_main._scan_share(
        {"share_id": "share-1", "server": "FS", "share": "Legal"},
        ledger,
        _Client(response={"synced": 1, "deleted": 0, "errors": []}),
        _Scanner(result),
        _FailingIndex(),
    )

    assert [item["path"] for item in ledger.upserts] == [r"\\FS\Legal\ok.txt"]
    assert outcome["status"] == "partial"
    assert outcome["error"] == "local_index_update_failed:OSError"


@pytest.mark.asyncio
async def test_unchanged_legacy_row_backfills_source_identity(monkeypatch):
    monkeypatch.setattr(agent_main, "SYNC_BATCH_SIZE", 100)
    ledger = _Ledger()
    legacy = _file(r"\\FS\Legal\legacy.txt")
    result = SimpleNamespace(
        new_files=[],
        changed_files=[],
        deleted_files=[],
        unchanged_files=[legacy],
        errors=[],
    )
    client = _Client(response={"synced": 1, "deleted": 0, "errors": []})

    outcome = await agent_main._scan_share(
        {"share_id": "share-1", "server": "FS", "share": "Legal"},
        ledger,
        client,
        _Scanner(result),
    )

    assert ledger.upserts[0]["source_id"].startswith("source:")
    assert ledger.upserts[0]["file_revision"] == "revision"
    assert outcome["file_count"] == 1
    assert outcome["synced"] == 1


def test_safe_request_error_summarizes_pydantic_detail_without_input():
    request = httpx.Request("POST", "https://getlawhand.com/api/v1/smb/agents/a/sync")
    response = httpx.Response(
        422,
        request=request,
        json={
            "detail": [
                {
                    "loc": ["body", "files", 0, "modified_time"],
                    "msg": "Input should be a valid datetime",
                    "input": "\\\\home\\share\\secret.docx",
                },
                {
                    "loc": ["body", "files", 0, "owner"],
                    "msg": "value at https://evil.example?token=secret",
                    "input": "home\\test:password",
                },
            ]
        },
    )

    error = agent_main._safe_request_error(
        httpx.HTTPStatusError("bad", request=request, response=response)
    )

    assert error == "HTTP 422: modified_time: Input should be a valid datetime"
    assert "secret" not in error
    assert "home" not in error


def test_default_local_index_path_resolves_relative_ledger(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(
        ledger_path="state/ledger.db",
        local_index_path="",
        local_index_max_file_mb=25,
        local_index_workers=1,
    )

    path, max_bytes, workers = agent_main._local_index_settings(config)

    assert Path(path).is_absolute()
    assert Path(path).name == "search-index.db"
    assert max_bytes == 25 * 1024 * 1024
    assert workers == 1
