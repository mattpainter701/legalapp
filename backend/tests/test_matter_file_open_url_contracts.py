from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

import app.services.matter_file_store as store_module
from app.services.matter_file_store import (
    MatterFileAccessError,
    MatterFileMetadataError,
    MatterFileNotFound,
    MatterFileStore,
)
from app.services.provider_http import ProviderAuthError, ProviderError


def _document(
    *,
    tenant_id="tenant-a",
    backend="google_drive",
    item_id="provider item",
    drive_id=None,
):
    return SimpleNamespace(
        tenant_id=tenant_id,
        _storage_backend=backend,
        storage_backend=backend,
        storage_provider=None,
        storage_path="https://attacker.invalid/display-only",
        provider_object_id=item_id,
        provider_drive_id=drive_id,
    )


def _mock_client(monkeypatch, handler):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(store_module.httpx, "AsyncClient", client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("backend", "drive_id", "payload", "expected_url", "request_fragment"),
    [
        (
            "google_drive",
            None,
            {"id": "provider item"},
            "https://drive.google.com/file/d/provider item/view",
            "/drive/v3/files/provider%20item",
        ),
        (
            "onedrive",
            None,
            {"id": "provider item", "webUrl": "https://onedrive.live.com/edit/1"},
            "https://onedrive.live.com/edit/1",
            "/me/drive/items/provider%20item",
        ),
        (
            "sharepoint",
            "drive id",
            {
                "id": "provider item",
                "webUrl": "https://tenant.sharepoint.com/sites/legal/document.docx",
            },
            "https://tenant.sharepoint.com/sites/legal/document.docx",
            "/drives/drive%20id/items/provider%20item",
        ),
    ],
)
async def test_open_url_is_refreshed_from_durable_provider_metadata(
    monkeypatch, backend, drive_id, payload, expected_url, request_fragment
):
    token_calls = []
    requested = []

    async def token(db, tenant_id, provider):
        token_calls.append((db, tenant_id, provider))
        return "fresh-token"

    def handler(request):
        requested.append(str(request.url))
        assert request.headers["Authorization"] == "Bearer fresh-token"
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(store_module, "get_fresh_token", token)
    _mock_client(monkeypatch, handler)
    db = object()

    result = await MatterFileStore().get_matter_file_open_url(
        db=db,
        tenant_id="tenant-a",
        document=_document(backend=backend, drive_id=drive_id),
    )

    assert result == expected_url
    assert request_fragment in requested[0]
    assert "attacker.invalid" not in requested[0]
    expected_provider = "google" if backend == "google_drive" else "microsoft"
    assert token_calls == [(db, "tenant-a", expected_provider)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("document", "error_type"),
    [
        (_document(tenant_id="tenant-b"), MatterFileAccessError),
        (_document(backend="local"), MatterFileMetadataError),
        (_document(item_id=None), MatterFileMetadataError),
        (
            _document(backend="sharepoint", drive_id=None),
            MatterFileMetadataError,
        ),
    ],
)
async def test_open_url_rejects_foreign_local_or_incomplete_metadata_before_http(
    monkeypatch, document, error_type
):
    async def unexpected_token(*_args):
        raise AssertionError("metadata must fail before credential resolution")

    monkeypatch.setattr(store_module, "get_fresh_token", unexpected_token)

    with pytest.raises(error_type):
        await MatterFileStore().get_matter_file_open_url(
            db=object(), tenant_id="tenant-a", document=document
        )


@pytest.mark.asyncio
async def test_open_url_requires_live_tenant_credentials(monkeypatch):
    async def missing_token(*_args):
        return None

    monkeypatch.setattr(store_module, "get_fresh_token", missing_token)

    with pytest.raises(ProviderAuthError, match="credentials are unavailable"):
        await MatterFileStore().get_matter_file_open_url(
            db=object(), tenant_id="tenant-a", document=_document()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (
            httpx.Response(
                200, content=b"{", headers={"Content-Type": "application/json"}
            ),
            ProviderError,
        ),
        (httpx.Response(200, json={"trashed": True}), MatterFileNotFound),
        (
            httpx.Response(200, json={"webViewLink": "https://attacker.invalid/edit"}),
            ProviderError,
        ),
    ],
)
async def test_open_url_fails_closed_on_invalid_deleted_or_untrusted_metadata(
    monkeypatch, response, error_type
):
    async def token(*_args):
        return "fresh-token"

    monkeypatch.setattr(store_module, "get_fresh_token", token)
    _mock_client(monkeypatch, lambda _request: response)

    with pytest.raises(error_type):
        await MatterFileStore().get_matter_file_open_url(
            db=object(), tenant_id="tenant-a", document=_document()
        )


@pytest.mark.asyncio
async def test_open_url_maps_transport_failure_to_provider_error(monkeypatch):
    async def token(*_args):
        return "fresh-token"

    def failed_transport(request):
        raise httpx.ConnectError("provider network detail", request=request)

    monkeypatch.setattr(store_module, "get_fresh_token", token)
    _mock_client(monkeypatch, failed_transport)

    with pytest.raises(ProviderError, match="metadata lookup did not complete"):
        await MatterFileStore().get_matter_file_open_url(
            db=object(), tenant_id="tenant-a", document=_document()
        )
