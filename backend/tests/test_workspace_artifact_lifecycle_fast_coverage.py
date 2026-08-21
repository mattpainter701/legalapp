from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import cloud_artifact_materialization as materialization
from app.services import document_accountability as accountability
from app.services.cloud_artifact_materialization import (
    CloudArtifactMaterializationError,
    CloudIntegrityError,
    CloudNotConfiguredError,
    CloudArtifactMaterializer,
    _configured_provider,
    _matter_folder_binding,
    _provider_datetime,
    canonical_docx_filename,
)
from app.services.generated_artifacts import (
    GeneratedArtifactError,
    create_initial_generated_artifact,
    _bounded_source_snapshot,
    _validated_variable_snapshot,
    canonical_artifact_request_sha256,
)
from app.services.matter_file_store import StorageResult


class _ExecuteResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _ArtifactDB:
    def __init__(self, scalar_values, claimed):
        self.values = iter(scalar_values)
        self.claimed = claimed
        self.added = []
        self.flushes = 0

    async def execute(self, _statement):
        return _ExecuteResult(self.claimed)

    async def scalar(self, _statement):
        return next(self.values)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushes += 1


def test_cloud_filename_and_datetime_cover_provider_safe_edges():
    artifact_id = uuid4()
    name = canonical_docx_filename("***", revision_no=0, artifact_id=artifact_id)
    assert name.endswith(f"-{str(artifact_id).replace('-', '')[:12]}-r1.docx")
    assert len(canonical_docx_filename("x" * 400)) <= 230
    assert _provider_datetime(None) is None
    assert _provider_datetime("not-a-date") is None
    assert _provider_datetime("2026-01-02T03:04:05Z").tzinfo is not None
    naive = _provider_datetime(datetime(2026, 1, 2))
    assert naive.tzinfo == timezone.utc


def test_cloud_provider_and_folder_binding_reject_incomplete_tenant_state():
    assert (
        _configured_provider(SimpleNamespace(primary_cloud_provider="google"))
        == "google_drive"
    )
    assert (
        _configured_provider(SimpleNamespace(primary_cloud_provider="share-point"))
        == "sharepoint"
    )
    with pytest.raises(CloudNotConfiguredError):
        _configured_provider(SimpleNamespace(primary_cloud_provider="box"))
    with pytest.raises(CloudNotConfiguredError):
        _matter_folder_binding({}, backend="google_drive")
    with pytest.raises(CloudNotConfiguredError):
        _matter_folder_binding({"google_drive": {}}, backend="google_drive")
    with pytest.raises(CloudNotConfiguredError):
        _matter_folder_binding(
            {"sharepoint": {"subfolders": {"documents": "docs"}}},
            backend="sharepoint",
        )
    binding = _matter_folder_binding(
        {"sharepoint": {"drive_id": "drive", "subfolders": {"uploads": "docs"}}},
        backend="sharepoint",
    )
    assert binding.parent_id == "docs" and binding.drive_id == "drive"


def test_source_and_variable_snapshots_are_bounded_and_normalized():
    sources = _bounded_source_snapshot(
        [
            {"source_id": "  authority-1 ", "sha256": "A" * 64, "label": " L "},
            {"source_id": ""},
            *({"source_id": f"source-{i}"} for i in range(20)),
        ]
    )
    assert sources[0] == {"source_id": "authority-1", "label": "L", "sha256": "a" * 64}
    assert len(sources) == 9
    assert _validated_variable_snapshot({"z": 1, "a": "two"}) == {"a": "two", "z": 1}
    with pytest.raises(GeneratedArtifactError, match="object"):
        _validated_variable_snapshot("bad")  # type: ignore[arg-type]
    with pytest.raises(GeneratedArtifactError, match="safe limit"):
        _validated_variable_snapshot({"x": "a" * 20_001})


@pytest.mark.asyncio
async def test_initial_artifact_creation_persists_hashes_and_provenance():
    tenant_id, matter_id, user_id, request_id = uuid4(), uuid4(), uuid4(), uuid4()
    artifact = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        client_request_id=request_id,
        request_sha256=None,
        current_revision_no=1,
    )
    request = {"matter_id": str(matter_id), "operation": "draft"}
    artifact.request_sha256 = canonical_artifact_request_sha256(
        {
            **request,
            "source_channel": "workspace_mcp",
            "source_snapshot": [{"source_id": "rag-1", "sha256": "b" * 64}],
            "template_id": None,
            "template_sha256": "c" * 64,
            "template_format": "docx",
            "variable_snapshot": {"client": "Example"},
            "unresolved_variables": ["hearing_date"],
        }
    )
    db = _ArtifactDB([artifact], artifact.id)
    result = await create_initial_generated_artifact(
        db,
        tenant_id=tenant_id,
        matter_id=matter_id,
        actor_user_id=user_id,
        conversation_id=None,
        title=" Motion ",
        kind=" pleading ",
        content_text="  Draft body  ",
        source_channel="workspace_mcp",
        client_request_id=request_id,
        request_payload=request,
        sources=[{"source_id": "rag-1", "sha256": "B" * 64}],
        template_sha256="C" * 64,
        template_format="DOCX",
        variable_snapshot={"client": "Example"},
        unresolved_variables=[" hearing_date", "hearing_date"],
    )
    assert result.created is True
    assert result.revision.content_text == "Draft body"
    assert (
        result.revision.content_sha256
        == __import__("hashlib").sha256(b"Draft body").hexdigest()
    )
    assert result.revision.template_format == "docx"
    assert result.revision.unresolved_variables == ["hearing_date"]
    assert db.flushes == 1 and len(db.added) == 1


@pytest.mark.asyncio
async def test_initial_artifact_creation_is_idempotent_and_rejects_conflicts():
    tenant_id, matter_id, user_id, request_id = uuid4(), uuid4(), uuid4(), uuid4()
    artifact = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        client_request_id=request_id,
        request_sha256="different",
        current_revision_no=1,
    )
    args = dict(
        tenant_id=tenant_id,
        matter_id=matter_id,
        actor_user_id=user_id,
        conversation_id=None,
        title="Draft",
        kind="memo",
        content_text="Body",
        source_channel="matter_chat",
        client_request_id=request_id,
        request_payload={"body": "Body"},
        sources=[],
    )
    with pytest.raises(GeneratedArtifactError) as conflict:
        await create_initial_generated_artifact(
            _ArtifactDB([artifact], artifact.id), **args
        )
    assert conflict.value.code == "idempotency_conflict"
    revision = SimpleNamespace(revision_no=1)
    artifact.request_sha256 = None
    artifact.request_sha256 = __import__(
        "app.services.generated_artifacts",
        fromlist=["canonical_artifact_request_sha256"],
    ).canonical_artifact_request_sha256(
        {
            "body": "Body",
            "source_channel": "matter_chat",
            "source_snapshot": [],
            "template_id": None,
            "template_sha256": None,
            "template_format": None,
            "variable_snapshot": {},
            "unresolved_variables": [],
        }
    )
    reused = await create_initial_generated_artifact(
        _ArtifactDB([artifact, revision], None), **args
    )
    assert reused.created is False and reused.revision is revision


def test_accountability_metadata_redacts_shape_and_preserves_canonical_hash():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clean = accountability.bounded_integrity_metadata(
        {"when": now, "id": uuid4(), "nested": {"ok": True}, "items": (1, 2)}
    )
    assert clean["when"].endswith("+00:00") and clean["items"] == [1, 2]
    assert accountability.integrity_event_sha256(
        {"b": 2, "a": 1}
    ) == accountability.integrity_event_sha256({"a": 1, "b": 2})
    for bad in (
        {"authorization": "x"},
        {"items": list(range(51))},
        {"nested": {"a": {"b": {"c": {"d": {"e": 1}}}}}},
    ):
        with pytest.raises(accountability.DocumentAccountabilityError):
            accountability.bounded_integrity_metadata(bad)


class _AccountabilityDB:
    def __init__(self, previous=None, operation=None):
        self.previous = previous
        self.operation = operation
        self.added = []
        self.flushes = 0
        self.executed = []

    async def execute(self, statement, params=None):
        self.executed.append((statement, params))

    async def scalar(self, statement):
        return self.previous if self.operation is None else self.operation

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushes += 1


@pytest.mark.asyncio
async def test_integrity_event_chains_and_storage_operation_reuses_only_exact_binding():
    tenant_id = uuid4()
    previous = SimpleNamespace(event_hash="prev-hash", chain_position=4)
    db = _AccountabilityDB(previous=previous)
    event = await accountability.append_document_integrity_event(
        db,
        tenant_id=tenant_id,
        event_type="verified",
        actor_type="service",
        matter_id=uuid4(),
        content_sha256="a" * 64,
        metadata={"count": 2},
    )
    assert event.chain_position == 5 and event.prev_event_hash == "prev-hash"
    assert len(event.event_hash) == 64 and db.flushes == 1
    operation_db = _AccountabilityDB()
    operation = await accountability.ensure_document_storage_operation(
        operation_db,
        tenant_id=tenant_id,
        matter_id=uuid4(),
        task_id=uuid4(),
        artifact_id=uuid4(),
        artifact_revision_id=uuid4(),
        actor_user_id=uuid4(),
        content_sha256="b" * 64,
        content_size=10,
        target_provider="microsoft",
        target_backend="sharepoint",
        target_drive_id="drive",
        target_parent_id="parent",
    )
    assert operation.status == "planned" and operation.idempotency_key.startswith(
        "generated-artifact:"
    )
    assert len(db.added) == 1
    assert len(operation_db.added) == 1


@pytest.mark.asyncio
async def test_materializer_compensation_and_reuse_conflict_paths(monkeypatch):
    tenant_id, matter_id, task_id, artifact_id, revision_id = (
        uuid4() for _ in range(5)
    )
    store = SimpleNamespace(delete_stored_result=lambda **_: None)
    materializer = CloudArtifactMaterializer(
        file_store=store, provider_db_factory=lambda: None
    )
    storage = StorageResult(
        provider="google",
        backend="google_drive",
        storage_path="x",
        web_url="https://x",
        provider_item_id="item",
    )

    class _Context:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def execute(self, *_args, **_kwargs):
            return None

    async def delete(**_):
        return None

    store.delete_stored_result = delete
    materializer.provider_db_factory = _Context
    assert await materializer._compensate(tenant_id=tenant_id, storage=storage)
    assert not await materializer._compensate(
        tenant_id=tenant_id,
        storage=StorageResult(provider="google", backend="google_drive"),
    )
    artifact = SimpleNamespace(
        id=artifact_id, matter_id=matter_id, output_document_id=None
    )
    task = SimpleNamespace(id=task_id)
    operation = SimpleNamespace(
        id=uuid4(), status="provider_accepted", delivery_certainty="provider_accepted"
    )
    document = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        matter_id=matter_id,
        task_id=task_id,
        generated_artifact_id=artifact_id,
        generated_artifact_revision_id=revision_id,
        storage_backend="google_drive",
        provider_object_id="item",
        document_sha256="a" * 64,
        storage_state="verified",
        file_size=3,
        storage_provider="google",
        storage_path="x",
        cloud_url="https://x",
        provider_etag=None,
        provider_version_id=None,
        provider_checksum=None,
        provider_modified_at=None,
        provider_drive_id=None,
        provider_parent_id="parent",
    )

    async def changed_readback(**_):
        raise RuntimeError("cloud bytes changed")

    materializer._readback = changed_readback
    monkeypatch.setattr(
        materialization, "append_document_integrity_event", _async_event
    )
    with pytest.raises(CloudIntegrityError):
        await materializer._reuse_existing(
            db=_AccountabilityDB(),
            tenant_id=tenant_id,
            artifact=artifact,
            revision=SimpleNamespace(id=revision_id),
            operation=operation,
            document=document,
            expected_sha256="a" * 64,
            task=task,
        )
    assert document.storage_state == "conflict"


async def _async_bytes(value):
    return value


async def _async_event(*_args, **_kwargs):
    return None


@pytest.mark.asyncio
async def test_materializer_rejects_missing_revision_before_cloud_access():
    class DB:
        async def scalar(self, _statement):
            return None

    with pytest.raises(CloudArtifactMaterializationError):
        await CloudArtifactMaterializer().materialize(
            db=DB(),
            tenant_id=uuid4(),
            artifact_id=uuid4(),
            revision_id=uuid4(),
            task_id=uuid4(),
            uploaded_by_user_id=uuid4(),
        )
