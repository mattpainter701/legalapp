import hashlib
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services import cloud_artifact_materialization as cloud
from app.services.matter_file_store import StorageResult


class DB:
    def __init__(self, values):
        self.scalar = AsyncMock(side_effect=values)
        self.added = []

    def begin_nested(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        for row in self.added:
            if row.id is None:
                row.id = uuid4()


def fixture(monkeypatch, status="planned", prior=False):
    tenant, matter_id, task_id, artifact_id, revision_id = [uuid4() for _ in range(5)]
    artifact = NS(
        id=artifact_id,
        matter_id=matter_id,
        task_id=task_id,
        current_revision_no=1,
        title="Draft",
        output_document_id=None,
    )
    revision = NS(
        id=revision_id,
        revision_no=1,
        content_text="Private text",
        renderer_version="test",
    )
    task = NS(id=task_id)
    matter = NS(
        id=matter_id,
        cloud_folder={
            "onedrive": {"drive_id": "drive", "subfolders": {"documents": "folder"}}
        },
    )
    content = cloud.render_revision_docx(title="Draft", content="Private text")
    operation = NS(
        id=uuid4(),
        status=status,
        attempts=0,
        delivery_certainty="unknown",
        content_sha256=hashlib.sha256(content).hexdigest(),
        content_size=len(content),
        target_provider="microsoft",
        target_backend="onedrive",
        target_drive_id="drive",
        target_parent_id="folder",
        provider_object_id="item" if status == "provider_accepted" else None,
        provider_etag=None,
        provider_version_id=None,
    )
    db = DB(
        [
            artifact,
            revision,
            task,
            matter,
            NS(primary_cloud_provider="onedrive"),
            operation if prior else None,
            None,
        ]
    )
    monkeypatch.setattr(
        cloud, "ensure_document_storage_operation", AsyncMock(return_value=operation)
    )
    monkeypatch.setattr(cloud, "append_document_integrity_event", AsyncMock())
    materializer = cloud.CloudArtifactMaterializer()
    materializer._upload = AsyncMock(
        return_value=StorageResult(
            provider="microsoft",
            backend="onedrive",
            provider_item_id="item",
            parent_id="folder",
            drive_id="drive",
        )
    )
    materializer._readback = AsyncMock(return_value=content)
    materializer._compensate = AsyncMock(return_value=True)
    args = dict(
        db=db,
        tenant_id=tenant,
        artifact_id=artifact_id,
        revision_id=revision_id,
        task_id=task_id,
        uploaded_by_user_id=uuid4(),
    )
    return materializer, operation, args


@pytest.mark.asyncio
async def test_checkpoints_wrap_provider_write_and_receipt(monkeypatch):
    materializer, operation, args = fixture(monkeypatch)
    phases = []

    async def checkpoint(**values):
        phases.append((values["phase"], operation.status, operation.provider_object_id))

    result = await materializer.materialize(**args, runtime_checkpoint=checkpoint)
    assert phases == [
        ("cloud_write_started", "writing", None),
        ("cloud_provider_accepted", "provider_accepted", "item"),
    ]
    assert result.document.storage_state == "verified" and operation.status == "linked"
    assert materializer._upload.await_count == 1


@pytest.mark.asyncio
async def test_accepted_receipt_recovers_without_upload(monkeypatch):
    materializer, operation, args = fixture(
        monkeypatch, "provider_accepted", prior=True
    )
    result = await materializer.materialize(**args, runtime_checkpoint=AsyncMock())
    assert result.document.provider_object_id == "item"
    materializer._upload.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["writing", "ambiguous", "linked"])
async def test_uncertain_or_missing_link_never_uploads_again(monkeypatch, status):
    materializer, operation, args = fixture(monkeypatch, status, prior=True)
    with pytest.raises(cloud.CloudReconciliationRequired):
        await materializer.materialize(**args, runtime_checkpoint=AsyncMock())
    materializer._upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_unconfirmed_write_retains_uncertain_operation(monkeypatch):
    materializer, operation, args = fixture(monkeypatch)
    materializer._upload.return_value = StorageResult(
        provider="microsoft", backend="onedrive", error="unconfirmed"
    )
    with pytest.raises(cloud.CloudUploadError):
        await materializer.materialize(**args, runtime_checkpoint=AsyncMock())
    assert operation.status == "ambiguous"
    materializer._compensate.assert_not_awaited()


@pytest.mark.asyncio
async def test_checkpointed_failed_readback_retains_provider_receipt(monkeypatch):
    materializer, operation, args = fixture(monkeypatch)
    materializer._readback.side_effect = ValueError("readback unavailable")
    with pytest.raises(cloud.CloudReconciliationRequired):
        await materializer.materialize(**args, runtime_checkpoint=AsyncMock())
    assert operation.provider_object_id == "item" and operation.status == "ambiguous"
    materializer._compensate.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_bytes_and_missing_receipt_are_blocked(monkeypatch):
    materializer, operation, args = fixture(monkeypatch, prior=True)
    operation.content_sha256 = "0" * 64
    with pytest.raises(cloud.CloudIntegrityError):
        await materializer.materialize(**args, runtime_checkpoint=AsyncMock())
    materializer._upload.assert_not_awaited()
    materializer, operation, args = fixture(
        monkeypatch, "provider_accepted", prior=True
    )
    operation.provider_object_id = None
    with pytest.raises(cloud.CloudReconciliationRequired):
        await materializer.materialize(**args, runtime_checkpoint=AsyncMock())
