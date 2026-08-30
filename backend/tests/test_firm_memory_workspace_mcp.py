from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.automation_capabilities import (
    CapabilityContext,
    CapabilityError,
    capability_catalog,
    resolve_capability_spec,
)
from app.services.chat_tools import handlers
from app.services.smb import smb_service


def test_firm_memory_capability_is_strict_and_workspace_only() -> None:
    spec = resolve_capability_spec("search_firm_memory")
    matter_id = uuid.uuid4()

    args = spec.parse_arguments(
        {
            "matter_id": str(matter_id),
            "query": "  summary judgment standard  ",
            "file_extensions": ["PDF", ".pdf", " docx "],
            "limit": 12,
        }
    )

    assert str(args.matter_id) == str(matter_id)
    assert args.query == "summary judgment standard"
    assert args.file_extensions == [".pdf", ".docx"]
    assert spec.audiences == ("workspace_mcp",)
    assert spec.required_scopes == ("matters:read", "documents:read")
    assert "search_firm_memory" in {
        item["name"] for item in capability_catalog(audience="workspace_mcp")
    }
    assert "search_firm_memory" not in {
        item["name"] for item in capability_catalog(audience="matter_chat")
    }

    with pytest.raises(CapabilityError, match="invalid query"):
        spec.parse_arguments({"matter_id": str(matter_id), "query": "   "})
    with pytest.raises(CapabilityError, match="invalid extra"):
        spec.parse_arguments(
            {"matter_id": str(matter_id), "query": "test", "extra": True}
        )


@pytest.mark.asyncio
async def test_firm_memory_handler_returns_safe_links_and_status(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    matter_id = uuid.uuid4()
    file_id = uuid.uuid4()
    redis = object()
    search = AsyncMock(
        return_value=SimpleNamespace(
            model_dump=lambda **_: {
                "correlation_id": "corr-1",
                "hits": [
                    {
                        "id": str(file_id),
                        "path": r"\\FILESERVER\Cases\Ada\motion.pdf",
                        "filename": "motion.pdf",
                        "ext": ".pdf",
                        "snippet": "The court applies the familiar standard.",
                        "page_number": 7,
                        "score": 8.25,
                        "share_id": str(uuid.uuid4()),
                        "owner": "FIRM\\lawyer",
                        "size_bytes": 4096,
                        "modified_time": "2026-08-30T12:00:00Z",
                    }
                ],
                "duration_ms": 83,
                "agent_statuses": [
                    {
                        "agent_id": str(uuid.uuid4()),
                        "status": "success",
                        "index_state": "ready",
                        "indexed_files": 125,
                        "pending_files": 2,
                    }
                ],
                "partial": False,
                "degraded": False,
                "errors": [],
            }
        )
    )
    monkeypatch.setattr(smb_service, "search_local_files", search)
    context = CapabilityContext(
        db=object(),
        user=SimpleNamespace(id=user_id, tenant_id=tenant_id),
        channel="workspace_mcp",
        granted_scopes=frozenset({"matters:read", "documents:read"}),
        redis=redis,
    )
    args = resolve_capability_spec("search_firm_memory").parse_arguments(
        {
            "matter_id": str(matter_id),
            "query": "summary judgment",
            "file_extensions": ["pdf"],
        }
    )

    result = await handlers.search_firm_memory(context, args)

    search.assert_awaited_once_with(
        context.db,
        str(tenant_id),
        str(user_id),
        str(matter_id),
        "summary judgment",
        [".pdf"],
        20,
        None,
        redis=redis,
    )
    assert "query" not in result
    assert result["result_count"] == 1
    assert result["hits"][0]["unc_path"].startswith(r"\\FILESERVER")
    assert result["hits"][0]["lawhand_url"] == (
        f"/firm-memory?matter={matter_id}&file={file_id}"
    )
    assert not result["hits"][0]["lawhand_url"].startswith(("file:", "smb:"))


@pytest.mark.asyncio
async def test_firm_memory_handler_maps_relay_outage(monkeypatch) -> None:
    monkeypatch.setattr(
        smb_service,
        "search_local_files",
        AsyncMock(side_effect=RuntimeError("SMB relay is temporarily unavailable")),
    )
    matter_id = uuid.uuid4()
    context = CapabilityContext(
        db=object(),
        user=SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4()),
        channel="workspace_mcp",
        redis=None,
    )
    args = resolve_capability_spec("search_firm_memory").parse_arguments(
        {"matter_id": str(matter_id), "query": "limitations"}
    )

    with pytest.raises(CapabilityError) as caught:
        await handlers.search_firm_memory(context, args)

    assert caught.value.code == "firm_memory_unavailable"
    assert "temporarily unavailable" in caught.value.message
