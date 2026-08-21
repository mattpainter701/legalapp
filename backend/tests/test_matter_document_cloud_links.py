import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.matter_document import MatterDocument
from app.routers import matter_documents
import app.services.matter_file_store as matter_file_store_module
from app.services.matter_file_store import (
    MatterFileStore,
    _document_folder_for_category,
    _extract_subfolder_id,
)
from app.services.provider_http import ProviderNotFound


class _FakeAsyncClient:
    def __init__(self, *, put_response=None, post_response=None, timeout=None):
        self.put_response = put_response
        self.post_response = post_response
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def put(self, *args, **kwargs):
        return self.put_response

    async def post(self, *args, **kwargs):
        return self.post_response


def _response(status_code, payload):
    return SimpleNamespace(
        status_code=status_code,
        json=lambda: payload,
        text=str(payload),
        headers={},
    )


def test_matter_document_exposes_google_drive_link_metadata():
    doc = MatterDocument(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        matter_id=uuid.uuid4(),
        filename="engagement-letter.docx",
        storage_path="https://drive.google.com/file/d/file-123/view",
    )

    assert doc.cloud_url == "https://drive.google.com/file/d/file-123/view"
    assert doc.storage_backend == "google_drive"


def test_matter_document_exposes_onedrive_link_metadata():
    doc = MatterDocument(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        matter_id=uuid.uuid4(),
        filename="complaint.pdf",
        storage_path="https://example.sharepoint.com/personal/user/Documents/complaint.pdf",
    )

    assert doc.cloud_url == doc.storage_path
    assert doc.storage_backend == "onedrive"


def test_matter_document_prefers_explicit_storage_metadata_over_legacy_url():
    doc = MatterDocument(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        matter_id=uuid.uuid4(),
        filename="shared-file.docx",
        storage_path="https://example.sharepoint.com/:w:/r/sites/matter/Shared%20Documents/file.docx",
        storage_backend="sharepoint",
        provider_object_id="item-123",
        provider_drive_id="drive-456",
        provider_parent_id="folder-789",
    )

    assert doc.cloud_url == doc.storage_path
    assert doc.storage_backend == "sharepoint"
    assert doc.provider_object_id == "item-123"
    assert doc.provider_drive_id == "drive-456"
    assert doc.provider_parent_id == "folder-789"


def test_matter_document_uses_explicit_provider_when_backend_is_missing():
    doc = MatterDocument(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        matter_id=uuid.uuid4(),
        filename="sharepoint-only.docx",
        storage_path="https://example.sharepoint.com/sites/matter/file.docx",
        storage_provider="sharepoint",
    )

    assert doc.storage_backend == "sharepoint"


def test_matter_document_accepts_storage_error_metadata():
    doc = MatterDocument(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        matter_id=uuid.uuid4(),
        filename="failed-delete.pdf",
        storage_path="https://drive.google.com/file/d/file-123/view",
        storage_backend="google_drive",
        provider_object_id="file-123",
        storage_error="delete failed: permission denied",
    )

    assert doc.storage_backend == "google_drive"
    assert doc.storage_error == "delete failed: permission denied"


def test_matter_document_marks_local_files_as_local():
    doc = MatterDocument(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        matter_id=uuid.uuid4(),
        filename="local.pdf",
        storage_path="/uploads/tenant/matter/local.pdf",
    )

    assert doc.cloud_url is None
    assert doc.storage_backend == "local"


def test_document_category_maps_to_provisioned_subfolders():
    assert _document_folder_for_category("pleading") == "pleadings"
    assert _document_folder_for_category("contract") == "documents"
    assert _document_folder_for_category("evidence") == "documents"
    assert _document_folder_for_category(None) == "documents"


def test_subfolder_lookup_accepts_historical_uploads_key():
    cloud_folder = {
        "onedrive": {
            "subfolders": {
                "uploads": "folder-uploads",
            }
        }
    }

    assert (
        _extract_subfolder_id(cloud_folder, "onedrive", "documents") == "folder-uploads"
    )


def test_storage_result_document_fields_persist_provider_metadata():
    storage_result = SimpleNamespace(
        storage_path="https://contoso.sharepoint.com/sites/site/doc.pdf",
        provider="microsoft",
        backend="sharepoint",
        provider_item_id="item-123",
        drive_id="drive-456",
        parent_id="folder-789",
        error=None,
    )

    assert matter_documents._storage_result_document_fields(storage_result) == {
        "storage_path": storage_result.storage_path,
        "storage_provider": "microsoft",
        "storage_backend": "sharepoint",
        "provider_object_id": "item-123",
        "provider_drive_id": "drive-456",
        "provider_parent_id": "folder-789",
        "storage_error": None,
    }


@pytest.mark.asyncio
async def test_onedrive_upload_result_captures_graph_item_metadata(monkeypatch):
    async def fake_token(db, tenant_id, provider):
        return "token"

    async def fake_ensure_path(token, folders):
        return "parent-123"

    graph_payload = {
        "id": "item-123",
        "webUrl": "https://contoso-my.sharepoint.com/doc.pdf",
        "parentReference": {"driveId": "drive-123", "id": "parent-from-graph"},
    }

    monkeypatch.setattr(matter_file_store_module, "get_fresh_token", fake_token)
    monkeypatch.setattr(
        matter_file_store_module, "_ensure_onedrive_path", fake_ensure_path
    )
    monkeypatch.setattr(
        matter_file_store_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(
            put_response=_response(201, graph_payload),
            timeout=kwargs.get("timeout"),
        ),
    )

    result = await MatterFileStore()._try_store_onedrive(
        db=None,
        tenant_id="tenant-1",
        matter_slug="matter-a",
        category="documents",
        filename="doc.pdf",
        content=b"pdf",
        content_type="application/pdf",
    )

    assert result.succeeded
    assert result.backend == "onedrive"
    assert result.storage_path == graph_payload["webUrl"]
    assert result.web_url == graph_payload["webUrl"]
    assert result.provider_item_id == "item-123"
    assert result.drive_id == "drive-123"
    assert result.parent_id == "parent-123"


@pytest.mark.asyncio
async def test_sharepoint_upload_result_preserves_configured_drive_and_parent(
    monkeypatch,
):
    async def fake_token(db, tenant_id, provider):
        return "token"

    graph_payload = {
        "id": "sp-item-123",
        "webUrl": "https://contoso.sharepoint.com/sites/site/doc.pdf",
        "parentReference": {"driveId": "response-drive", "id": "response-parent"},
    }

    monkeypatch.setattr(matter_file_store_module, "get_fresh_token", fake_token)
    monkeypatch.setattr(
        matter_file_store_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(
            put_response=_response(200, graph_payload),
            timeout=kwargs.get("timeout"),
        ),
    )

    result = await MatterFileStore()._try_store_sharepoint(
        db=None,
        tenant_id="tenant-1",
        filename="doc.pdf",
        content=b"pdf",
        content_type="application/pdf",
        folder_id="folder-123",
        drive_id="drive-123",
    )

    assert result.succeeded
    assert result.backend == "sharepoint"
    assert result.storage_path == graph_payload["webUrl"]
    assert result.provider_item_id == "sp-item-123"
    assert result.drive_id == "drive-123"
    assert result.parent_id == "folder-123"


@pytest.mark.asyncio
async def test_google_drive_upload_result_captures_file_id_and_parent(monkeypatch):
    async def fake_token(db, tenant_id, provider):
        return "token"

    async def fake_ensure_path(token, folders):
        return "gparent-123"

    async def fake_find_file(self, token, parent_id, filename):
        return None

    google_payload = {
        "id": "gfile-123",
        "webViewLink": "https://drive.google.com/file/d/gfile-123/view?usp=drivesdk",
    }

    monkeypatch.setattr(matter_file_store_module, "get_fresh_token", fake_token)
    monkeypatch.setattr(
        matter_file_store_module, "_ensure_gdrive_path", fake_ensure_path
    )
    monkeypatch.setattr(MatterFileStore, "_find_gdrive_file", fake_find_file)
    monkeypatch.setattr(
        matter_file_store_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(
            post_response=_response(200, google_payload),
            timeout=kwargs.get("timeout"),
        ),
    )

    result = await MatterFileStore()._try_store_google_drive(
        db=None,
        tenant_id="tenant-1",
        matter_slug="matter-a",
        category="documents",
        filename="doc.pdf",
        content=b"pdf",
        content_type="application/pdf",
    )

    assert result.succeeded
    assert result.backend == "google_drive"
    assert result.storage_path == google_payload["webViewLink"]
    assert result.web_url == google_payload["webViewLink"]
    assert result.provider_item_id == "gfile-123"
    assert result.parent_id == "gparent-123"


@pytest.mark.asyncio
async def test_google_drive_reuses_only_a_byte_identical_existing_file(monkeypatch):
    async def fake_token(db, tenant_id, provider):
        return "token"

    async def fake_find_file(self, token, parent_id, filename):
        return {
            "id": "existing-file",
            "webViewLink": "https://drive.google.com/file/d/existing-file/view",
            "md5Checksum": "900150983cd24fb0d6963f7d28e17f72",
            "version": "7",
        }

    class UnexpectedClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("byte-identical retry must not create a new file")

    monkeypatch.setattr(matter_file_store_module, "get_fresh_token", fake_token)
    monkeypatch.setattr(MatterFileStore, "_find_gdrive_file", fake_find_file)
    monkeypatch.setattr(matter_file_store_module.httpx, "AsyncClient", UnexpectedClient)

    result = await MatterFileStore()._try_store_google_drive(
        db=None,
        tenant_id="tenant-1",
        matter_slug="matter-a",
        category="documents",
        filename="draft.docx",
        content=b"abc",
        content_type=(
            "application/vnd.openxmlformats-officedocument." "wordprocessingml.document"
        ),
        folder_id="gparent-123",
    )

    assert result.succeeded
    assert result.provider_item_id == "existing-file"
    assert result.provider_checksum == "900150983cd24fb0d6963f7d28e17f72"


@pytest.mark.asyncio
async def test_google_drive_does_not_reuse_a_changed_same_name_file(monkeypatch):
    async def fake_token(db, tenant_id, provider):
        return "token"

    async def fake_find_file(self, token, parent_id, filename):
        return {
            "id": "user-edited-file",
            "webViewLink": "https://drive.google.com/file/d/user-edited-file/view",
            "md5Checksum": "149603e6c03516362a8da23f624db945",
        }

    created = {
        "id": "new-snapshot-file",
        "webViewLink": "https://drive.google.com/file/d/new-snapshot-file/view",
        "md5Checksum": "900150983cd24fb0d6963f7d28e17f72",
    }
    monkeypatch.setattr(matter_file_store_module, "get_fresh_token", fake_token)
    monkeypatch.setattr(MatterFileStore, "_find_gdrive_file", fake_find_file)
    monkeypatch.setattr(
        matter_file_store_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(
            post_response=_response(200, created),
            timeout=kwargs.get("timeout"),
        ),
    )

    result = await MatterFileStore()._try_store_google_drive(
        db=None,
        tenant_id="tenant-1",
        matter_slug="matter-a",
        category="documents",
        filename="draft.docx",
        content=b"abc",
        content_type=(
            "application/vnd.openxmlformats-officedocument." "wordprocessingml.document"
        ),
        folder_id="gparent-123",
    )

    assert result.succeeded
    assert result.provider_item_id == "new-snapshot-file"
    assert result.provider_item_id != "user-edited-file"


@pytest.mark.asyncio
async def test_delete_cloud_backing_fails_closed_for_legacy_url_only():
    doc = SimpleNamespace(
        tenant_id=uuid.uuid4(),
        storage_path="https://drive.google.com/file/d/file-123/view",
    )

    with pytest.raises(HTTPException) as exc:
        await matter_documents._delete_cloud_backing_if_needed(doc, object())

    assert exc.value.status_code == 501
    assert "database record was not removed" in exc.value.detail


@pytest.mark.asyncio
async def test_delete_cloud_backing_tolerates_provider_not_found(monkeypatch):
    doc = SimpleNamespace(
        tenant_id=uuid.uuid4(),
        storage_path="https://drive.google.com/file/d/file-123/view",
        storage_provider="google_drive",
        provider_object_id="file-123",
    )

    async def provider_delete(**kwargs):
        raise ProviderNotFound("already gone")

    monkeypatch.setattr(
        matter_documents, "_delete_cloud_provider_object", provider_delete
    )

    await matter_documents._delete_cloud_backing_if_needed(doc, object())
