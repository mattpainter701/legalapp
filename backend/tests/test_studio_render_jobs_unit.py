"""Database-free state-machine and lease-fence checks for Studio jobs."""

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import app.config as config_module
from app.config import validate_studio_render_paths
from app.models.durable_job import DurableJob
from app.models.studio_render import (
    StudioPreferredRenderEvidence,
    StudioRenderArtifact,
)
from app.schemas.studio_render import (
    StudioGeometryManifest,
    StudioPageGeometry,
    StudioRendererComponent,
    StudioRendererManifest,
    StudioRenderRequest,
    StudioRenderOptions,
    StudioRenderSourceContract,
    canonical_effective_render_request_hash,
    canonical_render_request_hash,
)
from app.services.studio_render_jobs import (
    StudioJobLease,
    StudioRenderJobService,
    StudioRenderWorkerService,
    _geometry_matches_request,
    _can_become_preferred_evidence,
    _evidence_basis_sha256,
    StudioRenderServiceError,
    _StudioRenderJobStore,
    _QueuedPayload,
    _parse_queued,
    _parse_result,
    _render_cache_key,
    _transition,
    sanitized_failure,
)
from app.services.studio_render_runtime import (
    StudioRenderRuntimeError,
    _prepare_workspace,
)


def _manifest():
    def component(name, digest):
        return StudioRendererComponent(
            name=name, version="1.0.0", content_sha256=digest * 64
        )
    return StudioRendererManifest(
        isolation_policy_id="studio-test-v1",
        launcher_sha256="1" * 64,
        sandbox_policy_sha256="9" * 64,
        fixed_arguments_sha256="2" * 64,
        environment_sha256="3" * 64,
        runtime_bundle_sha256="0" * 64,
        font_pack_sha256="4" * 64,
        renderer=component("renderer", "5"),
        rasterizer=component("rasterizer", "6"),
        converter=component("converter", "7"),
        validator=component("validator", "8"),
    )


def _lease():
    tenant_id = uuid.uuid4()
    job_id = uuid.uuid4()
    owner = "studio-worker-a"
    token = uuid.uuid4()
    source = StudioRenderSourceContract(
        artifact_id=uuid.uuid4(),
        sha256="a" * 64,
        media_type="text/markdown",
        format="markdown",
    )
    options = StudioRenderOptions()
    manifest = _manifest()
    values = {
        "kind": "studio_test_render",
        "draft_id": uuid.uuid4(),
        "rendered_revision": 1,
        "identity_sha256": "b" * 64,
        "snapshot_id": uuid.uuid4(),
        "snapshot_content_sha256": "c" * 64,
        "source": source,
        "render_options": options,
        "render_options_sha256": options.sha256,
        "requested_by": uuid.uuid4(),
        "input_binding_id": None,
        "input_binding_sha256": None,
        "input_binding_version": None,
        "renderer_manifest": manifest,
        "runtime_manifest_sha256": manifest.sha256,
        "admission_bytes": options.max_output_bytes + 100,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "lease_token": token,
        "lease_duration_seconds": 900,
        "lease_expires_at": datetime.now(timezone.utc) + timedelta(minutes=15),
    }
    values["request_sha256"] = canonical_render_request_hash(
        kind=values["kind"],
        draft_id=values["draft_id"],
        expected_revision=values["rendered_revision"],
        identity_sha256=values["identity_sha256"],
        snapshot_id=values["snapshot_id"],
        content_sha256=values["snapshot_content_sha256"],
        source=source,
        render_options=options,
        requested_by=values["requested_by"],
        input_binding_id=None,
    )
    values["effective_request_sha256"] = canonical_effective_render_request_hash(
        request_sha256=values["request_sha256"],
        input_binding_sha256=None,
        input_binding_version=None,
    )
    values["cache_key"] = _render_cache_key(
        kind=values["kind"],
        draft_id=values["draft_id"],
        rendered_revision=values["rendered_revision"],
        identity_sha256=values["identity_sha256"],
        snapshot_id=values["snapshot_id"],
        snapshot_content_sha256=values["snapshot_content_sha256"],
        source=source,
        render_options_sha256=values["render_options_sha256"],
        effective_request_sha256=values["effective_request_sha256"],
        input_binding_id=None,
        input_binding_sha256=None,
        input_binding_version=None,
        runtime_manifest_sha256=manifest.sha256,
    )
    payload = _QueuedPayload(**values)
    lease = StudioJobLease(
        job_id=job_id,
        tenant_id=tenant_id,
        owner=owner,
        token=token,
        attempt=2,
        payload=payload,
    )
    row = DurableJob(
        id=job_id,
        tenant_id=tenant_id,
        kind=payload.kind,
        idempotency_key="studio-render:" + "e" * 64,
        payload=payload.model_dump(mode="json"),
        status="running",
        attempts=2,
        lease_owner=owner,
        leased_at=datetime.now(timezone.utc),
    )
    return row, lease


def test_same_owner_cannot_cross_attempt_fence():
    row, lease = _lease()
    assert StudioRenderWorkerService._owns(row, lease)
    assert not StudioRenderWorkerService._owns(
        row, replace(lease, token=uuid.uuid4())
    )
    assert not StudioRenderWorkerService._owns(row, replace(lease, attempt=1))
    row.payload = {**row.payload, "lease_token": str(uuid.uuid4())}
    assert not StudioRenderWorkerService._owns(row, lease)


def test_owned_lease_cannot_mutate_after_database_expiry():
    row, lease = _lease()
    now = datetime.now(timezone.utc)
    row.payload = {
        **row.payload,
        "lease_expires_at": (now + timedelta(seconds=1)).isoformat(),
    }
    assert StudioRenderWorkerService._owns_live(row, lease, now=now)
    row.payload = {
        **row.payload,
        "lease_expires_at": (now - timedelta(seconds=1)).isoformat(),
    }
    assert not StudioRenderWorkerService._owns_live(row, lease, now=now)


def test_explicit_transition_table_rejects_terminal_or_skipped_edges():
    row, _ = _lease()
    now = datetime.now(timezone.utc)
    with pytest.raises(StudioRenderServiceError) as skipped:
        _transition(row, "cancelled", now=now)
    assert skipped.value.code == "invalid_job_transition"
    _transition(row, "cancel_requested", now=now)
    _transition(row, "completed", now=now)
    assert row.completed_at == now
    assert row.lease_owner is None
    with pytest.raises(StudioRenderServiceError):
        _transition(row, "running", now=now)


def test_queue_payload_is_reference_only_and_bounded():
    row, _ = _lease()
    durable = str(row.payload).lower()
    for forbidden in (
        "document_text",
        "raw_text",
        "provider_id",
        "signed_url",
        "storage_path",
        "exception",
    ):
        assert forbidden not in durable
    assert "object_key" not in durable


def test_cache_contract_binds_runtime_manifest_and_frozen_input_identity():
    row, lease = _lease()
    effective_tampered = {
        **row.payload,
        "effective_request_sha256": "0" * 64,
    }
    with pytest.raises(Exception, match="effective request hash mismatch"):
        _QueuedPayload.model_validate(effective_tampered)
    tampered = {**row.payload, "runtime_manifest_sha256": "0" * 64}
    with pytest.raises(Exception, match="manifest hash mismatch|cache key mismatch"):
        _QueuedPayload.model_validate(tampered)

    bound = {
        **lease.payload.model_dump(mode="python"),
        "input_binding_id": uuid.uuid4(),
        "input_binding_sha256": None,
        "input_binding_version": None,
    }
    with pytest.raises(Exception, match="input binding identity"):
        _QueuedPayload.model_validate(bound)
    partially_bound = {
        **lease.payload.model_dump(mode="python"),
        "input_binding_id": uuid.uuid4(),
        "input_binding_sha256": "f" * 64,
        "input_binding_version": None,
    }
    with pytest.raises(Exception, match="input binding identity"):
        _QueuedPayload.model_validate(partially_bound)

    substituted = {
        **lease.payload.model_dump(mode="python"),
        "draft_id": uuid.uuid4(),
    }
    with pytest.raises(Exception, match="request hash mismatch"):
        _QueuedPayload.model_validate(substituted)


def test_unknown_failure_never_echoes_exception_text():
    code, message = sanitized_failure(
        "database password secret-value at C:/private/source.docx"
    )
    assert code == "processor_unavailable"
    assert message == "Studio processing is temporarily unavailable."
    assert "secret-value" not in message


def test_malformed_persisted_payload_and_result_terminalize_safely():
    row, _ = _lease()
    now = datetime.now(timezone.utc)
    row.payload = {"raw_document": "provider secret C:/private"}
    assert _parse_queued(row, now=now) is None
    assert row.status == "failed"
    assert row.result == {"error_code": "job_data_unavailable"}
    assert row.payload == {}
    assert "provider secret" not in row.last_error

    row, _ = _lease()
    row.result = {"error_code": "raw database exception C:/private"}
    assert _parse_result(row, now=now) is None
    assert row.status == "failed"
    assert row.result == {"error_code": "job_data_unavailable"}

    row, _ = _lease()
    row.result = {"geometry_manifest_sha256": "f" * 64}
    assert _parse_result(row, now=now) is None
    assert row.status == "failed"
    assert row.result == {"error_code": "job_data_unavailable"}


def test_consumer_and_worker_facades_do_not_cross_expose_operations():
    consumer = StudioRenderJobService(
        object(), tenant_id=uuid.uuid4(), actor_user_id=uuid.uuid4()
    )
    worker = StudioRenderWorkerService(object(), tenant_id=uuid.uuid4())
    assert not hasattr(consumer, "claim")
    assert not hasattr(consumer, "adopt_output")
    assert not hasattr(worker, "enqueue")
    assert not hasattr(worker, "status")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["status", "request_cancel"])
async def test_other_actor_cannot_probe_or_mutate_corrupt_same_tenant_job(operation):
    row, lease = _lease()
    row.payload = {**row.payload, "render_options_sha256": "0" * 64}
    original_payload = dict(row.payload)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=row)
    store = _StudioRenderJobStore(
        db,
        tenant_id=lease.tenant_id,
        actor_user_id=uuid.uuid4(),
    )
    with patch(
        "app.services.studio_render_jobs.set_tenant_context", AsyncMock()
    ):
        with pytest.raises(StudioRenderServiceError) as caught:
            await getattr(store, operation)(row.id)
    assert caught.value.status_code == 404
    assert caught.value.code == "job_not_found"
    assert row.payload == original_payload
    assert row.status == "running"
    assert row.result is None
    db.flush.assert_not_awaited()


def test_artifact_model_uses_available_tenant_composite_references():
    names = {
        constraint.name
        for constraint in StudioRenderArtifact.__table__.constraints
        if constraint.name
    }
    assert {
        "fk_studio_render_artifact_draft_tenant",
        "fk_studio_render_artifact_source_contract",
        "fk_studio_render_artifact_requester_tenant",
    }.issubset(names)
    assert StudioRenderArtifact.__table__.c.requested_by_user_id.nullable is False
    retention = next(
        constraint
        for constraint in StudioRenderArtifact.__table__.constraints
        if constraint.name == "ck_studio_render_artifact_temporary_expiry"
    )
    retention_sql = str(retention.sqltext)
    assert "retention_class = 'evidence' AND content_expires_at IS NULL" in retention_sql
    assert "metadata_expires_at IS NULL" in retention_sql
    assert "retention_class IN ('ephemeral', 'review')" in retention_sql
    assert "metadata_expires_at > content_expires_at" in retention_sql
    columns = StudioRenderArtifact.__table__.c
    assert columns.render_options.nullable is False
    assert columns.effective_request_sha256.nullable is False
    assert columns.geometry_manifest.nullable is False
    assert columns.artifact_page_count.nullable is False
    assert columns.document_page_count.nullable is False
    preferred_names = {
        constraint.name
        for constraint in StudioPreferredRenderEvidence.__table__.constraints
        if constraint.name
    }
    assert {
        "fk_studio_preferred_render_draft_tenant",
        "fk_studio_preferred_render_exact_evidence",
        "uq_studio_preferred_render_artifact",
        "ck_studio_preferred_render_basis",
    }.issubset(preferred_names)


def test_preferred_evidence_basis_is_server_owned_and_bounded():
    first = _lease()[1].payload
    preview = first.model_copy(
        update={
            "kind": "studio_page_preview",
            "render_options": StudioRenderOptions(page_number=1),
        }
    )
    second_page = preview.model_copy(
        update={"render_options": StudioRenderOptions(page_number=2)}
    )
    quota_variant = first.model_copy(
        update={
            "render_options": first.render_options.model_copy(
                update={
                    "max_output_bytes": first.render_options.max_output_bytes + 1,
                    "max_pages": first.render_options.max_pages + 1,
                    "preview_purpose": "editor",
                }
            )
        }
    )
    assert _evidence_basis_sha256(first) != _evidence_basis_sha256(preview)
    assert _evidence_basis_sha256(preview) != _evidence_basis_sha256(second_page)
    assert _evidence_basis_sha256(first) == _evidence_basis_sha256(quota_variant)
    assert _can_become_preferred_evidence(first)
    assert not _can_become_preferred_evidence(preview)


def test_retained_evidence_quota_configuration_is_bounded():
    with pytest.raises(ValueError, match="retained_artifact_limit"):
        _StudioRenderJobStore(
            object(), tenant_id=uuid.uuid4(), retained_artifact_limit=0
        )
    with pytest.raises(ValueError, match="retained_byte_limit"):
        _StudioRenderJobStore(
            object(), tenant_id=uuid.uuid4(), retained_byte_limit=0
        )


def _studio_path_settings(tmp_path: Path, **overrides):
    values = {
        "TEMPLATE_STUDIO_RENDER_STORAGE_DIR": str(tmp_path / "cas"),
        "TEMPLATE_STUDIO_RENDER_WORKSPACE_DIR": str(tmp_path / "workspace"),
        "UPLOAD_DIR": str(tmp_path / "uploads"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_studio_render_paths_are_canonical_and_disjoint(tmp_path):
    settings = _studio_path_settings(tmp_path)
    storage, workspace = validate_studio_render_paths(
        settings, require_workspace=True
    )
    assert storage == (tmp_path / "cas").resolve()
    assert workspace == (tmp_path / "workspace").resolve()


@pytest.mark.parametrize(
    ("storage_suffix", "workspace_suffix"),
    [
        ("same", "same"),
        ("nested", "nested/child"),
        ("nested/child", "nested"),
    ],
)
def test_studio_render_paths_reject_equality_and_nesting(
    tmp_path, storage_suffix, workspace_suffix
):
    settings = _studio_path_settings(
        tmp_path,
        TEMPLATE_STUDIO_RENDER_STORAGE_DIR=str(tmp_path / storage_suffix),
        TEMPLATE_STUDIO_RENDER_WORKSPACE_DIR=str(tmp_path / workspace_suffix),
    )
    with pytest.raises(ValueError, match="must be disjoint"):
        validate_studio_render_paths(settings, require_workspace=True)


def test_studio_render_paths_reject_root_upload_and_application_paths(tmp_path):
    root = Path(tmp_path.anchor)
    with pytest.raises(ValueError, match="filesystem root"):
        validate_studio_render_paths(
            _studio_path_settings(
                tmp_path, TEMPLATE_STUDIO_RENDER_WORKSPACE_DIR=str(root)
            ),
            require_workspace=True,
        )
    with pytest.raises(ValueError, match="UPLOAD_DIR"):
        validate_studio_render_paths(
            _studio_path_settings(
                tmp_path,
                TEMPLATE_STUDIO_RENDER_WORKSPACE_DIR=str(tmp_path / "uploads" / "tmp"),
            ),
            require_workspace=True,
        )
    app_root = Path(config_module.__file__).resolve().parents[1]
    with pytest.raises(ValueError, match="application root"):
        validate_studio_render_paths(
            _studio_path_settings(
                tmp_path,
                TEMPLATE_STUDIO_RENDER_WORKSPACE_DIR=str(app_root / "app"),
            ),
            require_workspace=True,
        )


def test_prepare_workspace_cleans_only_a_safe_dedicated_root(tmp_path):
    settings = _studio_path_settings(tmp_path)
    workspace = tmp_path / "workspace"
    nested = workspace / "old-job"
    nested.mkdir(parents=True)
    (nested / "result.bin").write_bytes(b"old")

    prepared = _prepare_workspace(settings)

    assert prepared == workspace.resolve()
    assert list(prepared.iterdir()) == []


def test_prepare_workspace_rechecks_relationship_before_cleanup(tmp_path):
    settings = _studio_path_settings(
        tmp_path,
        TEMPLATE_STUDIO_RENDER_WORKSPACE_DIR=str(tmp_path / "uploads"),
    )
    protected = tmp_path / "uploads" / "keep.bin"
    protected.parent.mkdir(parents=True)
    protected.write_bytes(b"keep")

    with pytest.raises(StudioRenderRuntimeError):
        _prepare_workspace(settings)
    assert protected.read_bytes() == b"keep"


def test_durable_job_studio_partial_indexes_match_migration_contract():
    indexes = {index.name: index for index in DurableJob.__table__.indexes}
    idempotency = indexes["uq_durable_jobs_studio_idempotency"]
    claim = indexes["ix_durable_jobs_studio_claim"]
    assert idempotency.unique is True
    assert [column.name for column in idempotency.columns] == [
        "tenant_id",
        "idempotency_key",
    ]
    assert [column.name for column in claim.columns] == [
        "tenant_id",
        "status",
        "available_at",
        "created_at",
    ]
    idempotency_where = str(idempotency.dialect_options["postgresql"]["where"])
    claim_where = str(claim.dialect_options["postgresql"]["where"])
    for kind in (
        "studio_template_analysis",
        "studio_template_ocr",
        "studio_page_preview",
        "studio_test_render",
    ):
        assert kind in idempotency_where
        assert kind in claim_where
    assert "cancel_requested" in claim_where


@pytest.mark.asyncio
async def test_admission_rejects_unsupported_capability_before_database_access():
    queued = _lease()[1].payload
    request = StudioRenderRequest(
        kind=queued.kind,
        draft_id=queued.draft_id,
        expected_revision=queued.rendered_revision,
        identity_sha256=queued.identity_sha256,
        snapshot_id=queued.snapshot_id,
        content_sha256=queued.snapshot_content_sha256,
        source=queued.source,
        render_options=queued.render_options,
        requested_by=queued.requested_by,
        input_binding_id=queued.input_binding_id,
        request_sha256=queued.request_sha256,
    )
    service = StudioRenderJobService(
        object(),
        tenant_id=uuid.uuid4(),
        actor_user_id=queued.requested_by,
        renderer_manifests={(queued.kind, "docx", 1): queued.renderer_manifest},
    )

    async def audit(_event, _job_id):
        return None

    with pytest.raises(StudioRenderServiceError) as caught:
        await service.enqueue(request, idempotency_key="unsupported-123", audit=audit)
    assert caught.value.code == "processor_unavailable"


@pytest.mark.asyncio
async def test_admission_rejects_output_that_cannot_be_downloaded():
    queued = _lease()[1].payload
    request = StudioRenderRequest(
        kind=queued.kind,
        draft_id=queued.draft_id,
        expected_revision=queued.rendered_revision,
        identity_sha256=queued.identity_sha256,
        snapshot_id=queued.snapshot_id,
        content_sha256=queued.snapshot_content_sha256,
        source=queued.source,
        render_options=queued.render_options,
        requested_by=queued.requested_by,
        input_binding_id=queued.input_binding_id,
        request_sha256=queued.request_sha256,
    )
    service = StudioRenderJobService(
        object(),
        tenant_id=uuid.uuid4(),
        actor_user_id=queued.requested_by,
        renderer_manifest=queued.renderer_manifest,
        max_download_bytes=queued.render_options.max_output_bytes - 1,
    )

    with pytest.raises(StudioRenderServiceError) as caught:
        await service.enqueue(
            request,
            idempotency_key="undownloadable-output",
            audit=lambda *_args: None,
        )
    assert caught.value.code == "output_too_large"


def test_adoption_geometry_coverage_rechecks_preview_and_full_document():
    preview = StudioGeometryManifest(
        artifact_page_count=1,
        document_page_count=3,
        pages=[
            StudioPageGeometry(
                page_number=2,
                coordinate_space="pixels",
                width_px=20,
                height_px=30,
                dpi_x=150,
                dpi_y=150,
            )
        ],
    )
    assert _geometry_matches_request(
        preview,
        artifact_kind="page_preview",
        requested_page_number=2,
    )
    assert not _geometry_matches_request(
        preview,
        artifact_kind="page_preview",
        requested_page_number=1,
    )
    full = StudioGeometryManifest(
        artifact_page_count=2,
        document_page_count=2,
        pages=[
            StudioPageGeometry(page_number=1, coordinate_space="none"),
            StudioPageGeometry(page_number=2, coordinate_space="none"),
        ],
    )
    assert _geometry_matches_request(
        full,
        artifact_kind="analysis",
        requested_page_number=None,
    )
    assert not _geometry_matches_request(
        full,
        artifact_kind="analysis",
        requested_page_number=1,
    )
