import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.rag as rag
import app.services.smb_search as facade


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


@pytest.mark.asyncio
async def test_fetch_smb_rag_content_handles_empty_deadline_retry_errors_and_bounds(
    monkeypatch,
):
    import app.services.smb as smb

    monkeypatch.setattr(rag, "SMB_RAG_MAX_CONTENT_CHARS", 5)

    class Service:
        def __init__(self):
            self.calls = 0

        async def request_content_fetch(self, *_args, **_kwargs):
            return "task", "agent"

        async def get_task_result(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return None
            return {"ok": True, "content": "123456789"}

    service = Service()
    monkeypatch.setattr(smb, "smb_service", service)
    assert await rag._fetch_smb_rag_content(
        redis=object(), results=[], tenant_id="t", user_id="u", conversation_id=None
    ) == ({}, ())

    content, reasons = await rag._fetch_smb_rag_content(
        redis=object(),
        results=[SimpleNamespace(id="1")],
        tenant_id="t",
        user_id="u",
        conversation_id=None,
        session_factory=_Session,
    )
    assert content == {"1": "12345"}
    assert reasons == ()

    class ErrorService(Service):
        async def get_task_result(self, *_args, **_kwargs):
            return {"error": "offline"}

    monkeypatch.setattr(smb, "smb_service", ErrorService())
    content, reasons = await rag._fetch_smb_rag_content(
        redis=object(),
        results=[SimpleNamespace(id="1")],
        tenant_id="t",
        user_id="u",
        conversation_id=None,
        session_factory=_Session,
    )
    assert content == {}
    assert reasons == ("smb_content_fetch_failed",)


@pytest.mark.asyncio
async def test_fetch_smb_rag_content_covers_queue_deadline_and_pending_fetch(
    monkeypatch,
):
    import app.services.smb as smb

    monkeypatch.setattr(rag, "SMB_RAG_FETCH_TIMEOUT_SECONDS", 0)
    service = SimpleNamespace(request_content_fetch=AsyncMock())
    monkeypatch.setattr(smb, "smb_service", service)
    content, reasons = await rag._fetch_smb_rag_content(
        redis=object(),
        results=[SimpleNamespace(id="1")],
        tenant_id="t",
        user_id="u",
        conversation_id=None,
        session_factory=_Session,
    )
    assert content == {}
    assert reasons == ("smb_content_fetch_timeout",)

    monkeypatch.setattr(rag, "SMB_RAG_FETCH_TIMEOUT_SECONDS", 0.01)

    class SlowService:
        async def request_content_fetch(self, *_args, **_kwargs):
            return "task", "agent"

        async def get_task_result(self, *_args, **_kwargs):
            await asyncio.sleep(1)

    monkeypatch.setattr(smb, "smb_service", SlowService())
    content, reasons = await rag._fetch_smb_rag_content(
        redis=object(),
        results=[SimpleNamespace(id="1")],
        tenant_id="t",
        user_id="u",
        conversation_id=None,
        session_factory=_Session,
    )
    assert content == {}
    assert reasons == ("smb_content_fetch_timeout",)


def test_build_smb_rag_context_includes_all_metadata_and_fallback_snippet():
    hit = SimpleNamespace(
        id="f1",
        filename="contract.docx",
        ext="docx",
        owner="Ada",
        size_bytes=2048,
        modified_time=datetime(2025, 1, 2, tzinfo=timezone.utc),
        path="\\\\server\\share\\contract.docx",
        snippet="snippet text",
    )
    result = rag._build_smb_rag_context([hit], {})
    assert "[S1] On-prem: contract.docx (docx)" in result
    assert "Owner: Ada" in result and "2,048 bytes" in result
    assert "Modified: 2025-01-02T00:00:00+00:00" in result
    assert "Path: \\\\server\\share\\contract.docx" in result
    assert "Content:\nsnippet text" in result


def test_firm_memory_context_and_chunks_share_safe_page_citation_ids():
    file_id = "00000000-0000-0000-0000-000000000010"
    matter_id = "00000000-0000-0000-0000-000000000003"
    hit = {
        "id": file_id,
        "filename": "motion.pdf",
        "path": r"\\FILESERVER\Cases\motion.pdf",
        "snippet": "The motion is granted in part.",
        "page_number": 9,
        "modified_time": "2026-08-30T12:00:00Z",
    }

    context = rag._build_firm_memory_context([hit])
    chunks = rag._firm_memory_chunks([hit], matter_id)

    assert "[source: firm-memory:" in context
    assert "Page: 9" in context
    assert chunks[0]["id"] in context
    assert chunks[0]["page_number"] == 9
    assert chunks[0]["url"] == f"/firm-memory?matter={matter_id}&file={file_id}"
    assert not chunks[0]["url"].startswith(("file:", "smb:"))


@pytest.mark.asyncio
async def test_connected_source_query_cloud_and_smb_paths(monkeypatch):
    import app.services.smb as smb

    class Db:
        async def execute(self, _query):
            return SimpleNamespace(scalar_one=lambda: 1)

    class Planner:
        async def plan(self, **_kwargs):
            return {
                "should_search": True,
                "sources": ["drive", "smb"],
                "keywords": ["term"],
            }

    file_id = "00000000-0000-0000-0000-000000000010"
    hit_payload = {
        "id": file_id,
        "filename": "x.txt",
        "ext": ".txt",
        "owner": None,
        "size_bytes": 0,
        "modified_time": None,
        "path": r"\\server\share\x.txt",
        "snippet": "matched passage",
        "page_number": 2,
        "score": 3.0,
        "share_id": "00000000-0000-0000-0000-000000000020",
    }
    hit = SimpleNamespace(model_dump=lambda **_: hit_payload)

    class Cloud:
        async def search(self, **_kwargs):
            return [SimpleNamespace(to_dict=lambda: {"source": "x"})]

        async def fetch_contents(self, **_kwargs):
            return [
                {
                    "hit": SimpleNamespace(to_dict=lambda: {"source": "x"}),
                    "content": "body",
                }
            ]

    class Smb:
        async def search_local_files(self, *_args, **_kwargs):
            return SimpleNamespace(
                hits=[hit],
                partial=True,
                errors=["agent_search_timeout"],
                correlation_id="corr-1",
                duration_ms=44,
            )

    async def providers(*_args):
        return ["drive"]

    async def context(*_args):
        return None

    monkeypatch.setattr(rag, "async_session_maker", lambda: DbSession(Db()))
    monkeypatch.setattr(rag, "set_tenant_context", context)
    monkeypatch.setattr(rag, "_connected_providers", providers)
    monkeypatch.setattr(rag.settings, "SMB_ENABLED", True)
    monkeypatch.setattr(rag.settings, "CLOUD_SEARCH_ENABLED", True)
    monkeypatch.setattr(rag, "build_cloud_context", AsyncMock(return_value="cloud"))
    monkeypatch.setattr(smb, "smb_service", Smb())
    result = await rag._connected_source_query(
        question="question",
        tenant_id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
        cloud_search_service=Cloud(),
        retrieval_planner=Planner(),
        tenant_name="Tenant",
        matter_context_str=None,
        matter_id="00000000-0000-0000-0000-000000000003",
        matter_cloud_folder=None,
        redis=object(),
        conversation_id="c",
        db=Db(),
    )
    assert result[0] == "cloud"
    assert "[F1]" in result[2]
    assert "untrusted evidence" in result[2]
    assert "[source: firm-memory:" in result[2]
    assert result.smb_hits == [hit_payload]
    assert result.degradation_reasons == ("agent_search_timeout",)


class DbSession:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_):
        return False


@pytest.mark.asyncio
async def test_smb_search_facade_delegates_enabled_operations(monkeypatch):
    service = SimpleNamespace(
        search_files=AsyncMock(return_value=["hit"]),
        request_content_fetch=AsyncMock(return_value=("task", "agent")),
        poll_content_result=AsyncMock(return_value="body"),
        build_smb_context=AsyncMock(return_value="context"),
        get_admin_stats=AsyncMock(return_value={"count": 1}),
    )
    monkeypatch.setattr(facade, "smb_service", service)
    monkeypatch.setattr(facade.settings, "SMB_ENABLED", True)
    assert await facade.search_smb_files("db", "tenant", "query") == ["hit"]
    assert await facade.request_content_fetch("db", "t", "u", "f") == ("task", "agent")
    assert (
        await facade.get_content_result(
            "task", redis_client="r", tenant_id="t", file_id="f"
        )
        == "body"
    )
    assert await facade.build_smb_context([]) == "context"
    assert await facade.get_smb_stats("db", "t") == {"count": 1}
