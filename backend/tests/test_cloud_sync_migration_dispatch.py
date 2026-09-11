from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.services.cloud_sync import CloudSyncService
from app.services.storage_migration_reindex import storage_migration_reindex


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,group",
    [
        ("google_drive", "google"),
        ("onedrive", "microsoft"),
        ("sharepoint", "microsoft"),
    ],
)
async def test_migrated_sync_only_inventories_bound_target_root(
    monkeypatch, provider, group
):
    service = CloudSyncService()
    monkeypatch.setattr("app.services.cloud_sync.set_tenant_context", AsyncMock())
    service._latest_completed_migration = AsyncMock(
        return_value=SimpleNamespace(target_provider=provider)
    )
    reindex = AsyncMock(return_value={"status": "completed", "items": 7})
    monkeypatch.setattr(storage_migration_reindex, "run", reindex)
    service.sync_google_drive = AsyncMock(
        side_effect=AssertionError("must use bounded inventory")
    )
    service.sync_onedrive = AsyncMock(
        side_effect=AssertionError("must use bounded inventory")
    )
    service.sync_sharepoint = AsyncMock(
        side_effect=AssertionError("must use bounded inventory")
    )
    service.sync_gmail_metadata = AsyncMock(return_value=3)
    service.sync_outlook_mail = AsyncMock(return_value=3)
    db = Mock()
    tenant_id = str(uuid4())
    result = await service.sync_all(db, tenant_id)
    assert result[group] == {"files": 7, "emails": 3}
    assert sum(sum(result[name].values()) for name in ("google", "microsoft")) == 10
    assert result["failures"] == []
    reindex.assert_awaited_once_with(db, tenant_id, force=True)
    (
        service.sync_gmail_metadata
        if group == "microsoft"
        else service.sync_outlook_mail
    ).assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_migrated_sync_reports_failure_to_scheduler(monkeypatch):
    monkeypatch.setattr("app.services.cloud_sync.set_tenant_context", AsyncMock())
    service = CloudSyncService()
    service._latest_completed_migration = AsyncMock(
        return_value=SimpleNamespace(target_provider="google_drive")
    )
    monkeypatch.setattr(
        storage_migration_reindex,
        "run",
        AsyncMock(return_value={"status": "failed", "items": 0}),
    )
    with pytest.raises(RuntimeError, match="reindex remains pending"):
        await service.sync_all(Mock(), str(uuid4()))


@pytest.mark.asyncio
async def test_retired_email_cannot_repopulate_after_cutover():
    service = CloudSyncService()
    service._latest_completed_migration = AsyncMock(
        return_value=SimpleNamespace(target_provider="google_drive")
    )
    db = Mock(execute=AsyncMock())
    await service._upsert(
        db,
        str(uuid4()),
        provider="microsoft",
        object_type="email",
        object_id="old-email",
    )
    assert db.execute.await_count == 1
    assert "FOR UPDATE" in str(db.execute.await_args.args[0])
