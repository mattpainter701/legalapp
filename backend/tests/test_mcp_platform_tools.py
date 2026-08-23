from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime, timezone
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

    matter_id = uuid.uuid4()

    assert await tools.execute_platform_tool(
        "list_matters", {"query": "ada"}, db=db, tenant_id=tenant_id
    ) == {"content": []}
    assert await tools.execute_platform_tool(
        "list_matter_documents",
        {"matter_id": str(matter_id)},
        db=db,
        tenant_id=tenant_id,
    ) == {"content": []}
    assert await tools.execute_platform_tool(
        "create_document",
        {"matter_id": str(matter_id), "title": "Notes", "content": "Body"},
        db=db,
        tenant_id=tenant_id,
    ) == {"content": []}

    # The dispatcher hands each tool a validated model, never the raw dict.
    list_matters.assert_awaited_once_with(
        tools.ListMattersArgs(query="ada"), db=db, tenant_id=tenant_id
    )
    list_documents.assert_awaited_once_with(
        tools.ListMatterDocumentsArgs(matter_id=matter_id), db=db, tenant_id=tenant_id
    )
    create_document.assert_awaited_once_with(
        tools.CreateDocumentArgs(
            matter_id=matter_id, title="Notes", content="Body"
        ),
        db=db,
        tenant_id=tenant_id,
        user_id=None,
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
        tools.ListMattersArgs(query="Ada", limit=50), db=db, tenant_id=tenant_id
    )

    assert response["content"][0]["json"] == {
        "matters": [
            {"id": str(matter.id), "name": "Ada v. Example", "status": "active"}
        ],
        "count": 1,
    }
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_list_matter_documents_checks_the_matter_and_serializes_documents() -> (
    None
):
    tenant_id = uuid.uuid4()
    matter_id = uuid.uuid4()
    document = SimpleNamespace(
        id=uuid.uuid4(),
        filename="notes.md",
        document_category="generated",
        file_size=42,
        task_id=None,
        created_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )
    matter_result = SimpleNamespace(scalar_one_or_none=lambda: object())
    documents_result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [document])
    )
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[matter_result, documents_result])
    )

    response = await tools._list_matter_documents(
        tools.ListMatterDocumentsArgs(matter_id=matter_id, limit=100),
        db=db,
        tenant_id=tenant_id,
    )

    assert response["content"][0]["json"] == {
        "documents": [
            {
                "id": str(document.id),
                "filename": "notes.md",
                "category": "generated",
                "file_size": 42,
                "task_id": None,
                "created_at": "2026-08-23T00:00:00+00:00",
            }
        ],
        "count": 1,
    }
    assert db.execute.await_count == 2

    with pytest.raises(HTTPException, match="matter_id"):
        tools.parse_platform_tool_args("list_matter_documents", {})


# ── Argument validation ─────────────────────────────────────────────────────
#
# These cover the gap the mocked tests above could not: with an AsyncMock
# database, an invalid UUID never reaches a real UUID column, so the 500 it
# used to raise was invisible. Validation now happens before any query runs.


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        # A model that skips list_matters and passes a matter name is the
        # realistic trigger, and used to surface as DBAPIError -> HTTP 500.
        ("list_matter_documents", {"matter_id": "Ada v. Example"}, "matter_id"),
        ("create_document", {"matter_id": "not-a-uuid"}, "matter_id"),
        # int("abc") used to raise ValueError -> HTTP 500.
        ("list_matters", {"limit": "abc"}, "limit"),
        ("list_matters", {"limit": 0}, "limit"),
        ("list_matters", {"limit": 51}, "limit"),
        ("list_matter_documents", {}, "matter_id"),
        # .strip() on a non-string used to raise AttributeError -> HTTP 500.
        (
            "create_document",
            {
                "matter_id": "11111111-1111-1111-1111-111111111111",
                "title": {"not": "a string"},
                "content": "body",
            },
            "title",
        ),
        (
            "create_document",
            {
                "matter_id": "11111111-1111-1111-1111-111111111111",
                "title": "Notes",
                "content": "",
            },
            "content",
        ),
        # An invented argument fails loudly rather than being dropped.
        (
            "create_document",
            {
                "matter_id": "11111111-1111-1111-1111-111111111111",
                "title": "Notes",
                "content": "body",
                "delete_everything": True,
            },
            "delete_everything",
        ),
    ],
)
def test_invalid_tool_arguments_are_rejected_as_client_errors(
    name: str, arguments: dict, expected: str
) -> None:
    with pytest.raises(HTTPException) as caught:
        tools.parse_platform_tool_args(name, arguments)

    # 400, not 500: the caller can correct this, and the dispatcher only
    # records usage for failures it can classify.
    assert caught.value.status_code == 400
    assert expected in caught.value.detail


def test_valid_tool_arguments_coerce_to_typed_models() -> None:
    matter_id = uuid.uuid4()
    task_id = uuid.uuid4()

    listing = tools.parse_platform_tool_args("list_matters", {})
    assert listing.limit == 25 and listing.query is None

    documents = tools.parse_platform_tool_args(
        "list_matter_documents", {"matter_id": str(matter_id)}
    )
    assert documents.matter_id == matter_id and documents.limit == 50

    creation = tools.parse_platform_tool_args(
        "create_document",
        {
            "matter_id": str(matter_id),
            "title": "Engagement letter",
            "content": "# Draft",
            "task_id": str(task_id),
        },
    )
    assert creation.matter_id == matter_id
    assert creation.task_id == task_id
    assert creation.document_category == "generated"


def test_unknown_tool_and_non_object_arguments_are_client_errors() -> None:
    with pytest.raises(HTTPException, match="Unknown platform tool"):
        tools.parse_platform_tool_args("delete_matter", {})

    with pytest.raises(HTTPException, match="must be a JSON object"):
        tools.parse_platform_tool_args("list_matters", ["not", "an", "object"])


def test_document_content_is_bounded_below_the_transport_cap() -> None:
    matter_id = uuid.uuid4()
    oversized = "x" * (tools.MAX_DOCUMENT_CONTENT_CHARS + 1)

    with pytest.raises(HTTPException) as caught:
        tools.parse_platform_tool_args(
            "create_document",
            {"matter_id": str(matter_id), "title": "Notes", "content": oversized},
        )
    assert caught.value.status_code == 400
    assert "content" in caught.value.detail


def test_declared_input_schema_matches_the_validated_contract() -> None:
    """The protocol path validates against inputSchema; keep the two in step."""
    schemas = {item["name"]: item["inputSchema"] for item in tools.platform_tool_definitions()}

    documents = schemas["list_matter_documents"]["properties"]
    assert documents["matter_id"]["format"] == "uuid"
    assert documents["limit"]["maximum"] == 100

    matters = schemas["list_matters"]["properties"]
    assert matters["limit"]["maximum"] == 50

    creation = schemas["create_document"]["properties"]
    assert creation["matter_id"]["format"] == "uuid"
    assert creation["task_id"]["format"] == "uuid"
    assert creation["content"]["maxLength"] == tools.MAX_DOCUMENT_CONTENT_CHARS


# ── Metering on failure ─────────────────────────────────────────────────────


def _metering_stubs(monkeypatch, raises: Exception):
    """Stub the router's metering seam and make the tool call fail."""
    from app.routers import mcp as mcp_router

    recorded: list[dict] = []

    async def _record(**kwargs):
        recorded.append(kwargs)

    async def _execute(*args, **kwargs):
        raise raises

    monkeypatch.setattr(mcp_router, "record_mcp_usage", _record)
    monkeypatch.setattr(mcp_router, "execute_platform_tool", _execute)
    return mcp_router, recorded


@pytest.mark.asyncio
async def test_an_unexpected_tool_failure_is_still_metered(monkeypatch) -> None:
    """Catching only HTTPException left a failed call absent from usage records."""
    boom = RuntimeError("database said no")
    mcp_router, recorded = _metering_stubs(monkeypatch, boom)

    body = mcp_router.ToolCallRequest(name="list_matters", arguments={"query": "ada"})
    request = SimpleNamespace(
        headers={"User-Agent": "probe/1.0"}, client=SimpleNamespace(host="203.0.113.7")
    )
    product_key = SimpleNamespace(id=uuid.uuid4())
    tenant = SimpleNamespace(id=uuid.uuid4())

    with pytest.raises(RuntimeError):
        await mcp_router._call_platform_tool_metered(
            body,
            request,
            db=object(),
            product_key=product_key,
            tenant=tenant,
            transport="rest",
            started=0.0,
        )

    assert len(recorded) == 1
    assert recorded[0]["status_code"] == 500
    assert recorded[0]["error_class"] == "RuntimeError"
    assert recorded[0]["tool_name"] == "list_matters"


@pytest.mark.asyncio
async def test_an_http_error_keeps_its_own_status_when_metered(monkeypatch) -> None:
    refusal = HTTPException(status_code=404, detail="Matter not found")
    mcp_router, recorded = _metering_stubs(monkeypatch, refusal)

    body = mcp_router.ToolCallRequest(name="create_document", arguments={})
    request = SimpleNamespace(
        headers={"User-Agent": "probe/1.0"}, client=SimpleNamespace(host="203.0.113.7")
    )

    with pytest.raises(HTTPException):
        await mcp_router._call_platform_tool_metered(
            body,
            request,
            db=object(),
            product_key=SimpleNamespace(id=uuid.uuid4()),
            tenant=SimpleNamespace(id=uuid.uuid4()),
            transport="rest",
            started=0.0,
        )

    assert recorded[0]["status_code"] == 404
    assert recorded[0]["error_class"] == "HTTPException"
