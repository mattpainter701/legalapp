"""Database-free checks for the Phase 3 public render contract."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.studio_render import (
    STUDIO_PUBLIC_ERROR_MESSAGES,
    STUDIO_PUBLIC_ERROR_RETRYABLE,
    STUDIO_PUBLIC_ERROR_STATUS,
    StudioArtifactGeometry,
    StudioGeometryManifest,
    StudioPageGeometry,
    StudioRendererComponent,
    StudioRendererManifest,
    StudioRenderAccepted,
    StudioRenderCapabilities,
    StudioRenderCapability,
    StudioRenderErrorDetails,
    StudioRenderJobStatus,
    StudioRenderIntent,
    StudioRenderOptions,
    StudioRenderPublicError,
    StudioRenderRequest,
    StudioRenderSourceContract,
    canonical_effective_render_request_hash,
    canonical_render_request_hash,
)
from app.services.studio_render_jobs import (
    StudioRenderServiceError,
    studio_render_public_error,
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
        runtime_bundle_sha256="0" * 64,
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


def _effective_request_sha256(request):
    return canonical_effective_render_request_hash(
        request_sha256=request.request_sha256,
        input_binding_sha256=None,
        input_binding_version=None,
    )


def _geometry(page_count=1):
    return StudioGeometryManifest(
        artifact_page_count=page_count,
        document_page_count=page_count,
        pages=[
            StudioPageGeometry(
                page_number=page,
                coordinate_space="points",
                width_points=612,
                height_points=792,
            )
            for page in range(1, page_count + 1)
        ],
    )


def test_request_hash_is_canonical_and_tamper_evident():
    request = StudioRenderRequest.model_validate(_request_payload())
    tampered = request.model_dump()
    tampered["render_options"]["flatten_pdf"] = True
    with pytest.raises(ValidationError, match="request hash mismatch"):
        StudioRenderRequest.model_validate(tampered)


def test_client_intent_binds_actor_and_hash_on_the_server():
    payload = _request_payload()
    actor = payload.pop("requested_by")
    payload.pop("request_sha256")
    intent = StudioRenderIntent.model_validate(payload)
    request = intent.bind_actor(actor)
    assert request.requested_by == actor
    assert request.request_sha256 == canonical_render_request_hash(
        kind=request.kind,
        draft_id=request.draft_id,
        expected_revision=request.expected_revision,
        identity_sha256=request.identity_sha256,
        snapshot_id=request.snapshot_id,
        content_sha256=request.content_sha256,
        source=request.source,
        render_options=request.render_options,
        requested_by=actor,
        input_binding_id=request.input_binding_id,
    )
    with pytest.raises(ValidationError):
        StudioRenderIntent.model_validate({**payload, "requested_by": uuid.uuid4()})


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
        StudioRenderRequest.model_validate(_request_payload(kind="studio_page_preview"))
    with pytest.raises(ValidationError, match="only for test renders"):
        StudioRenderRequest.model_validate(
            _request_payload(kind="studio_template_ocr", input_binding_id=uuid.uuid4())
        )
    with pytest.raises(ValidationError, match="cannot exceed max_pages"):
        StudioRenderOptions(page_number=3, max_pages=2)


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
        "effective_request_sha256": _effective_request_sha256(request),
        "renderer_manifest": manifest,
        "runtime_manifest_sha256": manifest.sha256,
        "job_expires_at": now + timedelta(hours=1),
    }
    geometry = _geometry()
    with pytest.raises(ValidationError, match="only after materialization"):
        StudioRenderJobStatus(
            **{**common, "progress": 10},
            state="running",
            leased_at=now,
            artifact_id=uuid.uuid4(),
            artifact_availability="available",
            artifact_metadata_availability="available",
            adoption_outcome="current_evidence",
            content_expires_at=now + timedelta(hours=1),
            metadata_expires_at=now + timedelta(days=1),
            result_url="/api/template-studio/render-artifacts/123",
            download_url="/api/template-studio/render-artifacts/123/content",
            geometry_url="/api/template-studio/render-artifacts/123/geometry",
            adopted_as_preferred_evidence=True,
            output_sha256="9" * 64,
            output_media_type="application/pdf",
            output_byte_size=100,
            artifact_page_count=1,
            document_page_count=1,
            geometry_manifest_sha256=geometry.sha256,
            retention_class="review",
        )
    artifact_id = uuid.uuid4()
    completed = StudioRenderJobStatus(
        **common,
        state="completed",
        completed_at=now,
        artifact_id=artifact_id,
        artifact_availability="available",
        artifact_metadata_availability="available",
        result_url=f"/api/template-studio/render-artifacts/{artifact_id}",
        download_url=f"/api/template-studio/render-artifacts/{artifact_id}/content",
        geometry_url=f"/api/template-studio/render-artifacts/{artifact_id}/geometry",
        adoption_outcome="stale_output",
        adopted_as_preferred_evidence=False,
        is_preferred_evidence=False,
        output_sha256="9" * 64,
        output_media_type="application/pdf",
        output_byte_size=100,
        artifact_page_count=1,
        document_page_count=1,
        geometry_manifest_sha256=geometry.sha256,
        retention_class="review",
        content_expires_at=now + timedelta(hours=1),
        metadata_expires_at=now + timedelta(days=1),
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


def test_expired_completed_status_preserves_metadata_but_disables_auto_open():
    request = StudioRenderRequest.model_validate(_request_payload())
    now = datetime.now(timezone.utc)
    manifest = _manifest()
    artifact_id = uuid.uuid4()
    geometry = _geometry()
    values = {
        "job_id": uuid.uuid4(),
        "status_url": "/api/template-studio/render-jobs/expired-status",
        "kind": request.kind,
        "state": "completed",
        "progress": 100,
        "attempts": 1,
        "max_attempts": 5,
        "created_at": now - timedelta(hours=1),
        "updated_at": now - timedelta(minutes=1),
        "completed_at": now - timedelta(minutes=1),
        "draft_id": request.draft_id,
        "rendered_revision": request.expected_revision,
        "identity_sha256": request.identity_sha256,
        "snapshot_id": request.snapshot_id,
        "snapshot_content_sha256": request.content_sha256,
        "source": request.source,
        "render_options": request.render_options,
        "render_options_sha256": request.render_options.sha256,
        "request_sha256": request.request_sha256,
        "effective_request_sha256": _effective_request_sha256(request),
        "renderer_manifest": manifest,
        "runtime_manifest_sha256": manifest.sha256,
        "artifact_id": artifact_id,
        "artifact_availability": "expired",
        "artifact_metadata_availability": "available",
        "result_url": f"/api/template-studio/render-artifacts/{artifact_id}",
        "download_url": (
            f"/api/template-studio/render-artifacts/{artifact_id}/content"
        ),
        "geometry_url": f"/api/template-studio/render-artifacts/{artifact_id}/geometry",
        "adoption_outcome": "current_evidence",
        "adopted_as_preferred_evidence": True,
        "is_preferred_evidence": True,
        "auto_open": False,
        "output_sha256": "9" * 64,
        "output_media_type": "application/pdf",
        "output_byte_size": 100,
        "artifact_page_count": 1,
        "document_page_count": 1,
        "geometry_manifest_sha256": geometry.sha256,
        "retention_class": "review",
        "job_expires_at": now + timedelta(hours=1),
        "content_expires_at": now - timedelta(seconds=1),
        "metadata_expires_at": now + timedelta(days=30),
    }
    expired = StudioRenderJobStatus(**values)
    assert expired.state == "completed"
    assert expired.artifact_availability == "expired"
    assert expired.artifact_id == artifact_id
    assert expired.auto_open is False
    with pytest.raises(ValidationError, match="auto-open"):
        StudioRenderJobStatus(**{**values, "auto_open": True})

    redacted_values = {
        **values,
        "artifact_metadata_availability": "expired",
        "result_url": None,
        "download_url": None,
        "geometry_url": None,
        "output_sha256": None,
        "output_media_type": None,
        "output_byte_size": None,
        "artifact_page_count": None,
        "document_page_count": None,
        "geometry_manifest_sha256": None,
        "metadata_expires_at": now - timedelta(microseconds=1),
    }
    redacted = StudioRenderJobStatus(**redacted_values)
    assert redacted.artifact_id == artifact_id
    assert redacted.output_sha256 is None
    with pytest.raises(ValidationError, match="must be redacted"):
        StudioRenderJobStatus(
            **{**redacted_values, "geometry_manifest_sha256": geometry.sha256}
        )


@pytest.mark.parametrize("code", sorted(STUDIO_PUBLIC_ERROR_MESSAGES))
def test_public_service_errors_are_closed_canonical_and_redacted(code):
    draft_id = uuid.uuid4()
    details = {
        "current_revision": 7,
        "current_etag": f'"studio:{draft_id}:7:{"a" * 64}"',
        "exception": "C:/private/client.docx",
    }
    error = StudioRenderServiceError(
        418,
        code,
        "raw provider exception with signed URL",
        details=details,
    )
    public = studio_render_public_error(error)
    assert public.code == code
    assert error.status_code == STUDIO_PUBLIC_ERROR_STATUS[code]
    assert public.message == STUDIO_PUBLIC_ERROR_MESSAGES[code]
    assert public.retryable == STUDIO_PUBLIC_ERROR_RETRYABLE[code]
    assert "provider" not in public.model_dump_json()
    assert "private" not in public.model_dump_json()
    if code == "stale_revision":
        assert public.details is not None
        assert public.details.current_revision == 7
    else:
        assert public.details is None


def test_unknown_exception_and_noncanonical_error_model_fail_closed():
    public = studio_render_public_error(RuntimeError("secret exception"))
    assert public == StudioRenderPublicError(
        code="processor_unavailable",
        message=STUDIO_PUBLIC_ERROR_MESSAGES["processor_unavailable"],
        retryable=True,
    )
    unknown = StudioRenderServiceError(
        418, "vendor_secret_code", "signed URL and provider exception"
    ).to_public_error()
    assert unknown == public
    with pytest.raises(ValidationError, match="not canonical"):
        StudioRenderPublicError(
            code="processor_unavailable",
            message="secret exception",
            retryable=True,
        )
    with pytest.raises(ValidationError, match="details are not allowed"):
        StudioRenderPublicError(
            code="job_not_found",
            message=STUDIO_PUBLIC_ERROR_MESSAGES["job_not_found"],
            retryable=False,
            details={"current_revision": 7},
        )


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
        "effective_request_sha256": _effective_request_sha256(request),
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
            error_retryable=True,
        )
    with pytest.raises(ValidationError, match="retryability"):
        StudioRenderJobStatus(
            **common,
            error_code="processor_unavailable",
            error_message=STUDIO_PUBLIC_ERROR_MESSAGES["processor_unavailable"],
            error_retryable=False,
        )


def test_phase_two_etag_contract_is_full_and_exact():
    draft_id = uuid.uuid4()
    value = f'"studio:{draft_id}:12:{"a" * 64}"'
    assert (
        StudioRenderErrorDetails(current_revision=12, current_etag=value).current_etag
        == value
    )
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
        "effective_request_sha256": _effective_request_sha256(request),
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


@pytest.mark.parametrize(
    ("fmt", "media_type"),
    [
        ("markdown", "application/pdf"),
        ("docx", "text/markdown"),
        ("pdf", "text/markdown"),
    ],
)
def test_source_contract_rejects_format_media_type_mismatch(fmt, media_type):
    with pytest.raises(ValidationError, match="format and media type"):
        StudioRenderSourceContract(
            artifact_id=uuid.uuid4(),
            sha256="a" * 64,
            media_type=media_type,
            format=fmt,
        )



@pytest.mark.parametrize(
    "updates",
    [
        {"coordinate_space": "none", "width_points": 612},
        {"coordinate_space": "points", "width_points": None},
        {"coordinate_space": "points", "width_px": 100},
        {"coordinate_space": "pixels", "width_px": None},
        {"coordinate_space": "pixels", "width_points": 612},
        {"coordinate_space": "points", "width_points": float("nan")},
    ],
)
def test_page_geometry_rejects_incomplete_or_mixed_coordinate_spaces(updates):
    base = {
        "page_number": 1,
        "coordinate_space": "points",
        "width_points": 612,
        "height_points": 792,
    }
    with pytest.raises(ValidationError):
        StudioPageGeometry(**{**base, **updates})


def test_geometry_manifest_rejects_page_shape_errors():
    page = StudioPageGeometry(
        page_number=1,
        coordinate_space="points",
        width_points=612,
        height_points=792,
    )
    with pytest.raises(ValidationError, match="page count"):
        StudioGeometryManifest(
            artifact_page_count=2, document_page_count=2, pages=[page]
        )
    with pytest.raises(ValidationError, match="duplicates"):
        StudioGeometryManifest(
            artifact_page_count=2,
            document_page_count=2,
            pages=[
                StudioPageGeometry(
                    page_number=1,
                    coordinate_space="points",
                    width_points=612,
                    height_points=792,
                ),
                StudioPageGeometry(
                    page_number=1,
                    coordinate_space="points",
                    width_points=612,
                    height_points=792,
                ),
            ],
        )
    with pytest.raises(ValidationError, match="ordered"):
        StudioGeometryManifest(
            artifact_page_count=2,
            document_page_count=2,
            pages=[
                StudioPageGeometry(
                    page_number=2,
                    coordinate_space="points",
                    width_points=612,
                    height_points=792,
                ),
                StudioPageGeometry(
                    page_number=1,
                    coordinate_space="points",
                    width_points=612,
                    height_points=792,
                ),
            ],
        )
    with pytest.raises(ValidationError, match="exceeds the source document"):
        StudioGeometryManifest(
            artifact_page_count=1,
            document_page_count=1,
            pages=[
                StudioPageGeometry(
                    page_number=2,
                    coordinate_space="points",
                    width_points=612,
                    height_points=792,
                )
            ],
        )


def test_status_rejects_hash_and_binding_inconsistencies():
    request = StudioRenderRequest.model_validate(_request_payload())
    now = datetime.now(timezone.utc)
    manifest = _manifest()
    job_id = uuid.uuid4()
    base = {
        "job_id": job_id,
        "status_url": f"/api/template-studio/render-jobs/{job_id}",
        "kind": request.kind,
        "state": "pending",
        "progress": 0,
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
        "effective_request_sha256": _effective_request_sha256(request),
        "renderer_manifest": manifest,
        "runtime_manifest_sha256": manifest.sha256,
        "job_expires_at": now + timedelta(hours=1),
    }
    with pytest.raises(ValidationError, match="render options hash mismatch"):
        StudioRenderJobStatus(
            **{**base, "render_options_sha256": "0" * 64}
        )
    with pytest.raises(ValidationError, match="effective request hash mismatch"):
        StudioRenderJobStatus(
            **{**base, "effective_request_sha256": "0" * 64}
        )
    with pytest.raises(ValidationError, match="renderer manifest hash mismatch"):
        StudioRenderJobStatus(
            **{**base, "runtime_manifest_sha256": "0" * 64}
        )
    with pytest.raises(ValidationError, match="input binding identity is incomplete"):
        StudioRenderJobStatus(
            **base,
            input_binding_sha256="0" * 64,
        )
    with pytest.raises(ValidationError, match="attempt count exceeds"):
        StudioRenderJobStatus(
            **{**base, "attempts": 6, "max_attempts": 5}
        )


def test_completed_evidence_retention_requires_ordered_or_absent_expiry():
    request = StudioRenderRequest.model_validate(_request_payload())
    now = datetime.now(timezone.utc)
    manifest = _manifest()
    artifact_id = uuid.uuid4()
    geometry = _geometry()
    values = {
        "job_id": uuid.uuid4(),
        "status_url": f"/api/template-studio/render-jobs/{artifact_id}",
        "kind": request.kind,
        "state": "completed",
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
        "effective_request_sha256": _effective_request_sha256(request),
        "renderer_manifest": manifest,
        "runtime_manifest_sha256": manifest.sha256,
        "artifact_id": artifact_id,
        "artifact_availability": "available",
        "artifact_metadata_availability": "available",
        "result_url": f"/api/template-studio/render-artifacts/{artifact_id}",
        "download_url": f"/api/template-studio/render-artifacts/{artifact_id}/content",
        "geometry_url": f"/api/template-studio/render-artifacts/{artifact_id}/geometry",
        "adoption_outcome": "current_evidence",
        "adopted_as_preferred_evidence": True,
        "is_preferred_evidence": True,
        "output_sha256": "9" * 64,
        "output_media_type": "application/pdf",
        "output_byte_size": 100,
        "artifact_page_count": 1,
        "document_page_count": 1,
        "geometry_manifest_sha256": geometry.sha256,
        "retention_class": "evidence",
        "job_expires_at": now + timedelta(hours=1),
    }
    assert StudioRenderJobStatus(**values).retention_class == "evidence"
    with pytest.raises(ValidationError, match="evidence artifacts do not expire"):
        StudioRenderJobStatus(
            **{**values, "content_expires_at": now + timedelta(hours=1)}
        )
    values["retention_class"] = "ephemeral"
    with pytest.raises(ValidationError, match="ordered retention expiry"):
        StudioRenderJobStatus(
            **{
                **values,
                "content_expires_at": now + timedelta(days=1),
                "metadata_expires_at": now + timedelta(hours=1),
            }
        )


def test_non_completed_states_reject_materialized_or_expiry_fields():
    request = StudioRenderRequest.model_validate(_request_payload())
    now = datetime.now(timezone.utc)
    manifest = _manifest()
    job_id = uuid.uuid4()
    base = {
        "job_id": job_id,
        "status_url": f"/api/template-studio/render-jobs/{job_id}",
        "kind": request.kind,
        "state": "pending",
        "progress": 0,
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
        "effective_request_sha256": _effective_request_sha256(request),
        "renderer_manifest": manifest,
        "runtime_manifest_sha256": manifest.sha256,
        "job_expires_at": now + timedelta(hours=1),
    }
    with pytest.raises(ValidationError, match="artifact availability"):
        StudioRenderJobStatus(
            **{**base, "artifact_availability": "available"}
        )
    with pytest.raises(ValidationError, match="artifact expiry"):
        StudioRenderJobStatus(
            **{**base, "content_expires_at": now + timedelta(hours=1)}
        )
    with pytest.raises(ValidationError, match="artifact metadata exists only"):
        StudioRenderJobStatus(
            **{**base, "output_sha256": "0" * 64}
        )


def test_public_error_rejects_noncanonical_retryability_and_disallowed_details():
    with pytest.raises(ValidationError, match="retryability"):
        StudioRenderPublicError(
            code="processor_unavailable",
            message=STUDIO_PUBLIC_ERROR_MESSAGES["processor_unavailable"],
            retryable=False,
        )
    with pytest.raises(ValidationError, match="details are not allowed"):
        StudioRenderPublicError(
            code="access_denied",
            message=STUDIO_PUBLIC_ERROR_MESSAGES["access_denied"],
            retryable=False,
            details={"current_revision": 1},
        )


def test_render_options_reject_non_finite_and_oversized_values():
    with pytest.raises(ValidationError, match="finite"):
        StudioRenderOptions(max_output_bytes=float("inf"))


def test_render_capability_rejects_unexpected_output_media_type():
    manifest = _manifest()
    with pytest.raises(ValidationError, match="output media type"):
        StudioRenderCapability(
            kind="studio_test_render",
            source_format="markdown",
            output_media_type="image/png",
            renderer_manifest=manifest,
        )


def test_render_capabilities_reject_duplicate_keys():
    manifest = _manifest()
    capability = StudioRenderCapability(
        kind="studio_test_render",
        source_format="markdown",
        output_media_type="application/pdf",
        renderer_manifest=manifest,
    )
    with pytest.raises(ValidationError, match="duplicates"):
        StudioRenderCapabilities(capabilities=[capability, capability])


def test_effective_request_hash_rejects_invalid_identities():
    valid_sha256 = "a" * 64
    with pytest.raises(ValueError, match="client request hash"):
        canonical_effective_render_request_hash(
            request_sha256="not-hex",
            input_binding_sha256=None,
            input_binding_version=None,
        )
    with pytest.raises(ValueError, match="input binding hash"):
        canonical_effective_render_request_hash(
            request_sha256=valid_sha256,
            input_binding_sha256="short",
            input_binding_version=1,
        )
    with pytest.raises(ValueError, match="input binding version"):
        canonical_effective_render_request_hash(
            request_sha256=valid_sha256,
            input_binding_sha256=valid_sha256,
            input_binding_version=0,
        )
    with pytest.raises(ValueError, match="input binding identity"):
        canonical_effective_render_request_hash(
            request_sha256=valid_sha256,
            input_binding_sha256=valid_sha256,
            input_binding_version=None,
        )


def test_artifact_geometry_rejects_manifest_hash_mismatch():
    geometry = _geometry()
    with pytest.raises(ValidationError, match="geometry manifest hash mismatch"):
        StudioArtifactGeometry(
            artifact_id=uuid.uuid4(),
            geometry_manifest=geometry,
            geometry_manifest_sha256="0" * 64,
        )


def test_request_rejects_page_number_for_non_preview_kinds():
    with pytest.raises(ValidationError, match="only for page preview"):
        StudioRenderRequest.model_validate(
            _request_payload(
                kind="studio_test_render",
                render_options=StudioRenderOptions(page_number=2),
            )
        )


def test_completed_status_rejects_incomplete_materialization():
    request = StudioRenderRequest.model_validate(_request_payload())
    now = datetime.now(timezone.utc)
    manifest = _manifest()
    base = {
        "job_id": uuid.uuid4(),
        "status_url": "/api/template-studio/render-jobs/completed-test",
        "state": "completed",
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
        "effective_request_sha256": _effective_request_sha256(request),
        "renderer_manifest": manifest,
        "runtime_manifest_sha256": manifest.sha256,
        "job_expires_at": now + timedelta(hours=1),
    }
    with pytest.raises(ValidationError, match="materialized artifact evidence"):
        StudioRenderJobStatus(
            **base,
            kind=request.kind,
            artifact_id=None,
            adoption_outcome="current_evidence",
        )
    with pytest.raises(ValidationError, match="completed jobs require artifact metadata"):
        StudioRenderJobStatus(
            **base,
            kind=request.kind,
            artifact_id=uuid.uuid4(),
            adoption_outcome="current_evidence",
            artifact_availability=None,
        )
    with pytest.raises(ValidationError, match="page previews contain exactly one artifact page"):
        StudioRenderJobStatus(
            **base,
            kind="studio_page_preview",
            artifact_id=uuid.uuid4(),
            adoption_outcome="current_evidence",
            artifact_availability="available",
            artifact_metadata_availability="available",
            adopted_as_preferred_evidence=False,
            is_preferred_evidence=False,
            artifact_page_count=2,
            result_url="/api/template-studio/render-artifacts/123",
            download_url="/api/template-studio/render-artifacts/123/content",
            geometry_url="/api/template-studio/render-artifacts/123/geometry",
            output_sha256="9" * 64,
            output_media_type="application/pdf",
            output_byte_size=100,
            document_page_count=2,
            geometry_manifest_sha256=_geometry(2).sha256,
            retention_class="review",
            content_expires_at=now + timedelta(hours=1),
            metadata_expires_at=now + timedelta(days=30),
        )


def test_completed_status_rejects_preferred_evidence_inconsistencies():
    request = StudioRenderRequest.model_validate(_request_payload())
    now = datetime.now(timezone.utc)
    manifest = _manifest()
    artifact_id = uuid.uuid4()
    geometry = _geometry()
    base = {
        "job_id": uuid.uuid4(),
        "status_url": f"/api/template-studio/render-jobs/{artifact_id}",
        "kind": request.kind,
        "state": "completed",
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
        "effective_request_sha256": _effective_request_sha256(request),
        "renderer_manifest": manifest,
        "runtime_manifest_sha256": manifest.sha256,
        "artifact_id": artifact_id,
        "artifact_availability": "available",
        "artifact_metadata_availability": "available",
        "result_url": f"/api/template-studio/render-artifacts/{artifact_id}",
        "download_url": f"/api/template-studio/render-artifacts/{artifact_id}/content",
        "geometry_url": f"/api/template-studio/render-artifacts/{artifact_id}/geometry",
        "output_sha256": "9" * 64,
        "output_media_type": "application/pdf",
        "output_byte_size": 100,
        "artifact_page_count": 1,
        "document_page_count": 1,
        "geometry_manifest_sha256": geometry.sha256,
        "retention_class": "review",
        "job_expires_at": now + timedelta(hours=1),
        "content_expires_at": now + timedelta(hours=1),
        "metadata_expires_at": now + timedelta(days=30),
    }
    with pytest.raises(ValidationError, match="only current output"):
        StudioRenderJobStatus(
            **base,
            adoption_outcome="stale_output",
            adopted_as_preferred_evidence=True,
            is_preferred_evidence=False,
        )
    with pytest.raises(ValidationError, match="diagnostic output cannot be current"):
        StudioRenderJobStatus(
            **base,
            adoption_outcome="stale_output",
            adopted_as_preferred_evidence=False,
            is_preferred_evidence=True,
        )


def test_auto_open_is_allowed_only_for_live_current_evidence():
    request = StudioRenderRequest.model_validate(_request_payload())
    now = datetime.now(timezone.utc)
    manifest = _manifest()
    artifact_id = uuid.uuid4()
    geometry = _geometry()
    completed_base = {
        "job_id": uuid.uuid4(),
        "status_url": f"/api/template-studio/render-jobs/{artifact_id}",
        "kind": request.kind,
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
        "effective_request_sha256": _effective_request_sha256(request),
        "renderer_manifest": manifest,
        "runtime_manifest_sha256": manifest.sha256,
        "artifact_id": artifact_id,
        "artifact_metadata_availability": "available",
        "result_url": f"/api/template-studio/render-artifacts/{artifact_id}",
        "download_url": f"/api/template-studio/render-artifacts/{artifact_id}/content",
        "geometry_url": f"/api/template-studio/render-artifacts/{artifact_id}/geometry",
        "adoption_outcome": "current_evidence",
        "adopted_as_preferred_evidence": False,
        "is_preferred_evidence": True,
        "output_sha256": "9" * 64,
        "output_media_type": "application/pdf",
        "output_byte_size": 100,
        "artifact_page_count": 1,
        "document_page_count": 1,
        "geometry_manifest_sha256": geometry.sha256,
        "retention_class": "review",
        "job_expires_at": now + timedelta(hours=1),
        "content_expires_at": now + timedelta(hours=1),
        "metadata_expires_at": now + timedelta(days=30),
    }
    with pytest.raises(ValidationError, match="only live current evidence may auto-open"):
        StudioRenderJobStatus(
            **completed_base,
            state="completed",
            progress=100,
            completed_at=now,
            artifact_availability="expired",
            auto_open=True,
        )
    pending_base = {
        "job_id": uuid.uuid4(),
        "status_url": "/api/template-studio/render-jobs/pending-auto-open",
        "kind": request.kind,
        "state": "pending",
        "progress": 0,
        "attempts": 0,
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
        "effective_request_sha256": _effective_request_sha256(request),
        "renderer_manifest": manifest,
        "runtime_manifest_sha256": manifest.sha256,
        "job_expires_at": now + timedelta(hours=1),
        "auto_open": True,
    }
    with pytest.raises(ValidationError, match="only completed current evidence may auto-open"):
        StudioRenderJobStatus(**pending_base)


def test_non_completed_states_reject_failure_and_metadata_availability():
    request = StudioRenderRequest.model_validate(_request_payload())
    now = datetime.now(timezone.utc)
    manifest = _manifest()
    base = {
        "job_id": uuid.uuid4(),
        "status_url": "/api/template-studio/render-jobs/non-terminal",
        "kind": request.kind,
        "state": "pending",
        "progress": 0,
        "attempts": 0,
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
        "effective_request_sha256": _effective_request_sha256(request),
        "renderer_manifest": manifest,
        "runtime_manifest_sha256": manifest.sha256,
        "job_expires_at": now + timedelta(hours=1),
    }
    with pytest.raises(ValidationError, match="artifact metadata availability"):
        StudioRenderJobStatus(
            **base,
            artifact_metadata_availability="available",
        )
    with pytest.raises(ValidationError, match="only completed current evidence may auto-open"):
        StudioRenderJobStatus(
            **base,
            auto_open=True,
        )
    with pytest.raises(ValidationError, match="failure details exist only for failed jobs"):
        StudioRenderJobStatus(
            **base,
            error_code="processor_unavailable",
            error_message=STUDIO_PUBLIC_ERROR_MESSAGES["processor_unavailable"],
            error_retryable=True,
        )
