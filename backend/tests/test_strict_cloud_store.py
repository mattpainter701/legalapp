from __future__ import annotations

import pytest

from app.services.matter_file_store import MatterFileStore, StorageResult


@pytest.mark.asyncio
async def test_require_cloud_fails_closed_without_local_or_cross_provider_fallback(
    monkeypatch,
):
    store = MatterFileStore()
    calls: list[str] = []

    async def failed_google(*_args, **_kwargs):
        calls.append("google_drive")
        return StorageResult(
            provider="google",
            backend="google_drive",
            error="provider detail that must not be returned",
        )

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("strict Google storage cannot spill to another backend")

    async def unexpected_local(*_args, **_kwargs):
        raise AssertionError("strict cloud storage cannot write hosted local bytes")

    monkeypatch.setattr(store, "_try_store_google_drive", failed_google)
    monkeypatch.setattr(store, "_try_store_onedrive", unexpected)
    monkeypatch.setattr(store, "_try_store_sharepoint", unexpected)
    monkeypatch.setattr(store, "_store_local", unexpected_local)

    result = await store.store_matter_file_result(
        db=object(),
        tenant_id="tenant-a",
        matter_slug="matter-1",
        category="documents",
        filename="draft.docx",
        content=b"docx bytes",
        content_type=(
            "application/vnd.openxmlformats-officedocument." "wordprocessingml.document"
        ),
        preferred_provider="google_drive",
        require_cloud=True,
    )

    assert calls == ["google_drive"]
    assert result.succeeded is False
    assert result.backend == "google_drive"
    assert result.storage_path is None
    assert "local storage is disabled" in (result.error or "")
    assert "provider detail" not in (result.error or "")


def test_storage_result_carries_provider_concurrency_evidence():
    result = StorageResult(
        provider="microsoft",
        backend="sharepoint",
        provider_item_id="item-1",
        provider_etag='"etag-1"',
        provider_version_id="version-7",
        provider_modified_at="2026-08-21T12:00:00Z",
        provider_checksum="quick-xor-value",
    )

    assert result.succeeded is True
    assert result.provider_etag == '"etag-1"'
    assert result.provider_version_id == "version-7"
