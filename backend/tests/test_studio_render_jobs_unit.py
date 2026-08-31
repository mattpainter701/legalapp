"""Database-free state-machine and lease-fence checks for Studio jobs."""

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.models.durable_job import DurableJob
from app.schemas.studio_render import StudioRenderOptions, StudioRenderSourceContract
from app.services.studio_render_jobs import (
    StudioJobLease,
    StudioRenderJobService,
    StudioRenderServiceError,
    _QueuedPayload,
    _render_cache_key,
    _transition,
    sanitized_failure,
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
        "request_sha256": "d" * 64,
        "requested_by": uuid.uuid4(),
        "input_binding_id": None,
        "renderer_identity": "renderer-v1",
        "converter_identity": "converter-v1",
        "validator_identity": "validator-v1",
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "lease_token": token,
    }
    values["cache_key"] = _render_cache_key(
        kind=values["kind"],
        draft_id=values["draft_id"],
        rendered_revision=values["rendered_revision"],
        identity_sha256=values["identity_sha256"],
        snapshot_id=values["snapshot_id"],
        snapshot_content_sha256=values["snapshot_content_sha256"],
        source=source,
        render_options_sha256=values["render_options_sha256"],
        input_binding_id=None,
        renderer_identity=values["renderer_identity"],
        converter_identity=values["converter_identity"],
        validator_identity=values["validator_identity"],
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
    assert StudioRenderJobService._owns(row, lease)
    assert not StudioRenderJobService._owns(
        row, replace(lease, token=uuid.uuid4())
    )
    assert not StudioRenderJobService._owns(row, replace(lease, attempt=1))
    row.payload = {**row.payload, "lease_token": str(uuid.uuid4())}
    assert not StudioRenderJobService._owns(row, lease)


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


def test_unknown_failure_never_echoes_exception_text():
    code, message = sanitized_failure(
        "database password secret-value at C:/private/source.docx"
    )
    assert code == "processor_unavailable"
    assert message == "Studio processing is temporarily unavailable."
    assert "secret-value" not in message

