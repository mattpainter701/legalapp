from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
from fastapi import HTTPException

from app.services import mcp_platform_tools as tools


def test_document_filename_helpers_reject_unsafe_paths() -> None:
    assert tools._safe_filename("  Settlement / Draft  ", "md") == "Settlement-Draft.md"
    assert tools._safe_filename("...", "md") == "document.md"
    assert tools._requested_filename(None, "Case notes") == "Case-notes.md"
    assert tools._requested_filename("notes.md", "ignored") == "notes.md"
    assert (
        tools._storage_category(" correspondence / client ") == "correspondence-client"
    )
    assert tools._storage_category("...") == "generated"

    for filename in ("../secret.md", "folder/file.md", "bad\nname.md", "x" * 256):
        with pytest.raises(HTTPException, match="single file name"):
            tools._requested_filename(filename, "ignored")


def test_platform_tool_manifest_and_unknown_tool_are_explicit() -> None:
    definitions = tools.platform_tool_definitions()
    assert [item["name"] for item in definitions] == tools.PLATFORM_TOOL_NAMES
    assert definitions[2]["inputSchema"]["required"] == [
        "matter_id",
        "title",
        "content",
    ]


@pytest.mark.asyncio
async def test_execute_platform_tool_dispatches_with_tenant_scope(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    db = object()
    list_matters = AsyncMock(return_value={"content": []})
    list_documents = AsyncMock(return_value={"content": []})
    create_document = AsyncMock(return_value={"content": []})
    monkeypatch.setattr(tools, "_list_matters", list_matters)
    monkeypatch.setattr(tools, "_list_matter_documents", list_documents)
    monkeypatch.setattr(tools, "_create_document", create_document)

    assert await tools.execute_platform_tool(
        "list_matters", {"query": "ada"}, db=db, tenant_id=tenant_id
    ) == {"content": []}
    assert await tools.execute_platform_tool(
        "list_matter_documents", {"matter_id": "matter"}, db=db, tenant_id=tenant_id
    ) == {"content": []}
    assert await tools.execute_platform_tool(
        "create_document", {"matter_id": "matter"}, db=db, tenant_id=tenant_id
    ) == {"content": []}
    list_matters.assert_awaited_once_with({"query": "ada"}, db=db, tenant_id=tenant_id)
    list_documents.assert_awaited_once_with(
        {"matter_id": "matter"}, db=db, tenant_id=tenant_id
    )
    create_document.assert_awaited_once_with(
        {"matter_id": "matter"}, db=db, tenant_id=tenant_id, user_id=None
    )

    with pytest.raises(HTTPException, match="Unknown platform tool"):
        await tools.execute_platform_tool(
            "delete_matter", {}, db=db, tenant_id=tenant_id
        )


@pytest.mark.asyncio
async def test_list_matters_bounds_limit_and_serializes_scoped_records() -> None:
    tenant_id = uuid.uuid4()
    matter = SimpleNamespace(
        id=uuid.uuid4(), matter_name="Ada v. Example", status="active"
    )
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [matter]))
    db = SimpleNamespace(execute=AsyncMock(return_value=result))

    response = await tools._list_matters(
        {"query": "Ada", "limit": 999}, db=db, tenant_id=tenant_id
    )

    assert response["content"][0]["json"] == {
        "matters": [
            {"id": str(matter.id), "name": "Ada v. Example", "status": "active"}
        ],
        "count": 1,
    }
    assert db.execute.await_count == 1
