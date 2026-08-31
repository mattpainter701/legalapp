"""Focused PostgreSQL tests for fenced Studio render orchestration."""

import hashlib
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.durable_job import DurableJob
from app.models.studio_draft import StudioDraft, StudioDraftSnapshot, StudioSourceArtifact
from app.models.studio_render import StudioRenderArtifact
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.studio_render import (
    StudioRenderOptions,
    StudioRenderRequest,
    StudioRenderSourceContract,
    canonical_render_request_hash,
)
from app.services.studio_artifact_retention import StudioArtifactRetentionService
from app.services.studio_object_storage import LocalStudioObjectStore, StudioStorageError
from app.services.studio_render_jobs import (
    StudioRenderJobService,
    StudioRenderServiceError,
)
from app.services.studio_render_worker import StudioRenderWorker
from app.services.studio_worker_isolation import (
    StudioIsolationProfile,
    StudioIsolationRegistry,
    StudioTrustedProcessorAdapter,
)

pytestmark = pytest.mark.asyncio


async def _foundation(db, tenant, user):
    content = b"# Studio source\n"
    source_id = uuid.uuid4()
    draft_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    source_sha = hashlib.sha256(content).hexdigest()
    identity_sha = "a" * 64
    snapshot_sha = "b" * 64
    db.add(
        StudioSourceArtifact(
            id=source_id,
            tenant_id=tenant.id,
            sha256=source_sha,
            media_type="text/markdown",
            format="markdown",
            byte_size=len(content),
            resolver_key=f"studio-db:v1:{uuid.uuid4()}",
            content_bytes=content,
            created_by_user_id=user.id,
        )
    )
    db.add(
        StudioDraft(
            id=draft_id,
            tenant_id=tenant.id,
            source_artifact_id=source_id,
            source_sha256=source_sha,
            source_media_type="text/markdown",
            title="Phase 3 draft",
            format="markdown",
            revision=1,
            identity_sha256=identity_sha,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
    )
    db.add(
        StudioDraftSnapshot(
            id=snapshot_id,
            tenant_id=tenant.id,
            draft_id=draft_id,
            source_artifact_id=source_id,
            revision=1,
            identity_sha256=identity_sha,
            content_sha256=snapshot_sha,
            payload={"contract_version": 1, "format": "markdown"},
            created_by_user_id=user.id,
        )
    )
    await db.commit()
    return {
        "draft_id": draft_id,
        "snapshot_id": snapshot_id,
        "source_id": source_id,
        "source_sha": source_sha,
        "identity_sha": identity_sha,
        "snapshot_sha": snapshot_sha,
    }


def _request(foundation, user_id, *, options=None):
    source = StudioRenderSourceContract(
        artifact_id=foundation["source_id"],
        sha256=foundation["source_sha"],
        media_type="text/markdown",
        format="markdown",
    )
    render_options = options or StudioRenderOptions()
    values = {
        "kind": "studio_test_render",
        "draft_id": foundation["draft_id"],
        "expected_revision": 1,
        "identity_sha256": foundation["identity_sha"],
        "snapshot_id": foundation["snapshot_id"],
        "content_sha256": foundation["snapshot_sha"],
        "source": source,
        "render_options": render_options,
        "requested_by": user_id,
        "input_binding_id": None,
    }
    return StudioRenderRequest(
        **values,
        request_sha256=canonical_render_request_hash(**values),
    )


async def _enqueue(db, tenant, user, foundation, key="tenant-idem-key"):
    service = StudioRenderJobService(
        db, tenant_id=tenant.id, actor_user_id=user.id
    )
    accepted = await service.enqueue_test_render(
        _request(foundation, user.id), idempotency_key=key
    )
    return service, accepted


async def test_tenant_idempotency_replay_conflict_and_quota(
    db_session, test_tenant, test_user
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, first = await _enqueue(
        db_session, test_tenant, test_user, foundation, "tenant-idem-replay"
    )
    second = await service.enqueue_test_render(
        _request(foundation, test_user.id),
        idempotency_key="tenant-idem-replay",
    )
    assert second.job_id == first.job_id
    assert not db_session.in_transaction()

    changed = _request(
        foundation,
        test_user.id,
        options=StudioRenderOptions(flatten_pdf=True),
    )
    with pytest.raises(StudioRenderServiceError) as conflict:
        await service.enqueue_test_render(
            changed, idempotency_key="tenant-idem-replay"
        )
    assert conflict.value.code == "idempotency_key_mismatch"
    assert not db_session.in_transaction()

    limited = StudioRenderJobService(
        db_session,
        tenant_id=test_tenant.id,
        actor_user_id=test_user.id,
        active_job_limit=1,
    )
    with pytest.raises(StudioRenderServiceError) as quota:
        await limited.enqueue_test_render(
            changed, idempotency_key="tenant-idem-quota"
        )
    assert quota.value.status_code == 429
    assert not db_session.in_transaction()


async def test_cross_tenant_status_cancel_and_claim_are_not_found(
    db_session, test_tenant, test_user
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    _, accepted = await _enqueue(db_session, test_tenant, test_user, foundation)
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other firm",
        domain="other-studio.test",
        billing_tier="payg",
        is_active=True,
    )
    other_user = User(
        id=uuid.uuid4(),
        tenant_id=other_tenant.id,
        email="studio@other.test",
        full_name="Other Studio User",
        role="admin",
        oauth_provider="google",
        oauth_subject="other-studio-user",
        is_active=True,
    )
    db_session.add_all([other_tenant, other_user])
    await db_session.commit()
    other = StudioRenderJobService(db_session, tenant_id=other_tenant.id)
    with pytest.raises(StudioRenderServiceError) as status_error:
        await other.status(accepted.job_id)
    assert status_error.value.status_code == 404
    assert not db_session.in_transaction()
    with pytest.raises(StudioRenderServiceError) as cancel_error:
        await other.request_cancel(accepted.job_id)
    assert cancel_error.value.status_code == 404
    assert not db_session.in_transaction()
    assert await other.claim(accepted.job_id, owner="worker-other") is None
    assert not db_session.in_transaction()
    row = await db_session.get(DurableJob, accepted.job_id)
    assert row.status == "pending"


async def test_claim_rejects_invalid_lease_before_database_access(
    db_session, test_tenant, test_user
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(db_session, test_tenant, test_user, foundation)
    for invalid in (True, 29, 3601, 30.5):
        with pytest.raises(ValueError, match="lease_seconds"):
            await service.claim(
                accepted.job_id,
                owner="bounded-worker",
                lease_seconds=invalid,
            )
        assert not db_session.in_transaction()
    row = await db_session.get(DurableJob, accepted.job_id)
    assert row.status == "pending"
    assert row.attempts == 0
    assert row.lease_owner is None
    await db_session.rollback()


async def test_fenced_lease_rejects_old_attempt_even_for_same_owner(
    db_session, test_tenant, test_user
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(db_session, test_tenant, test_user, foundation)
    first = await service.claim(accepted.job_id, owner="worker-same", lease_seconds=900)
    assert first is not None
    assert not await service.renew_lease(replace(first, token=uuid.uuid4()))
    assert (
        await service.claim(accepted.job_id, owner="short-lease", lease_seconds=30)
        is None
    )

    row = await db_session.get(DurableJob, accepted.job_id)
    row.leased_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    row.payload = {
        **row.payload,
        "lease_expires_at": (
            datetime.now(timezone.utc) - timedelta(minutes=5)
        ).isoformat(),
    }
    await db_session.commit()
    second = await service.claim(accepted.job_id, owner="worker-same", lease_seconds=30)
    assert second is not None
    assert second.attempt == first.attempt + 1
    assert second.token != first.token
    assert not await service.renew_lease(first)
    assert await service.renew_lease(second)


async def test_pending_and_running_cancellation_transitions(
    db_session, test_tenant, test_user
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, pending = await _enqueue(
        db_session, test_tenant, test_user, foundation, "cancel-pending"
    )
    assert (await service.request_cancel(pending.job_id)).state == "cancelled"
    assert not db_session.in_transaction()

    _, running = await _enqueue(
        db_session, test_tenant, test_user, foundation, "cancel-running"
    )
    lease = await service.claim(running.job_id, owner="cancel-worker")
    assert lease is not None
    assert (await service.request_cancel(running.job_id)).state == "cancel_requested"
    assert not db_session.in_transaction()
    assert not await service.renew_lease(lease)
    row = await db_session.get(DurableJob, running.job_id)
    row.leased_at = datetime.now(timezone.utc) - timedelta(minutes=20)
    row.payload = {
        **row.payload,
        "lease_expires_at": (
            datetime.now(timezone.utc) - timedelta(minutes=20)
        ).isoformat(),
    }
    await db_session.commit()
    assert await service.claim(running.job_id, owner="reaper") is None
    assert (await service.status(running.job_id)).state == "cancelled"


async def test_stale_attempt_at_retry_ceiling_is_terminalized(
    db_session, test_tenant, test_user
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "attempt-ceiling"
    )
    lease = await service.claim(accepted.job_id, owner="ceiling-worker")
    assert lease is not None
    row = await db_session.get(DurableJob, accepted.job_id)
    row.attempts = row.max_attempts
    row.leased_at = datetime.now(timezone.utc) - timedelta(minutes=20)
    row.payload = {
        **row.payload,
        "lease_expires_at": (
            datetime.now(timezone.utc) - timedelta(minutes=20)
        ).isoformat(),
    }
    await db_session.commit()
    assert await service.claim(accepted.job_id, owner="ceiling-reaper") is None
    status = await service.status(accepted.job_id)
    assert status.state == "failed"
    assert status.error_code == "processor_unavailable"


async def test_stale_source_binding_and_expired_status_fail_closed(
    db_session, test_tenant, test_user
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service = StudioRenderJobService(
        db_session, tenant_id=test_tenant.id, actor_user_id=test_user.id
    )
    wrong_source = StudioRenderSourceContract(
        artifact_id=uuid.uuid4(),
        sha256=foundation["source_sha"],
        media_type="text/markdown",
        format="markdown",
    )
    valid = _request(foundation, test_user.id)
    values = valid.model_dump(exclude={"request_sha256"})
    values["source"] = wrong_source
    values["render_options"] = valid.render_options
    stale = StudioRenderRequest(
        **values,
        request_sha256=canonical_render_request_hash(**values),
    )
    with pytest.raises(StudioRenderServiceError) as stale_error:
        await service.enqueue_test_render(
            stale, idempotency_key="stale-source-binding"
        )
    assert stale_error.value.code == "stale_revision"
    assert not db_session.in_transaction()

    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "expire-status"
    )
    row = await db_session.get(DurableJob, accepted.job_id)
    row.payload = {
        **row.payload,
        "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    }
    await db_session.commit()
    status = await service.status(accepted.job_id)
    assert status.state == "failed"
    assert status.error_code == "expired"
    assert status.artifact_id is None


async def test_current_and_stale_adoption_flush_exact_artifact_ids(
    db_session, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, current_job = await _enqueue(
        db_session, test_tenant, test_user, foundation, "adopt-current"
    )
    current_lease = await service.claim(current_job.job_id, owner="adopter-current")
    object_store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    output = object_store.put(
        test_tenant.id,
        b"verified-pdf-output",
        media_type="application/pdf",
    )
    artifact_id, outcome = await service.adopt_output(
        current_lease,
        output,
        object_store=object_store,
        artifact_kind="test_render",
        renderer_identity=current_lease.payload.renderer_identity,
        converter_identity=current_lease.payload.converter_identity,
        validator_identity=current_lease.payload.validator_identity,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert outcome == "current_evidence"
    current_row = await db_session.get(DurableJob, current_job.job_id)
    current_artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    current_draft = await db_session.get(StudioDraft, foundation["draft_id"])
    assert current_artifact.id == artifact_id
    assert current_row.result["artifact_id"] == str(artifact_id)
    assert current_draft.evidence_revision == 1

    current_draft.revision = 2
    current_draft.identity_sha256 = "d" * 64
    await db_session.commit()
    stale_service, stale_job = await _enqueue_with_stale_request(
        db_session, test_tenant, test_user, foundation
    )
    stale_lease = await stale_service.claim(stale_job.job_id, owner="adopter-stale")
    stale_id, stale_outcome = await stale_service.adopt_output(
        stale_lease,
        output,
        object_store=object_store,
        artifact_kind="test_render",
        renderer_identity=stale_lease.payload.renderer_identity,
        converter_identity=stale_lease.payload.converter_identity,
        validator_identity=stale_lease.payload.validator_identity,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert stale_outcome == "stale_output"
    stale_row = await db_session.get(DurableJob, stale_job.job_id)
    stale_artifact = await db_session.get(StudioRenderArtifact, stale_id)
    assert stale_artifact.adoption_outcome == "stale_output"
    assert stale_row.result["artifact_id"] == str(stale_id)


async def _enqueue_with_stale_request(db, tenant, user, foundation):
    """Queue valid revision 1, then let the caller advance the draft before adoption."""

    draft = await db.get(StudioDraft, foundation["draft_id"])
    draft.revision = 1
    draft.identity_sha256 = foundation["identity_sha"]
    draft.evidence_revision = None
    await db.commit()
    service, accepted = await _enqueue(
        db, tenant, user, foundation, "adopt-stale"
    )
    draft = await db.get(StudioDraft, foundation["draft_id"])
    draft.revision = 2
    draft.identity_sha256 = "d" * 64
    await db.commit()
    return service, accepted


async def test_cancelled_output_materializes_without_current_evidence(
    db_session, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "cancel-output"
    )
    lease = await service.claim(accepted.job_id, owner="cancel-output-worker")
    await service.request_cancel(accepted.job_id)
    object_store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    output = object_store.put(
        test_tenant.id,
        b"cancelled-pdf-output",
        media_type="application/pdf",
    )
    artifact_id, outcome = await service.adopt_output(
        lease,
        output,
        object_store=object_store,
        artifact_kind="test_render",
        renderer_identity=lease.payload.renderer_identity,
        converter_identity=lease.payload.converter_identity,
        validator_identity=lease.payload.validator_identity,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert outcome == "cancelled_output"
    status = await service.status(accepted.job_id)
    assert status.state == "completed"
    assert status.artifact_id == artifact_id
    assert status.adoption_outcome == "cancelled_output"
    assert not db_session.in_transaction()
    draft = await db_session.get(StudioDraft, foundation["draft_id"])
    assert draft.evidence_revision is None


async def test_worker_missing_or_tampered_source_fails_sanitized(
    db_session, test_engine, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "worker-missing-source"
    )
    source = await db_session.get(StudioSourceArtifact, foundation["source_id"])
    source.content_bytes = b"tampered"
    source.byte_size = len(source.content_bytes)
    await db_session.commit()

    launcher = tmp_path / "sandbox-launcher.bin"
    executable = tmp_path / "renderer.bin"
    launcher.write_bytes(b"trusted sandbox")
    executable.write_bytes(b"renderer")
    registry = StudioIsolationRegistry(
        [
            StudioIsolationProfile(
                profile_id="studio-worker-test-v1",
                launcher=launcher.absolute(),
                launcher_sha256=hashlib.sha256(launcher.read_bytes()).hexdigest(),
                executable=executable.absolute(),
                executable_sha256=hashlib.sha256(
                    executable.read_bytes()
                ).hexdigest(),
                network_isolation_enforced=True,
                resource_limits_enforced=True,
                process_tree_enforced=True,
            )
        ]
    )
    processor = StudioTrustedProcessorAdapter(
        registry,
        "studio-worker-test-v1",
        workspace_root=tmp_path,
    )
    worker = StudioRenderWorker(
        async_sessionmaker(test_engine, expire_on_commit=False),
        object_store=LocalStudioObjectStore(
            tmp_path / "objects", max_object_bytes=1024
        ),
        processors={"studio_test_render": processor},
        lease_seconds=30,
        heartbeat_seconds=5,
        processor_timeout_seconds=5,
        artifact_ttl_seconds=300,
    )
    process_mock = AsyncMock(
        side_effect=AssertionError("tampered source must fail before processor launch")
    )
    with patch.object(StudioTrustedProcessorAdapter, "process", process_mock):
        assert await worker.process(accepted.job_id, test_tenant.id) is True
    status = await service.status(accepted.job_id)
    assert status.state == "failed"
    assert status.error_code == "source_integrity_failed"
    process_mock.assert_not_awaited()


async def test_retention_deletes_shared_cas_only_after_last_reference(
    db_session, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    object_store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    output = object_store.put(
        test_tenant.id, b"shared-cas-output", media_type="application/pdf"
    )
    artifact_ids = []
    for key in ("shared-cas-first", "shared-cas-second"):
        service, accepted = await _enqueue(
            db_session, test_tenant, test_user, foundation, key
        )
        lease = await service.claim(accepted.job_id, owner=f"worker-{key}")
        artifact_id, _ = await service.adopt_output(
            lease,
            output,
            object_store=object_store,
            artifact_kind="test_render",
            renderer_identity=lease.payload.renderer_identity,
            converter_identity=lease.payload.converter_identity,
            validator_identity=lease.payload.validator_identity,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        artifact_ids.append(artifact_id)
    for artifact_id in artifact_ids:
        artifact = await db_session.get(StudioRenderArtifact, artifact_id)
        artifact.adoption_outcome = "stale_output"
    await db_session.commit()

    async def not_held(_candidate):
        return False

    retention = StudioArtifactRetentionService(
        db_session,
        tenant_id=test_tenant.id,
        object_store=object_store,
        legal_hold_check=not_held,
    )
    assert (
        await retention.delete_if_eligible(
            artifact_ids[0], now=datetime.now(timezone.utc)
        )
    ).eligible
    assert object_store.read(output) == b"shared-cas-output"
    assert (
        await retention.delete_if_eligible(
            artifact_ids[1], now=datetime.now(timezone.utc)
        )
    ).eligible
    with pytest.raises(StudioStorageError) as missing:
        object_store.read(output)
    assert missing.value.code == "object_missing"


async def test_retention_recovers_or_preserves_durable_pending_state(
    db_session, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    object_store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    output = object_store.put(
        test_tenant.id, b"pending-output", media_type="application/pdf"
    )
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "pending-recovery"
    )
    lease = await service.claim(accepted.job_id, owner="pending-worker")
    artifact_id, _ = await service.adopt_output(
        lease,
        output,
        object_store=object_store,
        artifact_kind="test_render",
        renderer_identity=lease.payload.renderer_identity,
        converter_identity=lease.payload.converter_identity,
        validator_identity=lease.payload.validator_identity,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    now = datetime.now(timezone.utc)
    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    artifact.adoption_outcome = "stale_output"
    artifact.storage_state = "delete_pending"
    artifact.delete_requested_at = now
    artifact.legal_hold_at = now
    await db_session.commit()

    async def not_held(_candidate):
        return False

    retention = StudioArtifactRetentionService(
        db_session,
        tenant_id=test_tenant.id,
        object_store=object_store,
        legal_hold_check=not_held,
    )
    restored = await retention.delete_if_eligible(artifact_id, now=now)
    assert restored.reason == "legal_hold"
    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    assert artifact.storage_state == "active"
    assert artifact.delete_requested_at is None

    artifact.storage_state = "delete_pending"
    artifact.delete_requested_at = now
    object_store.delete(output)
    await db_session.commit()
    pending = await retention.delete_if_eligible(artifact_id, now=now)
    assert pending.reason == "storage_delete_pending"
    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    assert artifact.storage_state == "delete_pending"
