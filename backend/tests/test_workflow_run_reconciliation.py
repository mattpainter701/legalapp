from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services import workflow_run_reconciliation as service
from app.services.automation_capabilities import CapabilityError


class Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def setup(monkeypatch, backend="onedrive", metadata=None):
    operation = NS(
        target_backend=backend,
        target_drive_id="drive",
        target_parent_id="folder",
        content_sha256="a" * 64,
        content_size=42,
    )
    metadata = metadata or {
        "id": "item",
        "parentReference": {"id": "folder", "driveId": "drive"},
        "eTag": "etag",
        "cTag": "v1",
    }
    response = NS(raise_for_status=lambda: None, json=lambda: metadata)
    client = Session()
    client.get = AsyncMock(return_value=response)
    monkeypatch.setattr(service, "async_session_maker", Session)
    monkeypatch.setattr(service, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        service, "get_fresh_token", AsyncMock(return_value="test-token")
    )
    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kwargs: client)
    reader = AsyncMock(return_value=b"verified bytes")
    monkeypatch.setattr(service.MatterFileStore, "read_matter_file_bytes", reader)
    return operation, client, reader


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["onedrive", "sharepoint", "google_drive"])
async def test_reconciliation_checks_provider_folder_and_exact_bytes(
    monkeypatch, backend
):
    metadata = (
        {"id": "item", "parents": ["folder"], "driveId": "drive", "version": "2"}
        if backend == "google_drive"
        else None
    )
    operation, client, reader = setup(monkeypatch, backend, metadata)
    result = await service.verify_provider_object(
        tenant_id=uuid4(), operation=operation, provider_object_id="item"
    )
    assert result["provider_version_id"] in {"v1", "2"}
    assert client.get.call_args.args[0].startswith("https://")
    assert reader.call_args.kwargs["expected_sha256"] == "a" * 64
    assert reader.call_args.kwargs["expected_size"] == 42


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,code",
    [
        ({"id": "other"}, "cloud_object_unavailable"),
        ({"deleted": {}}, None),
        ({"trashed": True}, "cloud_object_unavailable"),
        (
            {"parentReference": {"id": "foreign", "driveId": "drive"}},
            "cloud_folder_mismatch",
        ),
        (
            {"parentReference": {"id": "folder", "driveId": "foreign"}},
            "cloud_folder_mismatch",
        ),
    ],
)
async def test_reconciliation_rejects_foreign_or_removed_object(
    monkeypatch, change, code
):
    metadata = {
        "id": "item",
        "parentReference": {"id": "folder", "driveId": "drive"},
        **change,
    }
    operation, client, reader = setup(monkeypatch, metadata=metadata)
    if code is None:
        # Presence of the Graph deleted facet, including {}, means removed.
        code = "cloud_object_unavailable"
    with pytest.raises(CapabilityError) as error:
        await service.verify_provider_object(
            tenant_id=uuid4(), operation=operation, provider_object_id="item"
        )
    assert error.value.code == code
    reader.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciliation_fails_closed_on_credentials_provider_and_bytes(
    monkeypatch,
):
    operation, client, reader = setup(monkeypatch)
    service.get_fresh_token.return_value = None
    with pytest.raises(CapabilityError, match="Reconnect"):
        await service.verify_provider_object(
            tenant_id=uuid4(), operation=operation, provider_object_id="item"
        )
    service.get_fresh_token.return_value = "test-token"
    client.get.side_effect = service.httpx.ReadTimeout("private provider detail")
    with pytest.raises(CapabilityError, match="could not be verified"):
        await service.verify_provider_object(
            tenant_id=uuid4(), operation=operation, provider_object_id="item"
        )
    client.get.side_effect = None
    reader.side_effect = ValueError("private byte content")
    with pytest.raises(CapabilityError, match="original draft bytes"):
        await service.verify_provider_object(
            tenant_id=uuid4(), operation=operation, provider_object_id="item"
        )
    operation.target_backend = "local"
    with pytest.raises(CapabilityError, match="provider is unavailable"):
        await service.verify_provider_object(
            tenant_id=uuid4(), operation=operation, provider_object_id="item"
        )
    operation.target_backend = "sharepoint"
    operation.target_drive_id = None
    with pytest.raises(CapabilityError, match="drive is unavailable"):
        await service.verify_provider_object(
            tenant_id=uuid4(), operation=operation, provider_object_id="item"
        )
