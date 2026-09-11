"""A failed cloud provider sync must be reported as failed, never as zero items.

Before this, each provider sync caught its own error, rolled back, logged a
warning and returned 0. The scheduler then recorded a completed sync with zero
items, so a tenant's connected-drive metadata could go stale for days with no
failed run and no integration error to alarm on. Production logged 16 such
OneDrive failures in 11 hours on 2026-09-11.
"""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.cloud_sync import CloudSyncService

TENANT = "9ff4a695-826c-422c-bb7f-6037495a2c4e"
ONEDRIVE_FAILURE = {
    "group": "microsoft",
    "provider": "OneDrive",
    "error": "RuntimeError",
}


@pytest.mark.asyncio
async def test_sync_all_reports_a_failed_provider_instead_of_zero_items(monkeypatch):
    service = CloudSyncService()
    monkeypatch.setattr(
        service, "_latest_completed_migration", AsyncMock(return_value=None)
    )
    monkeypatch.setattr("app.services.cloud_sync.set_tenant_context", AsyncMock())
    rollbacks = []

    class DummyDb:
        async def rollback(self):
            rollbacks.append(True)

    def provider(count):
        async def _provider(_db, _tenant_id):
            return count

        return _provider

    async def failing_onedrive(_db, _tenant_id):
        raise RuntimeError("upsert failed for 'Q3 settlement draft.docx'")

    monkeypatch.setattr(service, "sync_google_drive", provider(2))
    monkeypatch.setattr(service, "sync_gmail_metadata", provider(3))
    monkeypatch.setattr(service, "sync_onedrive", failing_onedrive)
    monkeypatch.setattr(service, "sync_sharepoint", provider(7))
    monkeypatch.setattr(service, "sync_outlook_mail", provider(11))

    result = await service.sync_all(DummyDb(), TENANT)

    # The other providers still ran and kept their counts.
    assert result["google"] == {"files": 2, "emails": 3}
    assert result["microsoft"] == {"files": 7, "emails": 11}
    assert result["failures"] == [ONEDRIVE_FAILURE]
    assert rollbacks == [True]
    # Only the exception type is surfaced; file names never leave the service.
    assert "Q3 settlement" not in repr(result)


@pytest.mark.asyncio
async def test_graph_file_sync_error_propagates_after_rollback(monkeypatch):
    service = CloudSyncService()
    db = SimpleNamespace(rollback=AsyncMock(), commit=AsyncMock())

    class _GraphClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def get(self, _url, **_kwargs):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "value": [
                        {
                            "id": "graph-item-1",
                            "name": "Engagement Letter.pdf",
                            "file": {"mimeType": "application/pdf"},
                            "parentReference": {"id": "parent-1", "path": ""},
                        }
                    ]
                },
            )

    monkeypatch.setattr(
        "app.services.cloud_sync.httpx.AsyncClient", lambda *_a, **_k: _GraphClient()
    )
    monkeypatch.setattr(
        service, "_upsert", AsyncMock(side_effect=RuntimeError("write refused"))
    )

    with pytest.raises(RuntimeError):
        await service._sync_graph_files(
            db,
            TENANT,
            "graph-token",
            "https://graph.microsoft.com/v1.0/me/drive/root/children",
            "file",
        )

    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_cloud_sync_total_skips_failures_and_returns_them(monkeypatch):
    from app.routers import cloud_admin

    service = SimpleNamespace(
        sync_all=AsyncMock(
            return_value={
                "google": {"files": 2, "emails": 3},
                "microsoft": {"files": 0, "emails": 1},
                "failures": [ONEDRIVE_FAILURE],
            }
        )
    )
    monkeypatch.setattr(
        cloud_admin,
        "_require_admin",
        AsyncMock(return_value=SimpleNamespace(tenant_id=TENANT)),
    )
    monkeypatch.setattr(cloud_admin, "_get_cloud_sync", lambda: service)
    monkeypatch.setattr(cloud_admin, "set_tenant_context", AsyncMock())

    body = await cloud_admin.cloud_search_sync(SimpleNamespace(), db=SimpleNamespace())

    assert body["total"] == 6
    assert body["failures"] == [ONEDRIVE_FAILURE]


@pytest.mark.asyncio
async def test_scheduled_cloud_sync_records_each_provider_failure(monkeypatch):
    from app.services import scheduler as sched

    tenant_uuid = uuid.UUID(TENANT)

    class _CredentialRows:
        def all(self):
            return [(tenant_uuid,)]

    session = SimpleNamespace(execute=AsyncMock(return_value=_CredentialRows()))

    @asynccontextmanager
    async def _session_maker():
        yield session

    completed = AsyncMock()
    recorded = AsyncMock()
    captured = AsyncMock()
    monkeypatch.setattr(sched, "async_session_maker", _session_maker)
    monkeypatch.setattr(sched, "_log_start", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(sched, "_log_complete", completed)
    monkeypatch.setattr(sched, "_log_failed", AsyncMock())
    monkeypatch.setattr(sched, "_apply_scheduler_tenant_context", AsyncMock())
    monkeypatch.setattr(sched, "_commit_and_restore_scheduler_context", AsyncMock())
    monkeypatch.setattr(sched, "record_integration_sync_run", recorded)
    monkeypatch.setattr(sched, "capture_integration_error", captured)
    monkeypatch.setattr(
        sched.CloudSyncService,
        "sync_all",
        AsyncMock(
            return_value={
                "google": {"files": 2, "emails": 3},
                "microsoft": {"files": 7, "emails": 11},
                "failures": [dict(ONEDRIVE_FAILURE)],
            }
        ),
    )

    token = sched._scheduler_tenant_id.set(tenant_uuid)
    try:
        await sched.LegalScheduler().run_cloud_sync()
    finally:
        sched._scheduler_tenant_id.reset(token)

    runs = [call.kwargs for call in recorded.await_args_list]
    assert {(run["provider"], run["status"]) for run in runs} == {
        ("google", "completed"),
        ("microsoft", "completed"),
        ("microsoft", "failed"),
    }
    failed = next(run for run in runs if run["status"] == "failed")
    assert failed["items_failed"] == 1
    assert failed["error_summary"] == "OneDrive sync failed (RuntimeError)"
    captured.assert_awaited_once()
    assert (
        captured.await_args.kwargs["message"] == "OneDrive sync failed (RuntimeError)"
    )
    assert "1 provider failure(s)" in completed.await_args.args[2]


class _UnreachableProviderClient:
    """Stands in for httpx.AsyncClient when the provider cannot be reached."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, *_args, **_kwargs):
        raise httpx.ConnectError("provider unreachable")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda s, db: s.sync_google_drive(db, TENANT), id="google-drive"),
        pytest.param(lambda s, db: s.sync_gmail_metadata(db, TENANT), id="gmail"),
        pytest.param(lambda s, db: s.sync_sharepoint(db, TENANT), id="sharepoint"),
        pytest.param(lambda s, db: s.sync_outlook_mail(db, TENANT), id="outlook"),
        pytest.param(
            lambda s, db: s._sync_google_drive_folder(
                db, TENANT, "provider-token", "folder-1", seen=set(), remaining=10
            ),
            id="google-drive-folder",
        ),
    ],
)
async def test_every_provider_sync_propagates_its_error_after_rollback(
    monkeypatch, call
):
    """No provider may turn an error into a zero-item success any more."""
    service = CloudSyncService()
    db = SimpleNamespace(rollback=AsyncMock(), commit=AsyncMock())
    monkeypatch.setattr(service, "_get_token", AsyncMock(return_value="provider-token"))
    monkeypatch.setattr(
        "app.services.cloud_sync.httpx.AsyncClient",
        lambda *_a, **_k: _UnreachableProviderClient(),
    )

    with pytest.raises(httpx.ConnectError):
        await call(service, db)

    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
