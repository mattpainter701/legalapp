import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.database import get_db
from app.routers import storage_migration as routes


TENANT = uuid.uuid4()
ADMIN = SimpleNamespace(id=uuid.uuid4(), tenant_id=TENANT)


def _row(**changes):
    values = dict(
        id=uuid.uuid4(), tenant_id=TENANT, phase="planning",
        source_provider="google_drive", target_provider="onedrive",
        bucket_counts={"matched": 0, "missing": 0, "ambiguous": 0},
        evidence_version=None, needs_reindex=False, error_message=None,
        acknowledged_policy=None,
    )
    values.update(changes)
    return SimpleNamespace(**values)


async def _client(db, monkeypatch, *, user=ADMIN):
    app = FastAPI()
    app.include_router(routes.router)

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(routes, "require_admin", AsyncMock(return_value=user))
    monkeypatch.setattr(routes, "set_tenant_context", AsyncMock())
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_start_returns_state_and_constructs_target_root(monkeypatch):
    db = AsyncMock()
    db.commit = AsyncMock()
    created = _row(target_provider="sharepoint")
    routes.service.start = AsyncMock(return_value=created)
    async with await _client(db, monkeypatch) as client:
        response = await client.post("/api/admin/storage-migrations", json={
            "target_provider": "sharepoint", "target_root_id": "root-7", "target_drive_id": "drive-9",
        })
    assert response.status_code == 200
    assert response.json()["target_provider"] == "sharepoint"
    routes.service.start.assert_awaited_once()
    assert routes.service.start.call_args.kwargs["target_root"] == {
        "id": "root-7", "path": "claritylegal-records", "drive_id": "drive-9"
    }


@pytest.mark.asyncio
async def test_start_rejects_drive_without_root_and_service_errors(monkeypatch):
    db = AsyncMock()
    async with await _client(db, monkeypatch) as client:
        response = await client.post("/api/admin/storage-migrations", json={"target_provider": "onedrive", "target_drive_id": "drive"})
    assert response.status_code == 422
    routes.service.start = AsyncMock(side_effect=ValueError("already active"))
    async with await _client(db, monkeypatch) as client:
        response = await client.post("/api/admin/storage-migrations", json={"target_provider": "onedrive"})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_latest_route_is_selected_before_uuid_route_and_returns_evidence(monkeypatch):
    db = AsyncMock()
    latest = _row(phase="awaiting_confirmation", evidence_version="server-evidence-1")
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: latest))
    async with await _client(db, monkeypatch) as client:
        response = await client.get("/api/admin/storage-migrations/latest")
    assert response.status_code == 200
    assert response.json()["evidence_version"] == "server-evidence-1"


@pytest.mark.asyncio
async def test_tenant_isolation_and_cutover_schedules_reindex(monkeypatch):
    migration = _row(phase="awaiting_confirmation", evidence_version="e-1")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: migration))
    routes.service.cutover = AsyncMock(return_value=_row(phase="complete", needs_reindex=True, evidence_version="e-1"))
    reindex = AsyncMock()
    monkeypatch.setattr(routes, "_reindex", reindex)
    async with await _client(db, monkeypatch) as client:
        response = await client.post(f"/api/admin/storage-migrations/{migration.id}/cutover", json={
            "evidence_version": "e-1", "acknowledged_policy": "all-matters-and-documents-resolved",
        })
    assert response.status_code == 200
    assert response.json()["needs_reindex"] is True
    assert routes.service.cutover.call_args.kwargs["evidence_version"] == "e-1"

    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))
    async with await _client(db, monkeypatch) as client:
        response = await client.get(f"/api/admin/storage-migrations/{migration.id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_retry_reindex_rejects_wrong_state(monkeypatch):
    db = AsyncMock()
    pending = _row(phase="complete", needs_reindex=True, error_message="provider unavailable")
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: pending))
    monkeypatch.setattr(routes, "_reindex", AsyncMock())
    async with await _client(db, monkeypatch) as client:
        response = await client.post(f"/api/admin/storage-migrations/{pending.id}/reindex")
    assert response.status_code == 200
    assert response.json()["error_message"] == "provider unavailable"

    pending.phase = "abandoned"
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: pending))
    async with await _client(db, monkeypatch) as client:
        response = await client.post(f"/api/admin/storage-migrations/{pending.id}/reindex")
    assert response.status_code == 409
