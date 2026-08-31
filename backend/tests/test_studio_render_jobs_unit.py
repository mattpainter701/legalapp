"""Database-free state-machine and lease-fence checks for Studio jobs."""

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.models.durable_job import DurableJob
from app.models.studio_render import StudioRenderArtifact
from app.schemas.studio_render import (
    StudioRendererComponent,
    StudioRendererManifest,
    StudioRenderOptions,
    StudioRenderSourceContract,
    canonical_effective_render_request_hash,
    canonical_render_request_hash,
)
from app.services.studio_render_jobs import (
    StudioJobLease,
    StudioRenderJobService,
    StudioRenderWorkerService,
    StudioRenderServiceError,
    _QueuedPayload,
    _parse_queued,
    _parse_result,
    _render_cache_key,
    _transition,
    sanitized_failure,
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
    row.result = {"mapping_manifest_sha256": "f" * 64}
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
    assert "retention_class = 'evidence' AND expires_at IS NULL" in retention_sql
    assert "retention_class IN ('ephemeral', 'review')" in retention_sql
