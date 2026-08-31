"""Database-free checks for the Phase 3 public render contract."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.studio_render import (
    StudioRenderJobStatus,
    StudioRenderOptions,
    StudioRenderRequest,
    StudioRenderSourceContract,
    canonical_render_request_hash,
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
    common = {
        "job_id": uuid.uuid4(),
        "kind": request.kind,
        "progress": 100,
        "draft_id": request.draft_id,
        "rendered_revision": request.expected_revision,
        "identity_sha256": request.identity_sha256,
        "snapshot_id": request.snapshot_id,
        "content_sha256": request.content_sha256,
        "source": request.source,
        "request_sha256": request.request_sha256,
        "renderer_identity": "renderer-v1",
        "converter_identity": "converter-v1",
        "validator_identity": "validator-v1",
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    with pytest.raises(ValidationError, match="only after materialization"):
        StudioRenderJobStatus(
            **common,
            state="running",
            artifact_id=uuid.uuid4(),
            adoption_outcome="current_evidence",
        )
    completed = StudioRenderJobStatus(
        **common,
        state="completed",
        artifact_id=uuid.uuid4(),
        adoption_outcome="stale_output",
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


def test_failed_status_requires_sanitized_shape():
    request = StudioRenderRequest.model_validate(_request_payload())
    with pytest.raises(ValidationError, match="sanitized failure"):
        StudioRenderJobStatus(
            job_id=uuid.uuid4(),
            kind=request.kind,
            state="failed",
            progress=100,
            draft_id=request.draft_id,
            rendered_revision=request.expected_revision,
            identity_sha256=request.identity_sha256,
            snapshot_id=request.snapshot_id,
            content_sha256=request.content_sha256,
            source=request.source,
            request_sha256=request.request_sha256,
            renderer_identity="renderer-v1",
            converter_identity="converter-v1",
            validator_identity="validator-v1",
            expires_at=datetime.now(timezone.utc),
        )
    with pytest.raises(ValidationError, match="canonical sanitized message"):
        StudioRenderJobStatus(
            job_id=uuid.uuid4(),
            kind=request.kind,
            state="failed",
            progress=100,
            draft_id=request.draft_id,
            rendered_revision=request.expected_revision,
            identity_sha256=request.identity_sha256,
            snapshot_id=request.snapshot_id,
            content_sha256=request.content_sha256,
            source=request.source,
            request_sha256=request.request_sha256,
            renderer_identity="renderer-v1",
            converter_identity="converter-v1",
            validator_identity="validator-v1",
            error_code="processor_unavailable",
            error_message="raw exception: C:/private/source.docx",
            expires_at=datetime.now(timezone.utc),
        )
