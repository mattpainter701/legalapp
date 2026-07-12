import hashlib
from types import SimpleNamespace

import httpx
import pytest

import app.services.matter_file_store as store_module
from app.services.matter_file_store import (
    MatterFileAccessError,
    MatterFileCleanupError,
    MatterFileIntegrityError,
    MatterFileMetadataError,
    MatterFileStore,
    MatterFileTooLarge,
    StorageResult,
)


def _document(
    tenant_id: str,
    *,
    backend: str,
    storage_path: str = "https://attacker.invalid/not-a-download-source",
    provider: str | None = None,
    item_id: str | None = None,
    drive_id: str | None = None,
    file_size: int | None = None,
):
    return SimpleNamespace(
        tenant_id=tenant_id,
        _storage_backend=backend,
        storage_backend=backend,
        storage_provider=provider,
        storage_path=storage_path,
        provider_object_id=item_id,
        provider_drive_id=drive_id,
        file_size=file_size,
    )


@pytest.mark.asyncio
async def test_local_read_is_tenant_scoped_size_bounded_and_hash_checked(
    tmp_path, monkeypatch
):
    tenant_id = "tenant-a"
    tenant_root = tmp_path / tenant_id
    tenant_root.mkdir()
    path = tenant_root / "agreement.pdf"
    content = b"%PDF-1.7\nsource bytes"
    path.write_bytes(content)
    monkeypatch.setattr(store_module.settings, "UPLOAD_DIR", str(tmp_path))

    document = _document(
        tenant_id,
        backend="local",
        storage_path=str(path),
        provider="local",
        file_size=len(content),
    )
    expected_hash = hashlib.sha256(content).hexdigest()
    store = MatterFileStore()

    assert (
        await store.read_matter_file_bytes(
            db=object(),
            tenant_id=tenant_id,
            document=document,
            expected_sha256=expected_hash,
        )
        == content
    )

    with pytest.raises(MatterFileIntegrityError):
        await store.read_matter_file_bytes(
            db=object(),
            tenant_id=tenant_id,
            document=document,
            expected_sha256="0" * 64,
        )

    with pytest.raises(MatterFileTooLarge):
        await store.read_matter_file_bytes(
            db=object(),
            tenant_id=tenant_id,
            document=document,
            max_bytes=len(content) - 1,
        )


@pytest.mark.asyncio
async def test_local_read_rejects_cross_tenant_and_traversal_paths(
    tmp_path, monkeypatch
):
    tenant_id = "tenant-a"
    (tmp_path / tenant_id).mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside")
    monkeypatch.setattr(store_module.settings, "UPLOAD_DIR", str(tmp_path))
    store = MatterFileStore()

    with pytest.raises(MatterFileAccessError):
        await store.read_matter_file_bytes(
            db=object(),
            tenant_id=tenant_id,
            document=_document(
                tenant_id,
                backend="local",
                provider="local",
                storage_path=str(outside),
                file_size=len(b"outside"),
            ),
        )

    with pytest.raises(MatterFileAccessError):
        await store._store_local(
            tenant_id,
            "matter",
            "documents",
            "../../../../outside.pdf",
            b"blocked",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("backend", "provider", "item_id", "drive_id", "token_provider", "path"),
    [
        (
            "google_drive",
            "google",
            "google-item",
            None,
            "google",
            "/drive/v3/files/google-item",
        ),
        (
            "onedrive",
            "microsoft",
            "onedrive-item",
            None,
            "microsoft",
            "/v1.0/me/drive/items/onedrive-item/content",
        ),
        (
            "sharepoint",
            "microsoft",
            "sharepoint-item",
            "sharepoint-drive",
            "microsoft",
            "/v1.0/drives/sharepoint-drive/items/sharepoint-item/content",
        ),
    ],
)
async def test_cloud_read_uses_fresh_tenant_token_and_only_durable_provider_ids(
    monkeypatch,
    backend,
    provider,
    item_id,
    drive_id,
    token_provider,
    path,
):
    tenant_id = "tenant-a"
    content = b"provider document bytes"
    token_calls = []
    requested_urls = []

    async def fake_get_fresh_token(db, resolved_tenant_id, resolved_provider):
        token_calls.append((db, resolved_tenant_id, resolved_provider))
        return "fresh-token"

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        assert request.headers["Authorization"] == "Bearer fresh-token"
        return httpx.Response(
            200,
            content=content,
            headers={"Content-Length": str(len(content))},
        )

    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(store_module, "get_fresh_token", fake_get_fresh_token)
    monkeypatch.setattr(store_module.httpx, "AsyncClient", mock_client)

    db = object()
    document = _document(
        tenant_id,
        backend=backend,
        provider=provider,
        item_id=item_id,
        drive_id=drive_id,
        file_size=len(content),
    )
    result = await MatterFileStore().read_matter_file_bytes(
        db=db,
        tenant_id=tenant_id,
        document=document,
        expected_sha256=hashlib.sha256(content).hexdigest(),
    )

    assert result == content
    assert token_calls == [(db, tenant_id, token_provider)]
    assert len(requested_urls) == 1
    assert path in requested_urls[0]
    assert "attacker.invalid" not in requested_urls[0]


@pytest.mark.asyncio
async def test_cloud_read_requires_durable_item_id_before_any_token_or_http_call(
    monkeypatch,
):
    async def unexpected_token(*_args, **_kwargs):
        raise AssertionError("token lookup must not occur without a provider item ID")

    monkeypatch.setattr(store_module, "get_fresh_token", unexpected_token)
    document = _document(
        "tenant-a",
        backend="google_drive",
        provider="google",
        item_id=None,
        file_size=1,
    )

    with pytest.raises(MatterFileMetadataError):
        await MatterFileStore().read_matter_file_bytes(
            db=object(),
            tenant_id="tenant-a",
            document=document,
        )


@pytest.mark.asyncio
async def test_explicit_primary_cloud_never_spills_to_another_cloud_and_warns_on_local_fallback(
    tmp_path,
    monkeypatch,
):
    tenant_id = "tenant-a"
    monkeypatch.setattr(store_module.settings, "UPLOAD_DIR", str(tmp_path))
    store = MatterFileStore()
    calls = []

    async def failed_google(*_args, **_kwargs):
        calls.append("google_drive")
        return StorageResult(
            provider="google",
            backend="google_drive",
            error="provider failure that must not be exposed verbatim",
        )

    async def unexpected_onedrive(*_args, **_kwargs):
        raise AssertionError("explicit Google preference must not spill to OneDrive")

    async def unexpected_sharepoint(*_args, **_kwargs):
        raise AssertionError("explicit Google preference must not spill to SharePoint")

    monkeypatch.setattr(store, "_try_store_google_drive", failed_google)
    monkeypatch.setattr(store, "_try_store_onedrive", unexpected_onedrive)
    monkeypatch.setattr(store, "_try_store_sharepoint", unexpected_sharepoint)

    content = b"durably retained bytes"
    result = await store.store_matter_file_result(
        db=object(),
        tenant_id=tenant_id,
        matter_slug="matter-1",
        category="documents",
        filename="agreement.pdf",
        content=content,
        content_type="application/pdf",
        preferred_provider="google_drive",
    )

    assert calls == ["google_drive"]
    assert result.provider == "local"
    assert result.backend == "local"
    assert result.storage_path is not None
    assert result.error is not None
    assert "Configured Google Drive upload failed" in result.error
    assert "saved to local storage" in result.error
    assert "Reconnect Google Drive" in result.error
    assert "provider failure that must not be exposed verbatim" not in result.error
    assert (
        tmp_path / tenant_id / "matters" / "matter-1" / "documents" / "agreement.pdf"
    ).read_bytes() == content


@pytest.mark.asyncio
async def test_auto_mode_keeps_first_available_cross_cloud_cascade(monkeypatch):
    store = MatterFileStore()
    calls = []

    async def failed_onedrive(*_args, **_kwargs):
        calls.append("onedrive")
        return StorageResult(
            provider="microsoft",
            backend="onedrive",
            error="not connected",
        )

    async def successful_sharepoint(*_args, **_kwargs):
        calls.append("sharepoint")
        return StorageResult(
            provider="microsoft",
            backend="sharepoint",
            storage_path="https://contoso.sharepoint.com/document.pdf",
            provider_item_id="item-1",
            drive_id="drive-1",
        )

    async def unexpected_google(*_args, **_kwargs):
        raise AssertionError("auto mode should stop at the first successful provider")

    monkeypatch.setattr(store, "_try_store_onedrive", failed_onedrive)
    monkeypatch.setattr(store, "_try_store_sharepoint", successful_sharepoint)
    monkeypatch.setattr(store, "_try_store_google_drive", unexpected_google)

    result = await store.store_matter_file_result(
        db=object(),
        tenant_id="tenant-a",
        matter_slug="matter-1",
        category="documents",
        filename="agreement.pdf",
        content=b"content",
        content_type="application/pdf",
        preferred_provider=None,
    )

    assert calls == ["onedrive", "sharepoint"]
    assert result.backend == "sharepoint"
    assert result.error is None


@pytest.mark.asyncio
async def test_cleanup_deletes_only_exact_local_file_beneath_tenant_root(
    tmp_path, monkeypatch
):
    tenant_id = "tenant-a"
    tenant_root = tmp_path / tenant_id
    tenant_root.mkdir()
    staged = tenant_root / "matters" / "matter-1" / "generated" / "form.pdf"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"staged")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside")
    monkeypatch.setattr(store_module.settings, "UPLOAD_DIR", str(tmp_path))
    store = MatterFileStore()

    await store.delete_stored_result(
        db=object(),
        tenant_id=tenant_id,
        result=StorageResult(
            provider="local",
            backend="local",
            storage_path=str(staged),
        ),
    )
    assert not staged.exists()

    with pytest.raises(MatterFileAccessError):
        await store.delete_stored_result(
            db=object(),
            tenant_id=tenant_id,
            result=StorageResult(
                provider="local",
                backend="local",
                storage_path=str(outside),
            ),
        )
    assert outside.read_bytes() == b"outside"


@pytest.mark.asyncio
async def test_cleanup_rejects_local_symlink_even_when_target_is_in_tenant_root(
    tmp_path, monkeypatch
):
    tenant_id = "tenant-a"
    tenant_root = tmp_path / tenant_id
    tenant_root.mkdir()
    target = tenant_root / "real-staged.pdf"
    target.write_bytes(b"inside target")
    link = tenant_root / "linked.pdf"
    try:
        link.symlink_to(target)
    except OSError:
        # Windows may disallow symlink creation without Developer Mode. Still
        # exercise the fail-closed branch; Linux CI uses the real symlink above.
        link.write_bytes(b"simulated symlink entry")
        original_is_symlink = type(link).is_symlink
        monkeypatch.setattr(
            type(link),
            "is_symlink",
            lambda path: path == link or original_is_symlink(path),
        )
    monkeypatch.setattr(store_module.settings, "UPLOAD_DIR", str(tmp_path))

    with pytest.raises(MatterFileAccessError):
        await MatterFileStore().delete_stored_result(
            db=object(),
            tenant_id=tenant_id,
            result=StorageResult(
                provider="local",
                backend="local",
                storage_path=str(link),
            ),
        )
    assert target.read_bytes() == b"inside target"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("backend", "provider", "item_id", "drive_id", "token_provider", "expected_url"),
    [
        (
            "google_drive",
            "google",
            "google item",
            None,
            "google",
            "https://www.googleapis.com/drive/v3/files/google%20item",
        ),
        (
            "onedrive",
            "microsoft",
            "one item",
            None,
            "microsoft",
            "https://graph.microsoft.com/v1.0/me/drive/items/one%20item",
        ),
        (
            "sharepoint",
            "microsoft",
            "share item",
            "drive id",
            "microsoft",
            "https://graph.microsoft.com/v1.0/drives/drive%20id/items/share%20item",
        ),
    ],
)
async def test_cloud_cleanup_uses_fixed_host_and_durable_provider_ids(
    monkeypatch,
    backend,
    provider,
    item_id,
    drive_id,
    token_provider,
    expected_url,
):
    tenant_id = "tenant-a"
    token_calls = []
    requested = []

    async def token(db, resolved_tenant, resolved_provider):
        token_calls.append((db, resolved_tenant, resolved_provider))
        return "fresh-token"

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        assert request.method == "DELETE"
        assert request.headers["Authorization"] == "Bearer fresh-token"
        return httpx.Response(204)

    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(store_module, "get_fresh_token", token)
    monkeypatch.setattr(store_module.httpx, "AsyncClient", mock_client)
    db = object()
    await MatterFileStore().delete_stored_result(
        db=db,
        tenant_id=tenant_id,
        result=StorageResult(
            provider=provider,
            backend=backend,
            storage_path="https://attacker.invalid/display-only",
            provider_item_id=item_id,
            drive_id=drive_id,
        ),
    )

    assert token_calls == [(db, tenant_id, token_provider)]
    assert requested == [expected_url]
    assert "attacker.invalid" not in requested[0]


@pytest.mark.asyncio
async def test_cloud_cleanup_treats_provider_404_as_idempotent_success(monkeypatch):
    async def token(*_args):
        return "fresh-token"

    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _request: httpx.Response(404))

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(store_module, "get_fresh_token", token)
    monkeypatch.setattr(store_module.httpx, "AsyncClient", mock_client)

    await MatterFileStore().delete_stored_result(
        db=object(),
        tenant_id="tenant-a",
        result=StorageResult(
            provider="google",
            backend="google_drive",
            provider_item_id="already-gone",
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        StorageResult(provider="google", backend="google_drive"),
        StorageResult(
            provider="microsoft",
            backend="sharepoint",
            provider_item_id="item-without-drive",
        ),
        StorageResult(
            provider="unknown",
            backend="unsupported",
            provider_item_id="item",
        ),
    ],
)
async def test_cloud_cleanup_missing_metadata_and_unknown_backends_fail_closed(
    monkeypatch, result
):
    async def unexpected_token(*_args):
        raise AssertionError("metadata must be validated before token lookup")

    monkeypatch.setattr(store_module, "get_fresh_token", unexpected_token)
    with pytest.raises(MatterFileCleanupError):
        await MatterFileStore().delete_stored_result(
            db=object(), tenant_id="tenant-a", result=result
        )


@pytest.mark.asyncio
async def test_cloud_cleanup_missing_tenant_token_fails_closed(monkeypatch):
    async def missing_token(*_args):
        return None

    monkeypatch.setattr(store_module, "get_fresh_token", missing_token)
    with pytest.raises(MatterFileCleanupError, match="credentials are unavailable"):
        await MatterFileStore().delete_stored_result(
            db=object(),
            tenant_id="tenant-a",
            result=StorageResult(
                provider="google",
                backend="google_drive",
                provider_item_id="item",
            ),
        )
