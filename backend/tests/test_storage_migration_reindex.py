from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.services.cloud_sync import CloudSyncService
from app.services.storage_migration_reindex import StorageMigrationReindexService


class _Result:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row

    def scalar_one(self):
        return self.row


class _Db:
    def __init__(self, migration):
        self.migration = migration
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, *_args, **_kwargs):
        return _Result(self.migration)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _migration():
    return SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), phase="complete",
        needs_reindex=True, target_provider="google_drive",
        target_root={"id": "target-root"}, error_message=None,
    )


@pytest.mark.asyncio
async def test_reindex_populates_target_and_clears_flag():
    migration = _migration()
    db = _Db(migration)
    discovery = AsyncMock()
    discovery.discover.return_value = [
        {"id": "folder", "name": "matter", "is_folder": True},
        {"id": "file", "name": "brief.pdf", "is_folder": False, "path": "matter/brief.pdf"},
        {"id": "marker", "name": ".lawhand-matter.json", "is_folder": False},
    ]
    service = StorageMigrationReindexService(discovery)
    with patch("app.services.cloud_sync.CloudSyncService._upsert", new=AsyncMock()) as upsert:
        result = await service.run(db, str(migration.tenant_id))
    assert result["status"] == "completed"
    assert result["items"] == 1
    assert migration.needs_reindex is False
    upsert.assert_awaited_once()
    assert upsert.await_args.kwargs["provider"] == "google"
    assert upsert.await_args.kwargs["trusted_reindex"] is True


@pytest.mark.asyncio
async def test_force_refresh_runs_latest_completed_success_even_when_flag_is_clear():
    migration = _migration()
    migration.needs_reindex = False
    db = _Db(migration)
    discovery = AsyncMock()
    discovery.discover.return_value = [{"id": "file", "name": "fresh.pdf", "is_folder": False}]
    service = StorageMigrationReindexService(discovery)
    with patch("app.services.cloud_sync.CloudSyncService._upsert", new=AsyncMock()) as upsert:
        result = await service.run(db, str(migration.tenant_id), force=True)
    assert result["status"] == "completed"
    assert result["items"] == 1
    upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_traversal_keeps_reindex_pending_and_records_error():
    migration = _migration()
    db = _Db(migration)
    discovery = AsyncMock()
    discovery.discover.side_effect = RuntimeError("provider unavailable")
    with patch("app.services.storage_migration_reindex.set_tenant_context", new=AsyncMock()) as bind:
        result = await StorageMigrationReindexService(discovery).run(db, str(migration.tenant_id))
    assert result["status"] == "failed"
    assert migration.needs_reindex is True
    assert migration.error_message == "provider unavailable"
    assert db.rollbacks == 1
    assert bind.await_count >= 2


@pytest.mark.asyncio
async def test_retired_provider_write_is_rejected_but_target_write_allowed():
    migration = _migration()
    migration.needs_reindex = False
    service = CloudSyncService()
    service._latest_completed_migration = AsyncMock(return_value=migration)
    db = AsyncMock()
    with patch("app.services.cloud_sync.pg_insert") as insert:
        await service._upsert(db, str(migration.tenant_id), provider="microsoft", object_type="file", object_id="old")
        insert.assert_not_called()
        await service._upsert(db, str(migration.tenant_id), provider="google", object_type="file", object_id="new", trusted_reindex=True)
        insert.assert_called_once()

@pytest.mark.asyncio
async def test_sharepoint_reindex_retains_drive_identity_for_live_content_fetch(monkeypatch):
    import httpx
    from app.services.storage_migration_reindex import inventory_metadata
    from app.services.cloud_search import CloudHit, CloudSearchService, _INDEX_SOURCE_MAP
    metadata = inventory_metadata({'id':'file-id','drive_id':'drive-id','name':'Contract.txt','is_folder':False}, 'sharepoint')
    assert metadata['object_type'] == 'sharepoint_file'
    assert metadata['object_id'] == 'drive-id:file-id'
    hit = CloudHit(provider='microsoft', source=_INDEX_SOURCE_MAP[('microsoft', metadata['object_type'])], object_id=metadata['object_id'], title='Contract.txt', snippet='', url='', modified_time='', mime_type='text/plain')
    reader = CloudSearchService()
    reader._get_microsoft_token = AsyncMock(return_value='token')
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, text='Live document')
    real_client = httpx.AsyncClient
    monkeypatch.setattr('app.services.cloud_search.httpx.AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    assert await reader._fetch_onedrive_content(None, hit, str(uuid.uuid4()), 500, None) == 'Live document'
    assert calls == ['/v1.0/drives/drive-id/items/file-id/content']


@pytest.mark.asyncio
async def test_generic_sharepoint_scan_cannot_repopulate_migrated_index():
    migration = _migration()
    migration.target_provider = 'sharepoint'
    service = CloudSyncService()
    service._latest_completed_migration = AsyncMock(return_value=migration)
    with patch('app.services.cloud_sync.pg_insert') as insert:
        await service._upsert(AsyncMock(), str(migration.tenant_id), provider='microsoft', object_type='sharepoint_file', object_id='old:file')
        insert.assert_not_called()
