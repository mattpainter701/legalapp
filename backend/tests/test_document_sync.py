"""Regression tests for immutable cloud-document versions."""

import hashlib

import pytest

import app.services.document_sync as document_sync_module
from app.services import token_vault
from app.services.document_sync import (
    _install_downloaded_synced_file,
    _synced_storage_path,
    _write_immutable_synced_file,
)


def test_cloud_sync_content_change_creates_a_new_immutable_path(tmp_path):
    file_info = {
        "drive": "onedrive",
        "drive_id": "drive-one",
        "id": "remote-document-42",
        "name": "Material Contract.pdf",
    }
    first_content = b"approved contract version"
    second_content = b"later cloud contract version"

    first_path = _synced_storage_path(tmp_path, file_info, first_content)
    _write_immutable_synced_file(first_path, first_content)
    repeated_path = _synced_storage_path(
        tmp_path,
        {**file_info, "name": "Renamed Contract.pdf"},
        first_content,
    )
    second_path = _synced_storage_path(tmp_path, file_info, second_content)
    _write_immutable_synced_file(repeated_path, first_content)
    _write_immutable_synced_file(second_path, second_content)

    assert repeated_path == first_path
    assert second_path != first_path
    assert first_path.read_bytes() == first_content
    assert second_path.read_bytes() == second_content


def test_cloud_sync_refuses_corrupt_content_addressed_file(tmp_path):
    file_info = {
        "drive": "google_drive",
        "id": "remote-document-77",
        "name": "Board Consent.docx",
    }
    content = b"expected exact bytes"
    path = _synced_storage_path(tmp_path, file_info, content)
    path.write_bytes(b"unexpected bytes")

    with pytest.raises(RuntimeError, match="unexpected bytes"):
        _write_immutable_synced_file(path, content)


def test_streamed_temp_file_installs_atomically(tmp_path):
    content = b"size-capped streamed content"
    temporary_path = tmp_path / ".sync-download-test"
    target_path = tmp_path / "content-addressed.pdf"
    temporary_path.write_bytes(content)

    _install_downloaded_synced_file(
        temporary_path,
        target_path,
        hashlib.sha256(content).hexdigest(),
    )

    assert temporary_path.exists() is False
    assert target_path.read_bytes() == content


@pytest.mark.asyncio
async def test_cloud_sync_rejects_declared_oversize_before_fetch(monkeypatch):
    monkeypatch.setattr(document_sync_module.settings, "MAX_FILE_SIZE_MB", 1)
    with pytest.raises(RuntimeError, match="exceeds"):
        await document_sync_module.document_sync.download_and_process(
            db=None,
            tenant_id="00000000-0000-0000-0000-000000000001",
            file_info={
                "drive": "google_drive",
                "id": "oversize",
                "name": "Huge.pdf",
                "size": 1_048_577,
            },
            user_id="00000000-0000-0000-0000-000000000002",
        )


@pytest.mark.asyncio
async def test_cloud_sync_stream_enforces_cap_and_removes_temp_file(
    tmp_path,
    monkeypatch,
):
    class FakeResponse:
        status_code = 200
        headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def aiter_bytes(self):
            yield b"a" * 700_000
            yield b"b" * 700_000

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, *args, **kwargs):
            return FakeResponse()

    async def fake_token(*args, **kwargs):
        return "token"

    monkeypatch.setattr(document_sync_module.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(document_sync_module.settings, "MAX_FILE_SIZE_MB", 1)
    monkeypatch.setattr(document_sync_module.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(token_vault, "get_fresh_user_token", fake_token)

    with pytest.raises(RuntimeError, match="size limit"):
        await document_sync_module.document_sync.download_and_process(
            db=None,
            tenant_id="00000000-0000-0000-0000-000000000001",
            file_info={
                "drive": "google_drive",
                "id": "streamed-oversize",
                "name": "Huge.pdf",
                "size": 0,
            },
            user_id="00000000-0000-0000-0000-000000000002",
        )

    sync_dir = tmp_path / "00000000-0000-0000-0000-000000000001" / "synced"
    assert list(sync_dir.iterdir()) == []
