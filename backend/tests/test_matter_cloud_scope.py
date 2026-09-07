"""Focused authorization and identity coverage for matter cloud retrieval."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services import cloud_sync
from app.services.cloud_sync import CloudSyncService
from app.services.matter_cloud_scope import (
    MAX_MATTER_DOCUMENT_CLOUD_REFS,
    load_matter_document_cloud_scope,
)


TENANT_ID = "11111111-1111-1111-1111-111111111111"
MATTER_ID = "22222222-2222-2222-2222-222222222222"


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_document_scope_keeps_exact_tenant_matter_references_and_drive_identity():
    statements = []

    class _Db:
        async def execute(self, statement):
            statements.append(statement)
            return _RowsResult(
                [
                    ("google", "google_drive", "google-file", "google-folder", None),
                    (
                        "microsoft",
                        "sharepoint",
                        "shared-item",
                        "shared-parent",
                        "drive-a",
                    ),
                    (
                        "microsoft",
                        "sharepoint",
                        "shared-item",
                        "shared-parent",
                        "drive-b",
                    ),
                    # Invalid/missing durable links must not broaden the scope.
                    ("microsoft", "sharepoint", "no-drive", "parent", None),
                    ("local", "local", "local-file", "local-parent", None),
                    ("microsoft", "onedrive", None, "parent", None),
                ]
            )

    scope = await load_matter_document_cloud_scope(
        _Db(), tenant_id=TENANT_ID, matter_id=MATTER_ID
    )

    assert scope.folder_ids == {"google_drive": ["google-folder"]}
    assert scope.object_ids == {
        ("google", "file"): ["google-file"],
        ("microsoft", "sharepoint_file"): [
            "drive-a:shared-item",
            "drive-b:shared-item",
        ],
    }
    assert scope.sharepoint_folder_refs == [
        ("drive-a", "shared-parent"),
        ("drive-b", "shared-parent"),
    ]

    statement = statements[0]
    params = {str(value) for value in statement.compile().params.values()}
    assert TENANT_ID in params
    assert MATTER_ID in params
    assert "matter_documents.tenant_id" in str(statement)
    assert "matter_documents.matter_id" in str(statement)
    assert statement._limit_clause.value == MAX_MATTER_DOCUMENT_CLOUD_REFS


@pytest.mark.asyncio
async def test_document_scope_rejects_invalid_matter_identity_without_querying():
    db = AsyncMock()

    scope = await load_matter_document_cloud_scope(
        db, tenant_id=TENANT_ID, matter_id="not-a-uuid"
    )

    assert scope.object_ids == {}
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_sharepoint_graph_sync_qualifies_item_and_parent_ids(monkeypatch):
    captured = []

    class _Db:
        commit = AsyncMock()
        rollback = AsyncMock()

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "value": [
                    {
                        "id": "item-1",
                        "name": "validation.txt",
                        "file": {"mimeType": "text/plain"},
                        "size": 420,
                        "lastModifiedDateTime": "2026-09-07T12:00:00Z",
                        "createdDateTime": "2026-09-07T11:00:00Z",
                        "webUrl": "https://example.sharepoint.com/validation.txt",
                        "parentReference": {
                            "id": "validation-folder",
                            "path": "/drive/root:/Matter/Validation",
                        },
                        "createdBy": {"user": {"email": "owner@example.com"}},
                    }
                ]
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, url, *, headers, params):
            assert url.endswith("/children")
            assert headers == {"Authorization": "Bearer token"}
            assert params and "$select" in params
            return _Response()

    async def capture_upsert(_db, _tenant_id, **metadata):
        captured.append(metadata)

    service = CloudSyncService()
    monkeypatch.setattr(cloud_sync.httpx, "AsyncClient", lambda: _Client())
    monkeypatch.setattr(service, "_upsert", capture_upsert)

    db = _Db()
    count = await service._sync_graph_files(
        db,
        TENANT_ID,
        "token",
        "https://graph.microsoft.com/v1.0/drives/drive-a/items/root/children",
        "sharepoint_file",
        drive_name="Documents",
        drive_id="drive-a",
    )

    assert count == 1
    assert len(captured) == 1
    metadata = captured[0]
    assert {
        key: metadata[key]
        for key in (
            "provider",
            "object_type",
            "object_id",
            "parent_id",
            "title",
            "mime_type",
            "size_bytes",
            "web_url",
        )
    } == {
        "provider": "microsoft",
        "object_type": "sharepoint_file",
        "object_id": "drive-a:item-1",
        "parent_id": "drive-a:validation-folder",
        "title": "validation.txt",
        "mime_type": "text/plain",
        "size_bytes": 420,
        "web_url": "https://example.sharepoint.com/validation.txt",
    }
    assert metadata["modified_time"].isoformat() == "2026-09-07T12:00:00+00:00"
    assert metadata["created_time"].isoformat() == "2026-09-07T11:00:00+00:00"
    db.commit.assert_awaited_once()
