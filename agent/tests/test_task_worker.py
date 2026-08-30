"""Task routing: content fetch, connection test, and scan-now.

These run without a file server: ``smbclient`` is replaced with a stub so the
tests cover what the worker decides — which credential it picks, which share a
path belongs to, and what it reports back — rather than SMB itself.
"""

import sys
import types

import pytest

pytest.importorskip("httpx", reason="agent runtime dependencies not installed")


class FakeEntry:
    def __init__(self, name):
        self.name = name


class FakeSmbClient(types.ModuleType):
    """Stand-in for the ``smbclient`` module the worker imports at module load."""

    def __init__(self):
        super().__init__("smbclient")
        self.sessions = []
        self.scandir_calls = []
        self.scandir_result = [FakeEntry("a.pdf"), FakeEntry("b.docx")]
        self.scandir_error = None
        self.connect_error = None

    def reset_connection_cache(self, **kwargs):
        pass

    def register_session(self, server, **kwargs):
        if self.connect_error:
            raise self.connect_error
        self.sessions.append((server, kwargs))
        return f"session:{server}"

    def scandir(self, path, **kwargs):
        self.scandir_calls.append((path, kwargs))
        if self.scandir_error:
            raise self.scandir_error
        return list(self.scandir_result)


@pytest.fixture
def smb(monkeypatch):
    fake = FakeSmbClient()
    monkeypatch.setitem(sys.modules, "smbclient", fake)
    for name in list(sys.modules):
        if name.startswith("clarity_agent"):
            del sys.modules[name]
    return fake


class FakeClient:
    def __init__(self, tasks=None):
        self.tasks = tasks or []
        self.results = []

    async def get_tasks(self):
        return self.tasks

    async def submit_task_result(
        self, task_id, content="", truncated=False, error=None, ok=None, detail=None
    ):
        self.results.append(
            {
                "task_id": task_id,
                "content": content,
                "truncated": truncated,
                "error": error,
                "ok": (error is None) if ok is None else ok,
                "detail": detail,
            }
        )
        return {"status": "ok"}


class FakeConfig:
    smb_username = ""
    smb_password = ""
    smb_domain = ""


SHARE = {
    "share_id": "share-1",
    "share_path": "\\\\FS01\\Legal",
    "server": "FS01",
    "share": "Legal",
    "credential": {
        "credential_id": "cred-1",
        "name": "svc-lawhand",
        "auth_method": "ntlm",
        "domain": "CORP",
        "username": "svc-lawhand",
        "password": "pw",
    },
}


def _worker(
    smb, client, scan_callback=None, update_callback=None, local_search_index=None
):
    from clarity_agent.smb_reader import SmbReader
    from clarity_agent.task_worker import TaskWorker

    async def shares():
        return [SHARE]

    return TaskWorker(
        FakeConfig(),
        client,
        SmbReader(),
        share_provider=shares,
        scan_callback=scan_callback,
        update_callback=update_callback,
        local_search_index=local_search_index,
    )


class FakeSearchIndex:
    available = True

    def __init__(self, result=None):
        self.result = result or {
            "hits": [
                {
                    "share_id": "share-1",
                    "relative_path": "Cases\\brief.pdf",
                    "filename": "brief.pdf",
                    "ext": ".pdf",
                    "snippet": "matching text",
                    "page_number": 4,
                    "score": 2.5,
                    "path": "\\\\FS01\\Legal\\Cases\\brief.pdf",
                }
            ],
            "index_state": "ready",
            "indexed_files": 9,
            "pending_files": 2,
        }
        self.calls = []

    async def search(self, query, scopes, assigned_shares, extensions, limit):
        self.calls.append((query, scopes, assigned_shares, extensions, limit))
        return self.result


@pytest.mark.asyncio
async def test_local_search_returns_bounded_safe_hits_and_correlation(smb):
    client = FakeClient(
        [
            {
                "task_id": "search-1",
                "kind": "local_search",
                "query": "summary judgment",
                "scopes": [{"share_id": "share-1", "folder_path": "Cases"}],
                "file_extensions": ["pdf"],
                "limit": 10,
                "correlation_id": "run-abc",
            }
        ]
    )
    index = FakeSearchIndex()

    await _worker(smb, client, local_search_index=index).poll_and_execute()

    result = client.results[0]
    assert result["ok"] is True
    assert result["detail"]["schema_version"] == 1
    assert result["detail"]["correlation_id"] == "run-abc"
    assert result["detail"]["hits"][0]["relative_path"] == "Cases\\brief.pdf"
    assert "path" not in result["detail"]["hits"][0]
    assert index.calls[0][0] == "summary judgment"


@pytest.mark.asyncio
async def test_local_search_fails_closed_when_index_unavailable(smb):
    index = FakeSearchIndex()
    index.available = False
    client = FakeClient(
        [
            {
                "task_id": "search-2",
                "kind": "local_search",
                "query": "secret",
                "scopes": [{"share_id": "share-1"}],
                "correlation_id": "run-unavailable",
            }
        ]
    )

    await _worker(smb, client, local_search_index=index).poll_and_execute()

    assert client.results[0]["ok"] is False
    assert "unavailable" in client.results[0]["error"]
    assert client.results[0]["detail"]["hits"] == []


@pytest.mark.asyncio
async def test_local_search_rejects_unassigned_scope_without_calling_index(smb):
    index = FakeSearchIndex()
    client = FakeClient(
        [
            {
                "task_id": "search-3",
                "kind": "local_search",
                "query": "secret",
                "scopes": [{"share_id": "other"}],
                "correlation_id": "run-scope",
            }
        ]
    )

    await _worker(smb, client, local_search_index=index).poll_and_execute()

    assert client.results[0]["ok"] is False
    assert "not assigned" in client.results[0]["error"]
    assert index.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("query", "q" * 1001),
        ("limit", 51),
        ("correlation_id", "c" * 129),
        ("scopes", [{"share_id": "share-1", "folder_path": "..\\private"}]),
    ],
)
async def test_local_search_rejects_invalid_bounded_input_without_leaking_query(
    smb, caplog, field, value
):
    task = {
        "task_id": "search-4",
        "kind": "local_search",
        "query": "safe-query",
        "scopes": [{"share_id": "share-1"}],
        "correlation_id": "run-invalid",
    }
    task[field] = value
    client = FakeClient([task])

    await _worker(smb, client, local_search_index=FakeSearchIndex()).poll_and_execute()

    assert client.results[0]["ok"] is False
    assert "safe-query" not in caplog.text


@pytest.mark.asyncio
async def test_verify_share_reports_the_identity_it_connected_with(smb):
    client = FakeClient(
        [{"task_id": "t1", "kind": "verify_share", "share_id": "share-1"}]
    )

    await _worker(smb, client).poll_and_execute()

    result = client.results[0]
    assert result["ok"] is True
    assert result["detail"]["identity"] == "CORP\\svc-lawhand (ntlm)"
    assert result["detail"]["entries_sampled"] == 2
    # The share's own credential is what got used, not the local config.
    assert smb.sessions[0][1]["username"] == "CORP\\svc-lawhand"
    assert smb.scandir_calls[0][1]["username"] == "CORP\\svc-lawhand"
    assert smb.scandir_calls[0][1]["connection_cache"] is not None


@pytest.mark.asyncio
async def test_verify_share_reports_the_real_failure(smb):
    smb.connect_error = PermissionError("logon failure")
    client = FakeClient(
        [{"task_id": "t2", "kind": "verify_share", "share_id": "share-1"}]
    )

    await _worker(smb, client).poll_and_execute()

    result = client.results[0]
    assert result["ok"] is False
    assert "logon failure" in result["error"]


@pytest.mark.asyncio
async def test_verify_share_refuses_a_share_this_agent_does_not_have(smb):
    client = FakeClient(
        [{"task_id": "t3", "kind": "verify_share", "share_id": "other"}]
    )

    await _worker(smb, client).poll_and_execute()

    assert client.results[0]["ok"] is False
    assert "not assigned" in client.results[0]["error"]


@pytest.mark.asyncio
async def test_scan_now_runs_the_scan_callback(smb):
    scanned = []

    async def scan(share):
        scanned.append(share["share_id"])

    client = FakeClient([{"task_id": "t4", "kind": "scan_now", "share_id": "share-1"}])

    await _worker(smb, client, scan_callback=scan).poll_and_execute()

    assert scanned == ["share-1"]
    assert client.results[0]["ok"] is True


@pytest.mark.asyncio
async def test_scan_now_reports_a_failing_scan(smb):
    async def scan(share):
        raise RuntimeError("share went offline")

    client = FakeClient([{"task_id": "t5", "kind": "scan_now", "share_id": "share-1"}])

    await _worker(smb, client, scan_callback=scan).poll_and_execute()

    assert client.results[0]["ok"] is False
    assert "share went offline" in client.results[0]["error"]


@pytest.mark.asyncio
async def test_content_fetch_uses_the_credential_of_the_owning_share(smb, monkeypatch):
    client = FakeClient(
        [
            {
                "task_id": "t6",
                "kind": "content_fetch",
                "file_path": "\\\\FS01\\Legal\\Clients\\brief.txt",
            }
        ]
    )
    worker = _worker(smb, client)

    from clarity_agent.smb_reader import ContentResult

    async def fake_read(self, session, path, max_bytes=512000, connection_kwargs=None):
        return ContentResult(content="brief text")

    monkeypatch.setattr(type(worker.reader), "read_content", fake_read)

    await worker.poll_and_execute()

    assert client.results[0]["content"] == "brief text"
    assert smb.sessions[0][1]["username"] == "CORP\\svc-lawhand"


@pytest.mark.asyncio
async def test_content_fetch_without_a_path_is_reported_not_crashed(smb):
    client = FakeClient([{"task_id": "t7", "kind": "content_fetch"}])

    await _worker(smb, client).poll_and_execute()

    assert client.results[0]["ok"] is False
    assert "file_path" in client.results[0]["error"]


@pytest.mark.asyncio
async def test_unknown_task_kind_is_rejected(smb):
    client = FakeClient(
        [{"task_id": "t8", "kind": "delete_everything", "share_id": "share-1"}]
    )

    await _worker(smb, client).poll_and_execute()

    assert client.results[0]["ok"] is False
    assert "Unsupported task kind" in client.results[0]["error"]


@pytest.mark.asyncio
async def test_content_fetch_fails_closed_when_path_has_no_assigned_share(smb):
    client = FakeClient(
        [
            {
                "task_id": "t13",
                "kind": "content_fetch",
                "file_path": "\\\\FS01\\Other\\brief.txt",
            }
        ]
    )

    await _worker(smb, client).poll_and_execute()

    assert client.results[0]["ok"] is False
    assert "assigned share" in client.results[0]["error"]
    assert smb.sessions == []


@pytest.mark.asyncio
async def test_content_fetch_share_id_cannot_bypass_path_boundary(smb):
    client = FakeClient(
        [
            {
                "task_id": "t14",
                "kind": "content_fetch",
                "share_id": "share-1",
                "file_path": "\\\\FS01\\Legal-old\\brief.txt",
            }
        ]
    )

    await _worker(smb, client).poll_and_execute()

    assert client.results[0]["ok"] is False
    assert smb.sessions == []


@pytest.mark.asyncio
async def test_scan_now_reports_a_scan_that_failed_without_raising(smb):
    async def scan(share):
        # _scan_share records a failure and returns normally when the share is
        # unreachable or the sync is rejected.
        return {"status": "failed", "error": "Failed to connect to \\\\FS01\\Legal"}

    client = FakeClient([{"task_id": "t9", "kind": "scan_now", "share_id": "share-1"}])

    await _worker(smb, client, scan_callback=scan).poll_and_execute()

    assert client.results[0]["ok"] is False
    assert "Failed to connect" in client.results[0]["error"]


@pytest.mark.asyncio
async def test_scan_now_reports_a_partial_scan_as_not_ok(smb):
    async def scan(share):
        return {"status": "partial", "file_count": 12, "error": "Sync failed: 503"}

    client = FakeClient([{"task_id": "t10", "kind": "scan_now", "share_id": "share-1"}])

    await _worker(smb, client, scan_callback=scan).poll_and_execute()

    assert client.results[0]["ok"] is False
    assert client.results[0]["detail"]["file_count"] == 12


@pytest.mark.asyncio
async def test_scan_now_reports_success_with_the_file_count(smb):
    async def scan(share):
        return {"status": "success", "file_count": 4210}

    client = FakeClient([{"task_id": "t11", "kind": "scan_now", "share_id": "share-1"}])

    await _worker(smb, client, scan_callback=scan).poll_and_execute()

    assert client.results[0]["ok"] is True
    assert client.results[0]["detail"]["file_count"] == 4210


@pytest.mark.asyncio
async def test_a_share_added_moments_ago_is_found_after_one_refresh(smb):
    """A cached share list must not make a just-added share untestable."""
    refreshed = []

    async def empty_provider():
        return []

    async def refresher():
        refreshed.append(True)
        return [SHARE]

    from clarity_agent.smb_reader import SmbReader
    from clarity_agent.task_worker import TaskWorker

    client = FakeClient(
        [{"task_id": "t12", "kind": "verify_share", "share_id": "share-1"}]
    )
    worker = TaskWorker(
        FakeConfig(),
        client,
        SmbReader(),
        share_provider=empty_provider,
        share_refresher=refresher,
    )

    await worker.poll_and_execute()

    assert refreshed == [True]
    assert client.results[0]["ok"] is True


@pytest.mark.asyncio
async def test_agent_update_always_applies_version_only_task(smb):
    calls = []

    async def update(target_version, manifest_id):
        calls.append((target_version, manifest_id))
        return {"status": "started", "version": target_version}

    client = FakeClient(
        [
            {
                "task_id": "update-1",
                "kind": "agent_update",
                "target_version": "0.15.1",
                "manifest_id": "agent-v0.15.1",
            }
        ]
    )
    await _worker(smb, client, update_callback=update).poll_and_execute()

    assert calls == [("0.15.1", "agent-v0.15.1")]
    assert client.results[0]["ok"] is True


@pytest.mark.asyncio
async def test_agent_update_rejects_missing_target(smb):
    client = FakeClient([{"task_id": "update-2", "kind": "agent_update"}])

    await _worker(
        smb, client, update_callback=lambda _target, _manifest: None
    ).poll_and_execute()

    assert client.results[0]["ok"] is False
    assert "target_version" in client.results[0]["error"]


@pytest.mark.asyncio
async def test_agent_update_rejects_mismatched_manifest_identity(smb):
    client = FakeClient(
        [
            {
                "task_id": "update-3",
                "kind": "agent_update",
                "target_version": "0.15.1",
                "manifest_id": "agent-v9.9.9",
            }
        ]
    )

    await _worker(
        smb,
        client,
        update_callback=lambda _target, _manifest: None,
    ).poll_and_execute()

    assert client.results[0]["ok"] is False
    assert "manifest_id" in client.results[0]["error"]
