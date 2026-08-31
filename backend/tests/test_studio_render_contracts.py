"""Database-free checks for the Phase 3 public render contract."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.studio_render import (
    StudioRendererComponent,
    StudioRendererManifest,
    StudioRenderAccepted,
    StudioRenderErrorDetails,
    StudioRenderJobStatus,
    StudioRenderOptions,
    StudioRenderRequest,
    StudioRenderSourceContract,
    canonical_render_request_hash,
)


def _manifest():
    def component(name, value):
        return StudioRendererComponent(
            name=name, version="1.0.0", content_sha256=value * 64
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


def _request_payload(**updates):
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
        "expected_revision": 3,
        "identity_sha256": "b" * 64,
        "snapshot_id": uuid.uuid4(),
        "content_sha256": "c" * 64,
        "source": source,
        "render_options": options,
        "requested_by": uuid.uuid4(),
        "input_binding_id": None,
    }
    values.update(updates)
    values["request_sha256"] = canonical_render_request_hash(
        kind=values["kind"],
        draft_id=values["draft_id"],
        expected_revision=values["expected_revision"],
        identity_sha256=values["identity_sha256"],
        snapshot_id=values["snapshot_id"],
        content_sha256=values["content_sha256"],
        source=values["source"],
        render_options=values["render_options"],
        requested_by=values["requested_by"],
        input_binding_id=values["input_binding_id"],
    )
    return values


def test_request_hash_is_canonical_and_tamper_evident():
    request = StudioRenderRequest.model_validate(_request_payload())
    tampered = request.model_dump()
    tampered["render_options"]["flatten_pdf"] = True
    with pytest.raises(ValidationError, match="request hash mismatch"):
        StudioRenderRequest.model_validate(tampered)


@pytest.mark.parametrize(
    "forbidden",
    [
        {"values": {"client_name": "privileged value"}},
        {"document_text": "secret"},
        {"provider_id": "drive-item"},
        {"signed_url": "https://example.invalid/signed"},
        {"storage_path": "C:/private/source.docx"},
    ],
)
def test_render_options_reject_raw_or_provider_payloads(forbidden):
    with pytest.raises(ValidationError):
        StudioRenderOptions.model_validate(forbidden)


def test_kind_specific_options_and_binding_are_bounded():
    preview = _request_payload(
        kind="studio_page_preview",
        render_options=StudioRenderOptions(page_number=2),
    )
    assert StudioRenderRequest.model_validate(preview).render_options.page_number == 2
    with pytest.raises(ValidationError, match="require page_number"):
        StudioRenderRequest.model_validate(
            _request_payload(kind="studio_page_preview")
        )
    with pytest.raises(ValidationError, match="only for test renders"):
        StudioRenderRequest.model_validate(
            _request_payload(
                kind="studio_template_ocr", input_binding_id=uuid.uuid4()
            )
        )


def test_status_exposes_artifact_only_after_materialization():
    request = StudioRenderRequest.model_validate(_request_payload())
    now = datetime.now(timezone.utc)
    manifest = _manifest()
    job_id = uuid.uuid4()
    common = {
        "job_id": job_id,
        "status_url": f"/api/template-studio/render-jobs/{job_id}",
        "kind": request.kind,
        "progress": 100,
        "attempts": 1,
        "max_attempts": 5,
        "created_at": now,
        "updated_at": now,
        "draft_id": request.draft_id,
        "rendered_revision": request.expected_revision,
        "identity_sha256": request.identity_sha256,
        "snapshot_id": request.snapshot_id,
        "snapshot_content_sha256": request.content_sha256,
        "source": request.source,
        "render_options": request.render_options,
        "render_options_sha256": request.render_options.sha256,
        "request_sha256": request.request_sha256,
        "renderer_manifest": manifest,
        "runtime_manifest_sha256": manifest.sha256,
        "job_expires_at": now + timedelta(hours=1),
    }
    with pytest.raises(ValidationError, match="only after materialization"):
        StudioRenderJobStatus(
            **{**common, "progress": 10},
            state="running",
            leased_at=now,
            artifact_id=uuid.uuid4(),
            adoption_outcome="current_evidence",
            artifact_expires_at=now + timedelta(hours=1),
            result_url="/api/template-studio/render-artifacts/123",
            download_url="/api/template-studio/render-artifacts/123/content",
            adopted_as_current_evidence=True,
            output_sha256="9" * 64,
            output_media_type="application/pdf",
            output_byte_size=100,
            page_count=1,
            retention_class="review",
        )
    artifact_id = uuid.uuid4()
    completed = StudioRenderJobStatus(
        **common,
        state="completed",
        completed_at=now,
        artifact_id=artifact_id,
        result_url=f"/api/template-studio/render-artifacts/{artifact_id}",
        download_url=f"/api/template-studio/render-artifacts/{artifact_id}/content",
        adoption_outcome="stale_output",
        adopted_as_current_evidence=False,
        is_current_evidence=False,
        output_sha256="9" * 64,
        output_media_type="application/pdf",
        output_byte_size=100,
        page_count=1,
        retention_class="review",
        artifact_expires_at=now + timedelta(hours=1),
    )
    public_json = completed.model_dump_json()
    for forbidden in (
        "object_key",
        "storage_path",
        "provider_id",
        "signed_url",
        "exception",
    ):
        assert forbidden not in public_json


@pytest.mark.parametrize(
    "unsafe",
    [
        "/api//template-studio/render-jobs/id",
        "/api/template-studio/../private",
        "https://example.invalid/status",
    ],
)
def test_advertised_resources_are_canonical_relative_paths(unsafe):
    with pytest.raises(ValidationError):
        StudioRenderAccepted(
            job_id=uuid.uuid4(),
            status_url=unsafe,
            job_expires_at=datetime.now(timezone.utc),
        )


def test_public_expiry_requires_an_absolute_instant():
    with pytest.raises(ValidationError):
        StudioRenderAccepted(
            job_id=uuid.uuid4(),
            status_url="/api/template-studio/render-jobs/time-test",
            job_expires_at=datetime.now(),
        )


def test_failed_status_requires_sanitized_shape():
    request = StudioRenderRequest.model_validate(_request_payload())
    now = datetime.now(timezone.utc)
    manifest = _manifest()
    job_id = uuid.uuid4()
    common = {
        "job_id": job_id,
        "status_url": f"/api/template-studio/render-jobs/{job_id}",
        "kind": request.kind,
        "state": "failed",
        "progress": 100,
        "attempts": 1,
        "max_attempts": 5,
        "created_at": now,
        "updated_at": now,
        "completed_at": now,
        "draft_id": request.draft_id,
        "rendered_revision": request.expected_revision,
        "identity_sha256": request.identity_sha256,
        "snapshot_id": request.snapshot_id,
        "snapshot_content_sha256": request.content_sha256,
        "source": request.source,
        "render_options": request.render_options,
        "render_options_sha256": request.render_options.sha256,
        "request_sha256": request.request_sha256,
        "renderer_manifest": manifest,
        "runtime_manifest_sha256": manifest.sha256,
        "job_expires_at": now,
    }
    with pytest.raises(ValidationError, match="sanitized failure"):
        StudioRenderJobStatus(**common)
    with pytest.raises(ValidationError, match="canonical sanitized message"):
        StudioRenderJobStatus(
            **common,
            error_code="processor_unavailable",
            error_message="raw exception: C:/private/source.docx",
        )


def test_phase_two_etag_contract_is_full_and_exact():
    draft_id = uuid.uuid4()
    value = f'"studio:{draft_id}:12:{"a" * 64}"'
    assert StudioRenderErrorDetails(
        current_revision=12, current_etag=value
    ).current_etag == value
    with pytest.raises(ValidationError):
        StudioRenderErrorDetails(
            current_revision=12,
            current_etag=f'W/"studio-draft-12-{"a" * 16}"',
        )


@pytest.mark.parametrize(
    ("state", "valid_timestamps", "invalid_timestamps"),
    [
        ("pending", {}, {"completed_at": "now"}),
        ("running", {"leased_at": "now"}, {}),
        ("retry_wait", {"retry_at": "future"}, {}),
        ("cancel_requested", {"leased_at": "now"}, {}),
        ("cancelled", {"completed_at": "now"}, {}),
    ],
)
def test_non_result_status_states_have_exact_timestamp_shapes(
    state, valid_timestamps, invalid_timestamps
):
    request = StudioRenderRequest.model_validate(_request_payload())
    now = datetime.now(timezone.utc)
    manifest = _manifest()
    values = {
        "job_id": uuid.uuid4(),
        "status_url": "/api/template-studio/render-jobs/state-test",
        "kind": request.kind,
        "state": state,
        "progress": 100 if state == "cancelled" else 0,
        "attempts": 1,
        "max_attempts": 5,
        "created_at": now,
        "updated_at": now,
        "draft_id": request.draft_id,
        "rendered_revision": request.expected_revision,
        "identity_sha256": request.identity_sha256,
        "snapshot_id": request.snapshot_id,
        "snapshot_content_sha256": request.content_sha256,
        "source": request.source,
        "render_options": request.render_options,
        "render_options_sha256": request.render_options.sha256,
        "request_sha256": request.request_sha256,
        "renderer_manifest": manifest,
        "runtime_manifest_sha256": manifest.sha256,
        "job_expires_at": now + timedelta(hours=1),
    }
    for key, marker in valid_timestamps.items():
        values[key] = now + timedelta(seconds=1) if marker == "future" else now
    assert StudioRenderJobStatus(**values).state == state

    broken = dict(values)
    if invalid_timestamps:
        for key in invalid_timestamps:
            broken[key] = now
    elif state in {"running", "cancel_requested"}:
        broken["leased_at"] = None
    elif state == "retry_wait":
        broken["retry_at"] = None
    else:
        broken["leased_at"] = now
    with pytest.raises(ValidationError):
        StudioRenderJobStatus(**broken)
