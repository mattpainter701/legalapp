"""Database-free contract checks for the Studio render job store.

These cover the synchronous validators that guard what may be written to
``durable_jobs``: the sanitized result shape, the ownership fence, and the
store's own construction bounds. Nothing here touches a session — the store
records its dependencies before it ever issues a statement.
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.studio_render_jobs import (
    StudioJobLease,
    StudioRenderServiceError,
    _PersistedResult,
    _peek_requested_by,
    _StudioRenderJobStore,
)

TENANT = uuid.UUID("11111111-1111-4111-8111-111111111111")


def _store(**overrides):
    return _StudioRenderJobStore(SimpleNamespace(), tenant_id=TENANT, **overrides)


# --- store construction bounds --------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("active_job_limit", 0, "active_job_limit"),
        ("active_job_limit", 33, "active_job_limit"),
        ("job_ttl", timedelta(minutes=4), "job_ttl"),
        ("job_ttl", timedelta(days=8), "job_ttl"),
        ("enqueue_rate_limit", 0, "enqueue_rate_limit"),
        ("enqueue_rate_limit", 10_001, "enqueue_rate_limit"),
        ("enqueue_rate_window", timedelta(milliseconds=999), "enqueue_rate_window"),
        ("enqueue_rate_window", timedelta(hours=2), "enqueue_rate_window"),
        ("queued_byte_limit", 0, "queued_byte_limit"),
        ("queued_byte_limit", 101 * 1024**3, "queued_byte_limit"),
        ("max_input_binding_bytes", 0, "max_input_binding_bytes"),
        ("max_input_binding_bytes", 100 * 1024 * 1024 + 1, "max_input_binding_bytes"),
        ("max_download_bytes", 0, "max_download_bytes"),
        ("max_download_bytes", 100 * 1024 * 1024 + 1, "max_download_bytes"),
        ("retained_artifact_limit", 0, "retained_artifact_limit"),
        ("retained_artifact_limit", 100_001, "retained_artifact_limit"),
        ("retained_byte_limit", 0, "retained_byte_limit"),
        ("retained_byte_limit", 10 * 1024**4 + 1, "retained_byte_limit"),
        ("live_artifact_limit", 1, "live_artifact_limit"),
        ("live_byte_limit", 1, "live_byte_limit"),
    ],
)
def test_store_refuses_a_quota_it_could_not_enforce(field, value, expected):
    with pytest.raises(ValueError, match=expected):
        _store(**{field: value})


def test_store_refuses_an_ambiguous_manifest_configuration():
    manifest = SimpleNamespace()

    with pytest.raises(ValueError, match="ambiguous"):
        _store(renderer_manifest=manifest, renderer_manifests={"x": manifest})


def test_store_expands_a_single_manifest_across_every_dispatch_key():
    manifest = SimpleNamespace()

    store = _store(renderer_manifest=manifest)

    assert store.renderer_manifests
    assert all(len(key) == 3 for key in store.renderer_manifests)
    assert all(value is manifest for value in store.renderer_manifests.values())


def test_store_keeps_only_manifests_it_can_dispatch_on():
    manifest = SimpleNamespace()

    store = _store(
        renderer_manifests={
            ("studio_test_render", "markdown", 1): manifest,
            ("studio_test_render", "markdown", 2): manifest,
            ("not_a_kind", "markdown", 1): manifest,
            ("studio_test_render", "rtf", 1): manifest,
            "studio_page_preview": manifest,
            "not_a_kind": manifest,
        }
    )

    assert ("studio_test_render", "markdown", 1) in store.renderer_manifests
    assert ("studio_test_render", "markdown", 2) not in store.renderer_manifests
    assert ("not_a_kind", "markdown", 1) not in store.renderer_manifests
    assert ("studio_test_render", "rtf", 1) not in store.renderer_manifests
    assert ("studio_page_preview", "docx", 1) in store.renderer_manifests
    assert not any(key[0] == "not_a_kind" for key in store.renderer_manifests)


# --- sanitized failure contract -------------------------------------------


def test_service_error_replaces_an_unknown_code_with_a_safe_default():
    """A leaked internal code must never reach a tenant response."""

    error = StudioRenderServiceError(500, "psycopg_operational_error", "boom")

    assert error.code == "processor_unavailable"


def test_service_error_keeps_a_public_failure_code():
    error = StudioRenderServiceError(409, "processor_timeout", "stopped")

    assert error.code == "processor_timeout"


# --- persisted result shape -----------------------------------------------


def _artifact_result(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "artifact_id": uuid.uuid4(),
        "adoption_outcome": "current_evidence",
        "preferred_evidence_at_completion": True,
        "retention_class": "review",
        "output_sha256": "a" * 64,
        "media_type": "application/pdf",
        "byte_size": 1024,
        "artifact_page_count": 1,
        "document_page_count": 1,
        "geometry_manifest_sha256": "b" * 64,
        "content_expires_at": now + timedelta(hours=1),
        "metadata_expires_at": now + timedelta(days=30),
    }
    values.update(overrides)
    return values


def test_persisted_result_accepts_a_complete_artifact_result():
    result = _PersistedResult(**_artifact_result())

    assert result.adoption_outcome == "current_evidence"
    assert result.error_code is None


def test_persisted_result_accepts_a_bare_public_failure():
    result = _PersistedResult(error_code="processor_timeout")

    assert result.artifact_id is None


def test_persisted_result_rejects_a_failure_code_that_is_not_public():
    with pytest.raises(ValueError, match="public failure code"):
        _PersistedResult(error_code="psycopg_operational_error")


def test_persisted_result_refuses_to_attach_artifact_metadata_to_a_failure():
    with pytest.raises(ValueError, match="cannot contain artifact metadata"):
        _PersistedResult(error_code="processor_timeout", artifact_id=uuid.uuid4())


def test_persisted_result_rejects_a_partially_materialized_artifact():
    values = _artifact_result()
    values.pop("output_sha256")

    with pytest.raises(ValueError, match="artifact result is incomplete"):
        _PersistedResult(**values)


def test_persisted_result_rejects_expiry_without_an_artifact():
    with pytest.raises(ValueError, match="expiry has no artifact"):
        _PersistedResult(content_expires_at=datetime.now(timezone.utc))


def test_persisted_result_rejects_binding_identity_without_an_artifact():
    with pytest.raises(ValueError, match="metadata has no artifact"):
        _PersistedResult(input_binding_sha256="c" * 64, input_binding_version=1)


def test_persisted_result_rejects_half_an_input_binding_identity():
    values = _artifact_result(input_binding_sha256="c" * 64)

    with pytest.raises(ValueError, match="binding identity is incomplete"):
        _PersistedResult(**values)


def test_persisted_result_rejects_an_unknown_adoption_outcome():
    with pytest.raises(ValueError, match="invalid adoption outcome"):
        _PersistedResult(**_artifact_result(adoption_outcome="adopted"))


def test_persisted_result_reserves_preferred_evidence_for_current_output():
    values = _artifact_result(
        adoption_outcome="stale_output", preferred_evidence_at_completion=True
    )

    with pytest.raises(ValueError, match="only current output"):
        _PersistedResult(**values)


def test_persisted_result_rejects_an_unknown_retention_class():
    with pytest.raises(ValueError, match="invalid retention class"):
        _PersistedResult(**_artifact_result(retention_class="forever"))


@pytest.mark.parametrize("retention_class", ["ephemeral", "review"])
def test_persisted_result_requires_expiry_for_temporary_retention(retention_class):
    values = _artifact_result(
        retention_class=retention_class,
        content_expires_at=None,
        metadata_expires_at=None,
    )

    with pytest.raises(ValueError, match="retention expiry is required"):
        _PersistedResult(**values)


def test_persisted_result_requires_metadata_to_outlive_temporary_content():
    now = datetime.now(timezone.utc)
    values = _artifact_result(
        content_expires_at=now + timedelta(days=30),
        metadata_expires_at=now + timedelta(hours=1),
    )

    with pytest.raises(ValueError, match="retention expiry is required"):
        _PersistedResult(**values)


def test_persisted_result_refuses_to_expire_evidence():
    values = _artifact_result(retention_class="evidence")

    with pytest.raises(ValueError, match="evidence artifact cannot expire"):
        _PersistedResult(**values)


def test_persisted_result_accepts_evidence_that_never_expires():
    values = _artifact_result(
        retention_class="evidence",
        content_expires_at=None,
        metadata_expires_at=None,
    )

    result = _PersistedResult(**values)

    assert result.content_expires_at is None
    assert result.metadata_expires_at is None


# --- ownership fence -------------------------------------------------------


def _lease(**overrides):
    values = {
        "job_id": uuid.uuid4(),
        "tenant_id": TENANT,
        "owner": "studio-worker-a",
        "token": uuid.uuid4(),
        "attempt": 1,
        "payload": None,
    }
    values.update(overrides)
    return StudioJobLease(**values)


def test_peek_requested_by_reads_the_ownership_fence():
    requester = uuid.uuid4()
    row = SimpleNamespace(payload={"requested_by": str(requester)})

    assert _peek_requested_by(row) == requester


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("not-a-dict", id="not-a-mapping"),
        pytest.param(None, id="absent"),
        pytest.param({}, id="no-requester"),
        pytest.param({"requested_by": "not-a-uuid"}, id="malformed-requester"),
        pytest.param({"requested_by": None}, id="null-requester"),
    ],
)
def test_peek_requested_by_never_raises_on_untrusted_payloads(payload):
    assert _peek_requested_by(SimpleNamespace(payload=payload)) is None


def test_ownership_is_refused_for_a_missing_row():
    lease = _lease()

    assert _StudioRenderJobStore._owns(None, lease) is False


@pytest.mark.parametrize(
    "row_overrides",
    [
        pytest.param({"tenant_id": uuid.uuid4()}, id="another-tenant"),
        pytest.param({"lease_owner": "studio-worker-b"}, id="another-owner"),
        pytest.param({"status": "succeeded"}, id="terminal-status"),
        pytest.param({"status": "queued"}, id="unclaimed-status"),
    ],
)
def test_ownership_is_refused_when_the_row_moved_out_from_under_the_lease(
    row_overrides,
):
    lease = _lease()
    values = {
        "tenant_id": lease.tenant_id,
        "lease_owner": lease.owner,
        "status": "running",
        "payload": {},
    }
    values.update(row_overrides)

    assert _StudioRenderJobStore._owns(SimpleNamespace(**values), lease) is False
