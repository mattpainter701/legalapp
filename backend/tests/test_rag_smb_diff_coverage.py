"""Focused coverage for the tenant-bound RAG/SMB integration branches."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.rag as rag
import app.services.smb_search as smb_search


class _TaskSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def _hit(file_id="file-1", **values):
    defaults = {
        "id": file_id,
        "filename": "contract.docx",
        "ext": "docx",
        "owner": "Alice",
        "size_bytes": 1234,
        "modified_time": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "path": r"\\server\share\contract.docx",
        "snippet": "indexed snippet",
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_fetch_smb_rag_handles_empty_and_expired_queue(monkeypatch):
    assert await rag._fetch_smb_rag_content(
        redis=object(),
        results=[],
        tenant_id="tenant",
        user_id="user",
        conversation_id=None,
    ) == ({}, ())

    monkeypatch.setattr(rag, "SMB_RAG_FETCH_TIMEOUT_SECONDS", 0)
    content, reasons = await rag._fetch_smb_rag_content(
        redis=object(),
        results=[SimpleNamespace(id="expired")],
        tenant_id="tenant",
        user_id="user",
        conversation_id=None,
        session_factory=_TaskSession,
    )
    assert content == {}
    assert reasons == ("smb_content_fetch_timeout",)


@pytest.mark.asyncio
async def test_fetch_smb_rag_retries_pending_result_and_limits_content(monkeypatch):
    import app.services.smb as smb_module

    class Service:
        def __init__(self):
            self.results = iter([None, {"ok": True, "content": "body"}])

        async def request_content_fetch(self, *_args, **_kwargs):
            return "task-1", "agent-1"

        async def get_task_result(self, *_args, **_kwargs):
            return next(self.results)

    service = Service()
    monkeypatch.setattr(smb_module, "smb_service", service)
    monkeypatch.setattr(rag, "SMB_RAG_MAX_CONTENT_CHARS", 3)
    monkeypatch.setattr(rag.asyncio, "sleep", AsyncMock())

    content, reasons = await rag._fetch_smb_rag_content(
        redis=object(),
        results=[SimpleNamespace(id="file-1")],
        tenant_id="tenant",
        user_id="user",
        conversation_id="conversation",
        session_factory=_TaskSession,
    )
    assert content == {"file-1": "bod"}
    assert reasons == ()


@pytest.mark.asyncio
async def test_fetch_smb_rag_records_relay_error_and_worker_exception(monkeypatch):
    import app.services.smb as smb_module

    class Service:
        async def request_content_fetch(
            self, _db, _tenant, _user, file_id, *_args, **_kwargs
        ):
            return f"task-{file_id}", "agent"

        async def get_task_result(self, task_id, *_args, **_kwargs):
            if task_id.endswith("error"):
                return {"ok": False}
            raise RuntimeError("relay unavailable")

    monkeypatch.setattr(smb_module, "smb_service", Service())
    content, reasons = await rag._fetch_smb_rag_content(
        redis=object(),
        results=[SimpleNamespace(id="error"), SimpleNamespace(id="exception")],
        tenant_id="tenant",
        user_id="user",
        conversation_id=None,
        session_factory=_TaskSession,
    )
    assert content == {}
    assert reasons == ("smb_content_fetch_failed",)


@pytest.mark.asyncio
async def test_fetch_smb_rag_cancels_slow_workers(monkeypatch):
    import app.services.smb as smb_module

    class SlowService:
        async def request_content_fetch(self, *_args, **_kwargs):
            return "task-1", "agent"

        async def get_task_result(self, *_args, **_kwargs):
            await asyncio.sleep(10)

    monkeypatch.setattr(smb_module, "smb_service", SlowService())
    monkeypatch.setattr(rag, "SMB_RAG_FETCH_TIMEOUT_SECONDS", 0.01)
    content, reasons = await rag._fetch_smb_rag_content(
        redis=object(),
        results=[SimpleNamespace(id="slow")],
        tenant_id="tenant",
        user_id="user",
        conversation_id=None,
        session_factory=_TaskSession,
    )
    assert content == {}
    assert reasons == ("smb_content_fetch_timeout",)


def test_build_smb_rag_context_uses_all_metadata_and_content_precedence():
    result = rag._build_smb_rag_context(
        [
            _hit(),
            _hit(
                "file-2",
                ext=None,
                owner=None,
                size_bytes=None,
                modified_time=None,
                path=None,
                snippet="fallback",
            ),
        ],
        {"file-1": "fetched"},
    )
    assert "[S1] On-prem: contract.docx (docx)" in result
    assert "Owner: Alice" in result and "1,234 bytes" in result
    assert "Modified: 2026-01-02T00:00:00+00:00" in result
    assert r"Path: \\server\share\contract.docx" in result
    assert "Content:\nfetched" in result
    assert "Content:\nfallback" in result


@pytest.mark.asyncio
async def test_connected_source_query_searches_cloud_and_smb(monkeypatch):
    class Db:
        async def execute(self, _statement):
            return SimpleNamespace(scalar_one=lambda: 1)

    class CloudHit:
        def to_dict(self):
            return {
                "provider": "drive",
                "source": "doc",
                "object_id": "1",
                "title": "Memo",
            }

    cloud = SimpleNamespace(
        search=AsyncMock(return_value=[CloudHit()]),
        fetch_contents=AsyncMock(
            return_value=[{"hit": CloudHit(), "content": "cloud body"}]
        ),
    )
    planner = SimpleNamespace(
        plan=AsyncMock(
            return_value={
                "should_search": True,
                "sources": ["google_drive", "smb"],
                "keywords": ["contract"],
                "max_hits": 20,
            }
        )
    )
    import app.services.smb as smb_module

    file_payload = {
        "id": "00000000-0000-0000-0000-000000000010",
        "filename": "contract.docx",
        "ext": ".docx",
        "owner": "Alice",
        "size_bytes": 1234,
        "modified_time": "2026-01-02T00:00:00Z",
        "path": r"\\server\share\contract.docx",
        "snippet": "smb body",
        "page_number": 4,
        "score": 2.0,
        "share_id": "00000000-0000-0000-0000-000000000020",
    }
    local_result = SimpleNamespace(
        hits=[SimpleNamespace(model_dump=lambda **_: file_payload)],
        partial=False,
        errors=[],
        correlation_id="corr-1",
        duration_ms=20,
    )
    smb = SimpleNamespace(search_local_files=AsyncMock(return_value=local_result))
    monkeypatch.setattr(smb_module, "smb_service", smb)
    monkeypatch.setattr(
        rag, "_connected_providers", AsyncMock(return_value=["google_drive"])
    )
    monkeypatch.setattr(rag, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(rag.settings, "SMB_ENABLED", True)
    monkeypatch.setattr(rag.settings, "CLOUD_SEARCH_ENABLED", True)
    result = await rag._connected_source_query(
        question="find contract",
        tenant_id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
        cloud_search_service=cloud,
        retrieval_planner=planner,
        tenant_name="Demo",
        matter_context_str=None,
        matter_id="00000000-0000-0000-0000-000000000003",
        matter_cloud_folder=None,
        redis=object(),
        db=Db(),
    )
    assert result[1][0]["object_id"] == "1"
    assert "cloud body" in result[0]
    assert "smb body" in result[2]
    assert result.smb_hits == [file_payload]
    assert result.degradation_reasons == ()
    cloud.search.assert_awaited_once()
    smb.search_local_files.assert_awaited_once()


@pytest.mark.asyncio
async def test_smb_search_facade_forwards_and_requires_bound_result_lookup(monkeypatch):
    service = SimpleNamespace(
        search_files=AsyncMock(return_value=[_hit()]),
        request_content_fetch=AsyncMock(return_value=("task", "agent")),
        poll_content_result=AsyncMock(return_value="content"),
        build_smb_context=AsyncMock(return_value="context"),
        get_admin_stats=AsyncMock(return_value={"files": 1}),
    )
    monkeypatch.setattr(smb_search, "smb_service", service)
    monkeypatch.setattr(smb_search.settings, "SMB_ENABLED", False)
    assert await smb_search.search_smb_files(object(), "tenant", "query") == []
    monkeypatch.setattr(smb_search.settings, "SMB_ENABLED", True)
    assert await smb_search.search_smb_files(object(), "tenant", "query", limit=4)
    assert await smb_search.request_content_fetch(
        object(), "tenant", "user", "file", redis_client="redis"
    ) == ("task", "agent")
    with pytest.raises(ValueError, match="tenant_id and file_id"):
        await smb_search.get_content_result("task", tenant_id="tenant")
    assert (
        await smb_search.get_content_result(
            "task", redis_client="redis", tenant_id="tenant", file_id="file"
        )
        == "content"
    )
    assert await smb_search.build_smb_context([_hit()]) == "context"
    assert await smb_search.get_smb_stats(object(), "tenant") == {"files": 1}
