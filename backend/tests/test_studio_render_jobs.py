"""Focused PostgreSQL tests for fenced Studio render orchestration."""

import asyncio
import hashlib
import os
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from app.database import set_tenant_context
from app.models.durable_job import DurableJob
from app.models.studio_draft import (
    StudioDraft,
    StudioDraftAuditEvent,
    StudioDraftSnapshot,
    StudioSourceArtifact,
)
from app.models.studio_render import (
    StudioPreferredRenderEvidence,
    StudioRenderArtifact,
)
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.studio_render import (
    StudioGeometryManifest,
    StudioPageGeometry,
    StudioRendererComponent,
    StudioRendererManifest,
    StudioRenderOptions,
    StudioRenderRequest,
    StudioRenderSourceContract,
    canonical_effective_render_request_hash,
    canonical_render_request_hash,
)
from app.services.studio_artifact_retention import (
    StudioArtifactRetentionService,
    StudioStagedReceiptReconciler,
)
from app.services.studio_drafts import StudioDraftService
from app.services.studio_object_storage import (
    LocalStudioObjectStore,
    StudioObjectRef,
    StudioStorageError,
)
from app.services.studio_render_jobs import (
    StudioRenderJobService,
    StudioResolvedInputBinding,
    StudioRenderServiceError,
    StudioRenderWorkerService,
    run_studio_consumer_transaction,
)
from app.services.studio_render_worker import StudioRenderWorker
from app.services.studio_worker_isolation import (
    StudioIsolationProfile,
    StudioIsolationRegistry,
    StudioTrustedProcessorAdapter,
)

pytestmark = pytest.mark.asyncio

_TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5432/legalapp_test",
)
_RLS_ROLE = "studio_render_rls_probe_role"
_RLS_PASSWORD = "studio_render_rls_probe_pw"


def _manifest():
    def component(name, value):
        return StudioRendererComponent(
            name=name, version="1.0.0", content_sha256=value * 64
        )

    return StudioRendererManifest(
        isolation_policy_id="studio-db-v1",
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


def _geometry_kwargs(page_count: int = 1) -> dict:
    manifest = StudioGeometryManifest(
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
    return {
        "artifact_page_count": page_count,
        "document_page_count": page_count,
        "geometry_manifest": manifest,
        "geometry_manifest_sha256": manifest.sha256,
    }


class _TestServices:
    def __init__(self, consumer, worker):
        self.consumer = consumer
        self.worker = worker

    def __getattr__(self, name):
        if hasattr(self.consumer, name):
            return getattr(self.consumer, name)
        return getattr(self.worker, name)


class _BlockingReadStore:
    def __init__(self, delegate, started, release):
        self.delegate = delegate
        self.started = started
        self.release = release

    def read(self, ref, *, max_bytes=None):
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("blocked read was not released")
        return self.delegate.read(ref, max_bytes=max_bytes)

    def __getattr__(self, name):
        return getattr(self.delegate, name)


class _BlockingDeleteStore:
    def __init__(self, delegate, started, release):
        self.delegate = delegate
        self.started = started
        self.release = release

    def delete(self, ref):
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("blocked delete was not released")
        return self.delegate.delete(ref)

    def __getattr__(self, name):
        return getattr(self.delegate, name)


class _FirstBlockingStageStore:
    def __init__(self, delegate, started, release):
        self.delegate = delegate
        self.started = started
        self.release = release
        self._lock = threading.Lock()
        self._calls = 0

    def stage(self, *args, **kwargs):
        with self._lock:
            self._calls += 1
            should_block = self._calls == 1
        if should_block:
            self.started.set()
            if not self.release.wait(timeout=10):
                raise RuntimeError("blocked stage was not released")
        return self.delegate.stage(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.delegate, name)


class _FirstBlockingStagedDeleteStore:
    def __init__(self, delegate, started, release):
        self.delegate = delegate
        self.started = started
        self.release = release
        self._lock = threading.Lock()
        self._calls = 0

    def delete_staged(self, stage):
        with self._lock:
            self._calls += 1
            should_block = self._calls == 1
        if should_block:
            self.started.set()
            if not self.release.wait(timeout=10):
                raise RuntimeError("blocked staged delete was not released")
        return self.delegate.delete_staged(stage)

    def __getattr__(self, name):
        return getattr(self.delegate, name)


class _AsyncBarrier:
    def __init__(self, parties):
        self.parties = parties
        self.arrived = 0
        self.condition = asyncio.Condition()

    async def wait(self):
        async with self.condition:
            self.arrived += 1
            if self.arrived >= self.parties:
                self.condition.notify_all()
                return
            await self.condition.wait_for(lambda: self.arrived >= self.parties)


class _AdmissionGateResolver:
    def __init__(self, tenant_id, binding_id):
        self.tenant_id = tenant_id
        self.binding_id = binding_id
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        content = b"admission-gate"
        sha256 = hashlib.sha256(content).hexdigest()
        self.resolved = StudioResolvedInputBinding(
            StudioObjectRef(
                tenant_id=tenant_id,
                object_key=f"studio-content/v1/{sha256[:2]}/{sha256}",
                sha256=sha256,
                byte_size=len(content),
                media_type="application/json",
            ),
            version=1,
        )

    async def resolve(self, tenant_id, binding_id):
        assert tenant_id == self.tenant_id
        assert binding_id == self.binding_id
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await self.release.wait()
        return self.resolved


async def _wait_past_database_time(factory, boundary):
    while True:
        async with factory() as session:
            current = await session.scalar(select(func.clock_timestamp()))
        if current > boundary:
            return current
        await asyncio.sleep(min(0.05, (boundary - current).total_seconds()))


async def _noop_audit(_event, _job_id):
    return None


async def _foundation(db, tenant, user):
    content = b"# Studio source\n"
    source_id = uuid.uuid4()
    draft_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    source_sha = hashlib.sha256(content).hexdigest()
    identity_sha = "a" * 64
    snapshot_payload = {
        "contract_version": 1,
        "draft_id": str(draft_id),
        "revision": 1,
        "identity_sha256": identity_sha,
        "format": "markdown",
        "lifecycle_state": "active",
        "source": {
            "contract_version": 1,
            "artifact_id": str(source_id),
            "sha256": source_sha,
            "media_type": "text/markdown",
            "format": "markdown",
        },
        "fields": [],
        "placements": [],
    }
    snapshot_sha = hashlib.sha256(
        __import__("json")
        .dumps(
            snapshot_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        .encode("utf-8")
    ).hexdigest()
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
            payload=snapshot_payload,
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


def _request(foundation, user_id, *, options=None, input_binding_id=None):
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
        "input_binding_id": input_binding_id,
    }
    return StudioRenderRequest(
        **values,
        request_sha256=canonical_render_request_hash(**values),
    )


async def _enqueue(db, tenant, user, foundation, key="tenant-idem-key"):
    service = StudioRenderJobService(
        db,
        tenant_id=tenant.id,
        actor_user_id=user.id,
        renderer_manifest=_manifest(),
    )
    accepted = await service.enqueue_test_render(
        _request(foundation, user.id),
        idempotency_key=key,
        audit=_noop_audit,
    )
    await db.commit()
    return _TestServices(
        service, StudioRenderWorkerService(db, tenant_id=tenant.id)
    ), accepted


async def _independent_enqueue(
    factory,
    *,
    tenant_id,
    actor_user_id,
    request,
    key,
    service_options=None,
    barrier=None,
):
    async with factory() as session:
        service = StudioRenderJobService(
            session,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            renderer_manifest=_manifest(),
            **(service_options or {}),
        )
        try:
            if barrier is not None:
                await barrier.wait()
            accepted = await service.enqueue_test_render(
                request,
                idempotency_key=key,
                audit=_noop_audit,
            )
            await session.commit()
            return accepted
        except StudioRenderServiceError as exc:
            await session.rollback()
            return exc


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
        audit=_noop_audit,
    )
    assert second.job_id == first.job_id
    assert db_session.in_transaction()
    await db_session.commit()

    changed = _request(
        foundation,
        test_user.id,
        options=StudioRenderOptions(flatten_pdf=True),
    )
    with pytest.raises(StudioRenderServiceError) as conflict:
        await service.enqueue_test_render(
            changed,
            idempotency_key="tenant-idem-replay",
            audit=_noop_audit,
        )
    assert conflict.value.code == "idempotency_key_mismatch"
    assert db_session.in_transaction()
    await db_session.rollback()

    rate_limited = StudioRenderJobService(
        db_session,
        tenant_id=test_tenant.id,
        actor_user_id=test_user.id,
        renderer_manifest=_manifest(),
        enqueue_rate_limit=1,
    )
    with pytest.raises(StudioRenderServiceError) as rate:
        await rate_limited.enqueue_test_render(
            changed,
            idempotency_key="tenant-rate-limit",
            audit=_noop_audit,
        )
    assert rate.value.code == "studio_job_rate"
    await db_session.rollback()

    existing = await db_session.get(DurableJob, first.job_id)
    admission_bytes = existing.payload["admission_bytes"]
    await db_session.rollback()
    byte_limited = StudioRenderJobService(
        db_session,
        tenant_id=test_tenant.id,
        actor_user_id=test_user.id,
        renderer_manifest=_manifest(),
        queued_byte_limit=admission_bytes * 2 - 1,
    )
    with pytest.raises(StudioRenderServiceError) as queued_bytes:
        await byte_limited.enqueue_test_render(
            changed,
            idempotency_key="tenant-byte-limit",
            audit=_noop_audit,
        )
    assert queued_bytes.value.code == "studio_queued_bytes"
    await db_session.rollback()

    limited = StudioRenderJobService(
        db_session,
        tenant_id=test_tenant.id,
        actor_user_id=test_user.id,
        active_job_limit=1,
        renderer_manifest=_manifest(),
    )
    with pytest.raises(StudioRenderServiceError) as quota:
        await limited.enqueue_test_render(
            changed,
            idempotency_key="tenant-idem-quota",
            audit=_noop_audit,
        )
    assert quota.value.status_code == 429
    await db_session.rollback()


async def test_independent_sessions_serialize_idempotent_enqueue_and_conflict(
    db_session, test_engine, test_tenant, test_user
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    same_binding_id = uuid.uuid4()
    same_resolver = _AdmissionGateResolver(test_tenant.id, same_binding_id)
    same_request = _request(
        foundation, test_user.id, input_binding_id=same_binding_id
    )
    same_barrier = _AsyncBarrier(2)
    same_tasks = [
        asyncio.create_task(
            _independent_enqueue(
                factory,
                tenant_id=test_tenant.id,
                actor_user_id=test_user.id,
                request=same_request,
                key="concurrent-same-request",
                service_options={"input_binding_resolver": same_resolver},
                barrier=same_barrier,
            )
        )
        for _ in range(2)
    ]
    await asyncio.wait_for(same_resolver.started.wait(), timeout=2)
    await asyncio.sleep(0.05)
    assert same_resolver.calls == 1
    assert all(not task.done() for task in same_tasks)
    same_resolver.release.set()
    same_results = await asyncio.gather(*same_tasks)
    assert all(not isinstance(item, Exception) for item in same_results)
    assert len({item.job_id for item in same_results}) == 1

    conflict_binding_id = uuid.uuid4()
    conflict_resolver = _AdmissionGateResolver(
        test_tenant.id, conflict_binding_id
    )
    first_request = _request(
        foundation,
        test_user.id,
        options=StudioRenderOptions(flatten_pdf=True),
        input_binding_id=conflict_binding_id,
    )
    second_request = _request(
        foundation,
        test_user.id,
        options=StudioRenderOptions(preview_purpose="validation"),
        input_binding_id=conflict_binding_id,
    )
    conflict_barrier = _AsyncBarrier(2)
    conflict_tasks = [
        asyncio.create_task(
            _independent_enqueue(
                factory,
                tenant_id=test_tenant.id,
                actor_user_id=test_user.id,
                request=request,
                key="concurrent-conflict",
                service_options={"input_binding_resolver": conflict_resolver},
                barrier=conflict_barrier,
            )
        )
        for request in (first_request, second_request)
    ]
    await asyncio.wait_for(conflict_resolver.started.wait(), timeout=2)
    await asyncio.sleep(0.05)
    assert conflict_resolver.calls == 1
    assert all(not task.done() for task in conflict_tasks)
    conflict_resolver.release.set()
    conflicting = await asyncio.gather(*conflict_tasks)
    accepted = [item for item in conflicting if not isinstance(item, Exception)]
    rejected = [item for item in conflicting if isinstance(item, Exception)]
    assert len(accepted) == len(rejected) == 1
    assert rejected[0].code == "idempotency_key_mismatch"

    winner_row = await db_session.get(DurableJob, accepted[0].job_id)
    assert winner_row.payload["request_sha256"] in {
        first_request.request_sha256,
        second_request.request_sha256,
    }
    winning_request = (
        first_request
        if winner_row.payload["request_sha256"] == first_request.request_sha256
        else second_request
    )
    losing_request = (
        second_request if winning_request is first_request else first_request
    )
    await db_session.rollback()
    replay = await _independent_enqueue(
        factory,
        tenant_id=test_tenant.id,
        actor_user_id=test_user.id,
        request=winning_request,
        key="concurrent-conflict",
        service_options={"input_binding_resolver": conflict_resolver},
    )
    stable_conflict = await _independent_enqueue(
        factory,
        tenant_id=test_tenant.id,
        actor_user_id=test_user.id,
        request=losing_request,
        key="concurrent-conflict",
        service_options={"input_binding_resolver": conflict_resolver},
    )
    assert replay.job_id == accepted[0].job_id
    assert isinstance(stable_conflict, StudioRenderServiceError)
    assert stable_conflict.code == "idempotency_key_mismatch"


@pytest.mark.parametrize(
    ("service_options", "expected_code"),
    [
        ({"active_job_limit": 1}, "studio_job_quota"),
        ({"enqueue_rate_limit": 1}, "studio_job_rate"),
        ({"queued_byte_limit": len(b"# Studio source\n")}, "studio_queued_bytes"),
    ],
)
async def test_independent_sessions_cannot_oversubscribe_tenant_admission(
    db_session,
    test_engine,
    test_tenant,
    test_user,
    service_options,
    expected_code,
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    binding_id = uuid.uuid4()
    resolver = _AdmissionGateResolver(test_tenant.id, binding_id)
    request = _request(
        foundation, test_user.id, input_binding_id=binding_id
    )
    gated_options = dict(service_options)
    if expected_code == "studio_queued_bytes":
        gated_options["queued_byte_limit"] = (
            len(b"# Studio source\n")
            + resolver.resolved.object_ref.byte_size
            + request.render_options.max_output_bytes
        )
    gated_options["input_binding_resolver"] = resolver
    admission_barrier = _AsyncBarrier(2)
    tasks = [
        asyncio.create_task(
            _independent_enqueue(
                factory,
                tenant_id=test_tenant.id,
                actor_user_id=test_user.id,
                request=request,
                key=key,
                service_options=gated_options,
                barrier=admission_barrier,
            )
        )
        for key in ("concurrent-admission-one", "concurrent-admission-two")
    ]
    await asyncio.wait_for(resolver.started.wait(), timeout=2)
    await asyncio.sleep(0.05)
    assert resolver.calls == 1
    assert all(not task.done() for task in tasks)
    resolver.release.set()
    results = await asyncio.gather(*tasks)
    accepted = [item for item in results if not isinstance(item, Exception)]
    rejected = [item for item in results if isinstance(item, Exception)]
    assert len(accepted) == len(rejected) == 1
    assert rejected[0].code == expected_code


async def test_enqueue_refreshes_database_clock_after_blocking_binding_resolver(
    db_session, test_engine, test_tenant, test_user
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    binding_id = uuid.uuid4()
    binding_content = b"server-owned-binding"
    binding_sha256 = hashlib.sha256(binding_content).hexdigest()
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingResolver:
        async def resolve(self, tenant_id, requested_binding_id):
            assert tenant_id == test_tenant.id
            assert requested_binding_id == binding_id
            started.set()
            await release.wait()
            return StudioResolvedInputBinding(
                StudioObjectRef(
                    tenant_id=test_tenant.id,
                    object_key=(
                        f"studio-content/v1/{binding_sha256[:2]}/{binding_sha256}"
                    ),
                    sha256=binding_sha256,
                    byte_size=len(binding_content),
                    media_type="application/json",
                ),
                version=1,
            )

    service = StudioRenderJobService(
        db_session,
        tenant_id=test_tenant.id,
        actor_user_id=test_user.id,
        renderer_manifest=_manifest(),
        input_binding_resolver=BlockingResolver(),
    )
    enqueue = asyncio.create_task(
        service.enqueue_test_render(
            _request(
                foundation,
                test_user.id,
                input_binding_id=binding_id,
            ),
            idempotency_key="blocking-binding-clock",
            audit=_noop_audit,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as independent:
        boundary = await independent.scalar(select(func.clock_timestamp()))
    release.set()
    accepted = await enqueue
    await db_session.commit()
    row = await db_session.get(DurableJob, accepted.job_id)
    assert row.created_at >= boundary
    assert row.available_at >= boundary


async def test_independent_status_pollers_terminalize_expiry_once(
    db_session, test_engine, test_tenant, test_user
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    _, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "concurrent-expiry"
    )
    row = await db_session.get(DurableJob, accepted.job_id)
    row.payload = {
        **row.payload,
        "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    }
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def poll():
        async with factory() as session:
            service = StudioRenderJobService(
                session,
                tenant_id=test_tenant.id,
                actor_user_id=test_user.id,
                renderer_manifest=_manifest(),
            )
            return await run_studio_consumer_transaction(
                session, lambda: service.status(accepted.job_id)
            )

    statuses = await asyncio.gather(poll(), poll())
    assert [item.state for item in statuses] == ["failed", "failed"]
    assert statuses[0].completed_at == statuses[1].completed_at
    persisted = await db_session.get(DurableJob, accepted.job_id)
    await db_session.refresh(persisted)
    assert persisted.status == "failed"
    assert persisted.result == {"error_code": "expired"}


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
    other = StudioRenderJobService(
        db_session,
        tenant_id=other_tenant.id,
        actor_user_id=other_user.id,
        renderer_manifest=_manifest(),
    )
    with pytest.raises(StudioRenderServiceError) as status_error:
        await other.status(accepted.job_id)
    assert status_error.value.status_code == 404
    await db_session.rollback()
    with pytest.raises(StudioRenderServiceError) as cancel_error:
        await other.request_cancel(accepted.job_id, audit=_noop_audit)
    assert cancel_error.value.status_code == 404
    await db_session.rollback()
    assert await StudioRenderWorkerService(
        db_session, tenant_id=other_tenant.id
    ).claim(accepted.job_id, owner="worker-other") is None
    assert not db_session.in_transaction()
    row = await db_session.get(DurableJob, accepted.job_id)
    assert row.status == "pending"


async def test_available_artifact_foreign_keys_reject_cross_tenant_substitution(
    db_session, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "artifact-tenant-fks"
    )
    lease = await service.claim(accepted.job_id, owner="artifact-tenant-worker")
    object_store = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)
    output = object_store.put(
        test_tenant.id, b"tenant-fk-output", media_type="application/pdf"
    )
    artifact_id, _ = await service.adopt_output(
        lease,
        output,
        object_store=object_store,
        artifact_kind="test_render",
        runtime_manifest_sha256=lease.payload.runtime_manifest_sha256,
        artifact_ttl_seconds=300,
        **_geometry_kwargs(),
    )

    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Artifact FK other tenant",
        domain=f"artifact-fk-{uuid.uuid4()}.invalid",
        billing_tier="payg",
        is_active=True,
    )
    other_user = User(
        id=uuid.uuid4(),
        tenant_id=other_tenant.id,
        email=f"artifact-fk-{uuid.uuid4()}@example.invalid",
        full_name="Artifact FK Other User",
        role="admin",
        oauth_provider="google",
        oauth_subject=f"artifact-fk-{uuid.uuid4()}",
        is_active=True,
    )
    db_session.add_all([other_tenant, other_user])
    await db_session.commit()
    other = await _foundation(db_session, other_tenant, other_user)

    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    artifact.draft_id = other["draft_id"]
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    artifact.source_artifact_id = other["source_id"]
    artifact.source_sha256 = other["source_sha"]
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    artifact.requested_by_user_id = other_user.id
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


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
    assert not await service.renew_lease(first)
    second = await service.claim(accepted.job_id, owner="worker-same", lease_seconds=30)
    assert second is not None
    assert second.attempt == first.attempt + 1
    assert second.token != first.token
    assert not await service.renew_lease(first)
    assert await service.renew_lease(second)


async def test_blocked_lease_mutations_recheck_wall_clock_after_row_lock(
    db_session, test_engine, test_tenant, test_user
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "lease-lock-expiry"
    )
    lease = await service.claim(
        accepted.job_id, owner="lock-expiry-worker", lease_seconds=30
    )
    assert lease is not None
    row = await db_session.get(DurableJob, accepted.job_id)
    expires_at = await db_session.scalar(
        select(func.clock_timestamp())
    ) + timedelta(seconds=2)
    original_leased_at = row.leased_at
    row.payload = {**row.payload, "lease_expires_at": expires_at.isoformat()}
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async with factory() as blocker:
        await blocker.scalar(
            select(DurableJob)
            .where(DurableJob.id == accepted.job_id)
            .with_for_update()
        )

        async def mutate(kind):
            async with factory() as session:
                worker = StudioRenderWorkerService(
                    session, tenant_id=test_tenant.id
                )
                if kind == "renew":
                    return await worker.renew_lease(lease)
                if kind == "progress":
                    return await worker.update_progress(lease, 75)
                return await worker.fail_owned_job(
                    lease, "processor_timeout", retryable=True
                )

        tasks = [
            asyncio.create_task(mutate(kind))
            for kind in ("renew", "progress", "fail")
        ]
        await asyncio.sleep(0.05)
        assert all(not task.done() for task in tasks)
        await _wait_past_database_time(factory, expires_at)
        await blocker.rollback()

    assert await asyncio.gather(*tasks) == [False, False, False]
    persisted = await db_session.get(DurableJob, accepted.job_id)
    await db_session.refresh(persisted)
    assert persisted.status == "running"
    assert persisted.progress == 0
    assert persisted.result is None
    assert persisted.leased_at == original_leased_at
    assert datetime.fromisoformat(persisted.payload["lease_expires_at"]) == expires_at


async def test_claim_uses_wall_clock_when_transaction_predates_lease_expiry(
    db_session, test_engine, test_tenant, test_user
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "claim-fresh-clock"
    )
    first = await service.claim(
        accepted.job_id, owner="first-clock-worker", lease_seconds=30
    )
    assert first is not None
    row = await db_session.get(DurableJob, accepted.job_id)
    expires_at = await db_session.scalar(
        select(func.clock_timestamp())
    ) + timedelta(seconds=2)
    row.payload = {**row.payload, "lease_expires_at": expires_at.isoformat()}
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async with factory() as aged_session:
        await set_tenant_context(aged_session, str(test_tenant.id))
        transaction_started_at = await aged_session.scalar(
            select(func.transaction_timestamp())
        )
        assert transaction_started_at < expires_at
        await _wait_past_database_time(factory, expires_at)
        second = await StudioRenderWorkerService(
            aged_session, tenant_id=test_tenant.id
        ).claim(
            accepted.job_id,
            owner="second-clock-worker",
            lease_seconds=30,
        )

    assert second is not None
    assert second.attempt == first.attempt + 1
    assert second.token != first.token


async def test_pending_and_running_cancellation_transitions(
    db_session, test_tenant, test_user
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, pending = await _enqueue(
        db_session, test_tenant, test_user, foundation, "cancel-pending"
    )
    assert (
        await service.request_cancel(pending.job_id, audit=_noop_audit)
    ).state == "cancelled"
    assert db_session.in_transaction()
    await db_session.commit()

    _, running = await _enqueue(
        db_session, test_tenant, test_user, foundation, "cancel-running"
    )
    lease = await service.claim(running.job_id, owner="cancel-worker")
    assert lease is not None
    assert (
        await service.request_cancel(running.job_id, audit=_noop_audit)
    ).state == "cancel_requested"
    assert db_session.in_transaction()
    await db_session.commit()
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


async def test_retry_status_remains_reclaimable_without_terminal_result(
    db_session, test_tenant, test_user
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "retry-status-reclaim"
    )
    lease = await service.claim(accepted.job_id, owner="retry-worker")
    assert lease is not None
    assert await service.fail_owned_job(
        lease, "processor_timeout", retryable=True
    )
    row = await db_session.get(DurableJob, accepted.job_id)
    assert row.status == "pending"
    assert row.result is None
    retry_status = await service.status(accepted.job_id)
    assert retry_status.state == "retry_wait"
    assert retry_status.retry_at is not None
    assert retry_status.completed_at is None

    row = await db_session.get(DurableJob, accepted.job_id)
    row.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()
    reclaimed = await service.claim(accepted.job_id, owner="retry-worker-2")
    assert reclaimed is not None
    assert reclaimed.attempt == lease.attempt + 1
    running = await service.status(accepted.job_id)
    assert running.state == "running"
    assert running.error_code is None


async def test_inflight_cancellation_failure_reports_cancelled_without_poison(
    db_session, test_tenant, test_user
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "cancel-failure-status"
    )
    lease = await service.claim(accepted.job_id, owner="cancel-failure-worker")
    assert lease is not None
    requested = await service.request_cancel(
        accepted.job_id, audit=_noop_audit
    )
    assert requested.state == "cancel_requested"
    await db_session.commit()
    assert await service.fail_owned_job(lease, "cancelled", retryable=False)
    status = await service.status(accepted.job_id)
    assert status.state == "cancelled"
    assert status.error_code is None
    assert status.completed_at is not None
    row = await db_session.get(DurableJob, accepted.job_id)
    assert row.result is None


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
        db_session,
        tenant_id=test_tenant.id,
        actor_user_id=test_user.id,
        renderer_manifest=_manifest(),
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
            stale,
            idempotency_key="stale-source-binding",
            audit=_noop_audit,
        )
    assert stale_error.value.code == "stale_revision"
    assert stale_error.value.details == {
        "current_revision": 1,
        "current_etag": (
            f'"studio:{foundation["draft_id"]}:1:{foundation["identity_sha"]}"'
        ),
    }
    await db_session.rollback()

    snapshot = await db_session.get(
        StudioDraftSnapshot, foundation["snapshot_id"]
    )
    snapshot.payload = {**snapshot.payload, "format": "pdf"}
    await db_session.commit()
    with pytest.raises(StudioRenderServiceError) as snapshot_error:
        await service.enqueue_test_render(
            valid,
            idempotency_key="tampered-snapshot-payload",
            audit=_noop_audit,
        )
    assert snapshot_error.value.code == "stale_revision"
    await db_session.rollback()
    snapshot = await db_session.get(
        StudioDraftSnapshot, foundation["snapshot_id"]
    )
    snapshot.payload = {**snapshot.payload, "format": "markdown"}
    await db_session.commit()

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
    await db_session.commit()

    service, active = await _enqueue(
        db_session, test_tenant, test_user, foundation, "expire-active-lease"
    )
    active_lease = await service.claim(active.job_id, owner="expiring-worker")
    assert active_lease is not None
    active_row = await db_session.get(DurableJob, active.job_id)
    active_row.payload = {
        **active_row.payload,
        "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    }
    await db_session.commit()
    assert not await service.renew_lease(active_lease)
    active_status = await service.status(active.job_id)
    assert active_status.state == "failed"
    assert active_status.error_code == "expired"
    await db_session.commit()


async def test_current_and_stale_adoption_flush_exact_artifact_ids(
    db_session, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, current_job = await _enqueue(
        db_session, test_tenant, test_user, foundation, "adopt-current"
    )
    current_lease = await service.claim(current_job.job_id, owner="adopter-current")
    object_store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    wrong_media = object_store.put(
        test_tenant.id,
        b"{}",
        media_type="application/json",
    )
    with pytest.raises(StudioRenderServiceError) as tuple_error:
        await service.adopt_output(
            current_lease,
            wrong_media,
            object_store=object_store,
            artifact_kind="test_render",
            runtime_manifest_sha256=current_lease.payload.runtime_manifest_sha256,
            artifact_ttl_seconds=3600,
        **_geometry_kwargs(),
        )
    assert tuple_error.value.code == "validation_failed"
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
        runtime_manifest_sha256=current_lease.payload.runtime_manifest_sha256,
        artifact_ttl_seconds=3600,
        **_geometry_kwargs(),
    )
    assert outcome == "current_evidence"
    current_row = await db_session.get(DurableJob, current_job.job_id)
    current_artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    preferred = await db_session.get(
        StudioPreferredRenderEvidence,
        (test_tenant.id, foundation["draft_id"]),
    )
    current_draft = await db_session.get(StudioDraft, foundation["draft_id"])
    assert current_artifact.id == artifact_id
    assert preferred.artifact_id == artifact_id
    assert preferred.job_id == current_job.job_id
    assert current_row.result["artifact_id"] == str(artifact_id)
    assert current_draft.evidence_revision == 1
    assert current_artifact.artifact_page_count == 1
    assert current_artifact.document_page_count == 1
    current_status = await service.status(current_job.job_id)
    assert current_status.adopted_as_preferred_evidence is True
    assert current_status.is_preferred_evidence is True
    assert current_status.auto_open is True
    assert (
        current_status.effective_request_sha256
        == current_lease.payload.effective_request_sha256
    )
    cached = await service.find_cached_output(
        current_lease.payload.cache_key,
        object_store=object_store,
        max_bytes=1024,
    )
    assert cached is not None
    assert (
        cached.effective_request_sha256
        == current_lease.payload.effective_request_sha256
    )
    assert cached.artifact_page_count == 1
    assert cached.document_page_count == 1
    assert cached.geometry_manifest.sha256 == cached.geometry_manifest_sha256

    current_draft = await db_session.get(StudioDraft, foundation["draft_id"])
    current_draft.revision = 2
    current_draft.identity_sha256 = "d" * 64
    await db_session.commit()
    superseded_status = await service.status(current_job.job_id)
    assert superseded_status.adopted_as_preferred_evidence is True
    assert superseded_status.is_preferred_evidence is False
    assert superseded_status.auto_open is False
    await db_session.rollback()
    stale_service, stale_job = await _enqueue_with_stale_request(
        db_session, test_tenant, test_user, foundation
    )
    stale_lease = await stale_service.claim(stale_job.job_id, owner="adopter-stale")
    stale_id, stale_outcome = await stale_service.adopt_output(
        stale_lease,
        output,
        object_store=object_store,
        artifact_kind="test_render",
        runtime_manifest_sha256=stale_lease.payload.runtime_manifest_sha256,
        artifact_ttl_seconds=3600,
        **_geometry_kwargs(),
    )
    assert stale_outcome == "stale_output"
    stale_row = await db_session.get(DurableJob, stale_job.job_id)
    stale_artifact = await db_session.get(StudioRenderArtifact, stale_id)
    assert stale_artifact.adoption_outcome == "stale_output"
    assert stale_row.result["artifact_id"] == str(stale_id)


@pytest.mark.parametrize(
    "poison", ["missing", "ownership", "metadata"]
)
async def test_completed_status_revalidates_owned_live_artifact(
    db_session, test_tenant, test_user, tmp_path, poison
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session,
        test_tenant,
        test_user,
        foundation,
        f"completed-artifact-poison-{poison}",
    )
    lease = await service.claim(accepted.job_id, owner=f"poison-{poison}")
    object_store = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)
    output = object_store.put(
        test_tenant.id,
        f"poison-{poison}".encode(),
        media_type="application/pdf",
    )
    artifact_id, _ = await service.adopt_output(
        lease,
        output,
        object_store=object_store,
        artifact_kind="test_render",
        runtime_manifest_sha256=lease.payload.runtime_manifest_sha256,
        artifact_ttl_seconds=300,
        **_geometry_kwargs(),
    )
    row = await db_session.get(DurableJob, accepted.job_id)
    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    if poison == "missing":
        row.result = {**row.result, "artifact_id": str(uuid.uuid4())}
    elif poison == "ownership":
        _, other_job = await _enqueue(
            db_session,
            test_tenant,
            test_user,
            foundation,
            "completed-artifact-poison-owner",
        )
        artifact.job_id = other_job.job_id
    elif poison == "metadata":
        artifact.artifact_page_count = 2
    await db_session.commit()

    with pytest.raises(StudioRenderServiceError) as caught:
        await run_studio_consumer_transaction(
            db_session, lambda: service.status(accepted.job_id)
        )
    assert caught.value.code == "job_data_unavailable"
    assert caught.value.durable_state_changed is True
    await db_session.refresh(row)
    assert row.status == "failed"
    assert row.result == {"error_code": "job_data_unavailable"}


async def test_completed_status_preserves_expired_artifact_result_and_returns_410(
    db_session, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "completed-artifact-expiry"
    )
    lease = await service.claim(accepted.job_id, owner="artifact-expiry-worker")
    object_store = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)
    content = b"expiring-completed-output"
    output = object_store.put(
        test_tenant.id, content, media_type="application/pdf"
    )
    artifact_id, _ = await service.adopt_output(
        lease,
        output,
        object_store=object_store,
        artifact_kind="test_render",
        runtime_manifest_sha256=lease.payload.runtime_manifest_sha256,
        artifact_ttl_seconds=300,
        **_geometry_kwargs(),
    )
    available = await run_studio_consumer_transaction(
        db_session, lambda: service.status(accepted.job_id)
    )
    assert available.state == "completed"
    assert available.artifact_availability == "available"
    assert available.auto_open is True
    result = await run_studio_consumer_transaction(
        db_session, lambda: service.artifact_result(artifact_id)
    )
    assert result.artifact_id == artifact_id
    downloaded = await run_studio_consumer_transaction(
        db_session,
        lambda: service.artifact_content(
            artifact_id,
            object_store=object_store,
            max_bytes=4096,
        ),
    )
    assert downloaded.content == content
    assert downloaded.sha256 == output.sha256

    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    row = await db_session.get(DurableJob, accepted.job_id)
    artifact.content_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    row.result = {
        **row.result,
        "content_expires_at": artifact.content_expires_at.isoformat(),
    }
    immutable_result = dict(row.result)
    await db_session.commit()

    expired = await run_studio_consumer_transaction(
        db_session, lambda: service.status(accepted.job_id)
    )
    assert expired.state == "completed"
    assert expired.artifact_id == artifact_id
    assert expired.artifact_availability == "expired"
    assert expired.auto_open is False
    await db_session.refresh(row)
    assert row.status == "completed"
    assert row.result == immutable_result

    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    deleted_at = datetime.now(timezone.utc)
    artifact.storage_state = "deleted"
    artifact.delete_requested_at = deleted_at
    artifact.deleted_at = deleted_at
    object_store.delete(output)
    await db_session.commit()
    deleted = await run_studio_consumer_transaction(
        db_session, lambda: service.status(accepted.job_id)
    )
    assert deleted.state == "completed"
    assert deleted.artifact_availability == "expired"
    assert deleted.artifact_id == artifact_id

    retained_metadata = await run_studio_consumer_transaction(
        db_session, lambda: service.artifact_result(artifact_id)
    )
    assert retained_metadata.artifact_id == artifact_id
    assert retained_metadata.artifact_metadata_availability == "available"
    for operation in (
        lambda: service.artifact_content(
            artifact_id,
            object_store=object_store,
            max_bytes=4096,
        ),
    ):
        with pytest.raises(StudioRenderServiceError) as caught:
            await run_studio_consumer_transaction(db_session, operation)
        assert caught.value.status_code == 410
        assert caught.value.code == "artifact_expired"
        assert caught.value.message == "The Studio artifact has expired."
        assert caught.value.retryable is False
    await db_session.refresh(row)
    assert row.status == "completed"
    assert row.result == immutable_result


async def test_cache_rechecks_expiry_after_blocking_verified_read(
    db_session, test_engine, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "cache-expiry-fence"
    )
    lease = await service.claim(accepted.job_id, owner="cache-expiry-worker")
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)
    output = store.put(
        test_tenant.id, b"cache-expiry-output", media_type="application/pdf"
    )
    artifact_id, _ = await service.adopt_output(
        lease,
        output,
        object_store=store,
        artifact_kind="test_render",
        runtime_manifest_sha256=lease.payload.runtime_manifest_sha256,
        artifact_ttl_seconds=300,
        **_geometry_kwargs(),
    )
    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    artifact.content_expires_at = await db_session.scalar(
        select(func.clock_timestamp())
    ) + timedelta(seconds=3)
    expires_at = artifact.content_expires_at
    await db_session.commit()

    started = threading.Event()
    release = threading.Event()
    lookup = asyncio.create_task(
        service.find_cached_output(
            lease.payload.cache_key,
            object_store=_BlockingReadStore(store, started, release),
            max_bytes=4096,
        )
    )
    assert await asyncio.to_thread(started.wait, 2)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    while True:
        async with factory() as independent:
            current = await independent.scalar(select(func.clock_timestamp()))
        if current > expires_at:
            break
        await asyncio.sleep(min(0.05, (expires_at - current).total_seconds()))
    release.set()
    assert await lookup is None


async def test_content_read_releases_row_lock_and_rechecks_expiry_after_io(
    db_session, test_engine, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "content-expiry-fence"
    )
    lease = await service.claim(accepted.job_id, owner="content-expiry-worker")
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)
    output = store.put(
        test_tenant.id, b"content-expiry-output", media_type="application/pdf"
    )
    artifact_id, _ = await service.adopt_output(
        lease,
        output,
        object_store=store,
        artifact_kind="test_render",
        runtime_manifest_sha256=lease.payload.runtime_manifest_sha256,
        artifact_ttl_seconds=300,
        **_geometry_kwargs(),
    )
    expires_at = await db_session.scalar(
        select(func.clock_timestamp())
    ) + timedelta(seconds=2)
    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    row = await db_session.get(DurableJob, accepted.job_id)
    artifact.content_expires_at = expires_at
    row.result = {
        **row.result,
        "content_expires_at": expires_at.isoformat(),
    }
    await db_session.commit()
    started = threading.Event()
    release = threading.Event()
    blocking_store = _BlockingReadStore(store, started, release)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def download():
        async with factory() as session:
            consumer = StudioRenderJobService(
                session,
                tenant_id=test_tenant.id,
                actor_user_id=test_user.id,
                renderer_manifest=_manifest(),
            )
            return await run_studio_consumer_transaction(
                session,
                lambda: consumer.artifact_content(
                    artifact_id,
                    object_store=blocking_store,
                    max_bytes=4096,
                ),
            )

    task = asyncio.create_task(download())
    assert await asyncio.to_thread(started.wait, 2)
    locked = await asyncio.wait_for(
        db_session.scalar(
            select(StudioRenderArtifact)
            .where(StudioRenderArtifact.id == artifact_id)
            .with_for_update()
        ),
        timeout=1,
    )
    assert locked.id == artifact_id
    await db_session.rollback()
    await _wait_past_database_time(factory, expires_at)
    release.set()
    with pytest.raises(StudioRenderServiceError) as caught:
        await task
    assert caught.value.status_code == 410
    assert caught.value.code == "artifact_expired"


async def test_completed_status_rechecks_expiry_after_artifact_lock_wait(
    db_session, test_engine, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "status-expiry-lock-fence"
    )
    lease = await service.claim(accepted.job_id, owner="status-expiry-worker")
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)
    output = store.put(
        test_tenant.id, b"status-expiry-output", media_type="application/pdf"
    )
    artifact_id, _ = await service.adopt_output(
        lease,
        output,
        object_store=store,
        artifact_kind="test_render",
        runtime_manifest_sha256=lease.payload.runtime_manifest_sha256,
        artifact_ttl_seconds=300,
        **_geometry_kwargs(),
    )
    expires_at = await db_session.scalar(
        select(func.clock_timestamp())
    ) + timedelta(seconds=3)
    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    row = await db_session.get(DurableJob, accepted.job_id)
    artifact.content_expires_at = expires_at
    row.result = {
        **row.result,
        "content_expires_at": expires_at.isoformat(),
    }
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async with factory() as blocker:
        await blocker.scalar(
            select(StudioRenderArtifact)
            .where(StudioRenderArtifact.id == artifact_id)
            .with_for_update()
        )

        async def poll_status():
            async with factory() as session:
                consumer = StudioRenderJobService(
                    session,
                    tenant_id=test_tenant.id,
                    actor_user_id=test_user.id,
                    renderer_manifest=_manifest(),
                )
                return await run_studio_consumer_transaction(
                    session, lambda: consumer.status(accepted.job_id)
                )

        poll = asyncio.create_task(poll_status())
        await asyncio.sleep(0.05)
        assert not poll.done()
        async with factory() as clock_session:
            started_at = await clock_session.scalar(
                select(func.clock_timestamp())
            )
        assert started_at < expires_at
        while True:
            async with factory() as clock_session:
                current = await clock_session.scalar(
                    select(func.clock_timestamp())
                )
            if current > expires_at:
                break
            await asyncio.sleep(
                min(0.05, (expires_at - current).total_seconds())
            )
        await blocker.rollback()

    completed = await poll
    assert completed.state == "completed"
    assert completed.artifact_id == artifact_id
    assert completed.artifact_availability == "expired"
    assert completed.auto_open is False
    await db_session.refresh(row)
    assert row.status == "completed"
    assert row.result["artifact_id"] == str(artifact_id)


@pytest.mark.parametrize(
    ("tamper", "expected_code"),
    [
        ("snapshot_payload", "validation_failed"),
        ("source_content", "source_integrity_failed"),
    ],
)
async def test_adoption_rechecks_exact_snapshot_and_source_bindings(
    db_session, test_tenant, test_user, tmp_path, tamper, expected_code
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session,
        test_tenant,
        test_user,
        foundation,
        f"adoption-binding-{tamper}",
    )
    lease = await service.claim(accepted.job_id, owner=f"binding-{tamper}")
    snapshot = await db_session.get(
        StudioDraftSnapshot, foundation["snapshot_id"]
    )
    if tamper == "snapshot_payload":
        snapshot.payload = {**snapshot.payload, "format": "pdf"}
    else:
        source = await db_session.get(
            StudioSourceArtifact, foundation["source_id"]
        )
        source.content_bytes = b"# corrupted source bytes\n"
        source.byte_size = len(source.content_bytes)
    await db_session.commit()

    object_store = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)
    output = object_store.put(
        test_tenant.id,
        f"stale-{tamper}".encode(),
        media_type="application/pdf",
    )
    with pytest.raises(StudioRenderServiceError) as caught:
        await service.adopt_output(
            lease,
            output,
            object_store=object_store,
            artifact_kind="test_render",
            runtime_manifest_sha256=lease.payload.runtime_manifest_sha256,
            artifact_ttl_seconds=300,
        **_geometry_kwargs(),
        )
    assert caught.value.code == expected_code
    artifact = await db_session.scalar(
        select(StudioRenderArtifact).where(
            StudioRenderArtifact.job_id == accepted.job_id
        )
    )
    assert artifact is None
    row = await db_session.get(DurableJob, accepted.job_id)
    assert row.status == "failed"
    assert row.result == {"error_code": expected_code}
    draft = await db_session.get(StudioDraft, foundation["draft_id"])
    assert draft.evidence_revision is None


async def test_cancellation_rechecks_immutable_inputs_before_materialization(
    db_session, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "cancel-corrupt-input"
    )
    lease = await service.claim(accepted.job_id, owner="cancel-corrupt-worker")
    await service.request_cancel(accepted.job_id, audit=_noop_audit)
    snapshot = await db_session.get(
        StudioDraftSnapshot, foundation["snapshot_id"]
    )
    snapshot.payload = {**snapshot.payload, "identity_sha256": "f" * 64}
    await db_session.commit()
    object_store = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)
    output = object_store.put(
        test_tenant.id, b"cancel-corrupt-output", media_type="application/pdf"
    )

    with pytest.raises(StudioRenderServiceError) as caught:
        await service.adopt_output(
            lease,
            output,
            object_store=object_store,
            artifact_kind="test_render",
            runtime_manifest_sha256=lease.payload.runtime_manifest_sha256,
            artifact_ttl_seconds=300,
        **_geometry_kwargs(),
        )
    assert caught.value.code == "validation_failed"
    assert await db_session.scalar(
        select(StudioRenderArtifact).where(
            StudioRenderArtifact.job_id == accepted.job_id
        )
    ) is None


async def test_stale_draft_does_not_mask_immutable_corruption(
    db_session, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "stale-corrupt-input"
    )
    lease = await service.claim(accepted.job_id, owner="stale-corrupt-worker")
    draft = await db_session.get(StudioDraft, foundation["draft_id"])
    draft.revision = 2
    draft.identity_sha256 = "d" * 64
    snapshot = await db_session.get(
        StudioDraftSnapshot, foundation["snapshot_id"]
    )
    snapshot.payload = {**snapshot.payload, "format": "pdf"}
    await db_session.commit()
    object_store = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)
    output = object_store.put(
        test_tenant.id, b"stale-corrupt-output", media_type="application/pdf"
    )

    with pytest.raises(StudioRenderServiceError) as caught:
        await service.adopt_output(
            lease,
            output,
            object_store=object_store,
            artifact_kind="test_render",
            runtime_manifest_sha256=lease.payload.runtime_manifest_sha256,
            artifact_ttl_seconds=300,
        **_geometry_kwargs(),
        )
    assert caught.value.code == "validation_failed"
    assert await db_session.scalar(
        select(StudioRenderArtifact).where(
            StudioRenderArtifact.job_id == accepted.job_id
        )
    ) is None


async def test_adoption_rechecks_server_owned_input_binding_identity(
    db_session, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    binding_id = uuid.uuid4()
    binding_content = b'{"server_owned":true}'
    binding_sha256 = hashlib.sha256(binding_content).hexdigest()

    class MutableResolver:
        version = 1

        async def resolve(self, tenant_id, requested_binding_id):
            assert tenant_id == test_tenant.id
            assert requested_binding_id == binding_id
            return StudioResolvedInputBinding(
                StudioObjectRef(
                    tenant_id=tenant_id,
                    object_key=(
                        f"studio-content/v1/{binding_sha256[:2]}/{binding_sha256}"
                    ),
                    sha256=binding_sha256,
                    byte_size=len(binding_content),
                    media_type="application/json",
                ),
                version=self.version,
            )

    resolver = MutableResolver()
    consumer = StudioRenderJobService(
        db_session,
        tenant_id=test_tenant.id,
        actor_user_id=test_user.id,
        renderer_manifest=_manifest(),
        input_binding_resolver=resolver,
    )
    accepted = await consumer.enqueue_test_render(
        _request(
            foundation,
            test_user.id,
            input_binding_id=binding_id,
        ),
        idempotency_key="adoption-input-binding",
        audit=_noop_audit,
    )
    await db_session.commit()
    worker = StudioRenderWorkerService(
        db_session,
        tenant_id=test_tenant.id,
        input_binding_resolver=resolver,
    )
    lease = await worker.claim(accepted.job_id, owner="input-binding-worker")
    assert lease.payload.effective_request_sha256 == (
        canonical_effective_render_request_hash(
            request_sha256=lease.payload.request_sha256,
            input_binding_sha256=binding_sha256,
            input_binding_version=1,
        )
    )
    assert lease.payload.effective_request_sha256 != lease.payload.request_sha256
    admitted = await consumer.status(accepted.job_id)
    assert admitted.request_sha256 == lease.payload.request_sha256
    assert (
        admitted.effective_request_sha256
        == lease.payload.effective_request_sha256
    )
    resolver.version = 2
    object_store = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)
    output = object_store.put(
        test_tenant.id, b"binding-output", media_type="application/pdf"
    )

    with pytest.raises(StudioRenderServiceError) as caught:
        await worker.adopt_output(
            lease,
            output,
            object_store=object_store,
            artifact_kind="test_render",
            runtime_manifest_sha256=lease.payload.runtime_manifest_sha256,
            artifact_ttl_seconds=300,
        **_geometry_kwargs(),
        )
    assert caught.value.code == "validation_failed"
    assert await db_session.scalar(
        select(StudioRenderArtifact).where(
            StudioRenderArtifact.job_id == accepted.job_id
        )
    ) is None


async def test_phase2_rollback_recovery_rechecks_immutable_inputs(
    db_session, test_engine, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "recovery-corrupt-input"
    )
    lease = await service.claim(accepted.job_id, owner="recovery-corrupt-worker")
    object_store = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)
    output = object_store.put(
        test_tenant.id, b"recovery-corrupt-output", media_type="application/pdf"
    )
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def rollback_then_corrupt(phase2_service, *_args):
        await phase2_service.db.rollback()
        async with factory() as independent:
            snapshot = await independent.get(
                StudioDraftSnapshot, foundation["snapshot_id"]
            )
            snapshot.payload = {**snapshot.payload, "format": "pdf"}
            await independent.commit()
        return False

    with patch.object(
        StudioDraftService,
        "mark_render_evidence_if_current",
        new=rollback_then_corrupt,
    ):
        with pytest.raises(StudioRenderServiceError) as caught:
            await service.adopt_output(
                lease,
                output,
                object_store=object_store,
                artifact_kind="test_render",
                runtime_manifest_sha256=lease.payload.runtime_manifest_sha256,
                artifact_ttl_seconds=300,
        **_geometry_kwargs(),
            )
    assert caught.value.code == "validation_failed"
    assert await db_session.scalar(
        select(StudioRenderArtifact).where(
            StudioRenderArtifact.job_id == accepted.job_id
        )
    ) is None


async def test_adoption_uses_fresh_database_clock_after_blocking_storage_io(
    db_session, test_engine, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "fresh-adoption-clock"
    )
    lease = await service.claim(
        accepted.job_id, owner="fresh-clock-worker", lease_seconds=30
    )
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)
    output = store.put(
        test_tenant.id, b"fresh-clock-output", media_type="application/pdf"
    )
    started = threading.Event()
    release = threading.Event()
    blocking_store = _BlockingReadStore(store, started, release)
    adoption = asyncio.create_task(
        service.adopt_output(
            lease,
            output,
            object_store=blocking_store,
            artifact_kind="test_render",
            runtime_manifest_sha256=lease.payload.runtime_manifest_sha256,
            artifact_ttl_seconds=300,
        **_geometry_kwargs(),
        )
    )
    assert await asyncio.to_thread(started.wait, 2)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as independent:
        row = await independent.get(DurableJob, accepted.job_id)
        row.payload = {
            **row.payload,
            "lease_expires_at": (
                datetime.now(timezone.utc) + timedelta(milliseconds=200)
            ).isoformat(),
        }
        await independent.commit()
    await asyncio.sleep(0.35)
    release.set()
    with pytest.raises(StudioRenderServiceError) as caught:
        await adoption
    assert caught.value.code == "lease_lost"
    artifact = await db_session.scalar(
        select(StudioRenderArtifact).where(
            StudioRenderArtifact.job_id == accepted.job_id
        )
    )
    assert artifact is None


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
    await service.request_cancel(accepted.job_id, audit=_noop_audit)
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
        runtime_manifest_sha256=lease.payload.runtime_manifest_sha256,
        artifact_ttl_seconds=3600,
        **_geometry_kwargs(),
    )
    assert outcome == "cancelled_output"
    status = await service.status(accepted.job_id)
    assert status.state == "completed"
    assert status.artifact_id == artifact_id
    assert status.adoption_outcome == "cancelled_output"
    await db_session.commit()
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
    font_pack = tmp_path / "fonts.bundle"
    rasterizer = tmp_path / "rasterizer.bin"
    converter = tmp_path / "converter.bin"
    validator = tmp_path / "validator.bin"
    launcher.write_bytes(b"trusted sandbox")
    executable.write_bytes(b"renderer")
    font_pack.write_bytes(b"fonts")
    rasterizer.write_bytes(b"rasterizer")
    converter.write_bytes(b"converter")
    validator.write_bytes(b"validator")
    registry = StudioIsolationRegistry(
        [
            StudioIsolationProfile(
                profile_id="studio-worker-test-v1",
                runtime_root=tmp_path.absolute(),
                launcher=launcher.absolute(),
                launcher_sha256=hashlib.sha256(launcher.read_bytes()).hexdigest(),
                executable=executable.absolute(),
                executable_sha256=hashlib.sha256(
                    executable.read_bytes()
                ).hexdigest(),
                font_pack=font_pack.absolute(),
                font_pack_sha256=hashlib.sha256(font_pack.read_bytes()).hexdigest(),
                renderer_version="1.0.0",
                rasterizer=rasterizer.absolute(),
                rasterizer_version="1.0.0",
                rasterizer_sha256=hashlib.sha256(
                    rasterizer.read_bytes()
                ).hexdigest(),
                converter=converter.absolute(),
                converter_version="1.0.0",
                converter_sha256=hashlib.sha256(
                    converter.read_bytes()
                ).hexdigest(),
                validator=validator.absolute(),
                validator_version="1.0.0",
                validator_sha256=hashlib.sha256(
                    validator.read_bytes()
                ).hexdigest(),
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


async def test_worker_rehashes_snapshot_payload_before_processor_use(
    db_session, test_engine, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "worker-snapshot-rehash"
    )
    lease = await service.claim(accepted.job_id, owner="snapshot-rehash-worker")
    snapshot = await db_session.get(
        StudioDraftSnapshot, foundation["snapshot_id"]
    )
    snapshot.payload = {**snapshot.payload, "identity_sha256": "f" * 64}
    await db_session.commit()

    worker = object.__new__(StudioRenderWorker)
    worker.session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    worker.object_store = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)
    worker.input_bindings = None
    worker.processor_timeout_seconds = 5
    with pytest.raises(StudioRenderServiceError) as caught:
        await worker._load_inputs(lease)
    assert caught.value.code == "validation_failed"


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
            runtime_manifest_sha256=lease.payload.runtime_manifest_sha256,
            artifact_ttl_seconds=300,
        **_geometry_kwargs(),
        )
        artifact_ids.append(artifact_id)
    for artifact_id in artifact_ids:
        artifact = await db_session.get(StudioRenderArtifact, artifact_id)
        artifact.adoption_outcome = "stale_output"
        artifact.content_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    async def not_held(_candidate):
        return False

    retention = StudioArtifactRetentionService(
        db_session,
        tenant_id=test_tenant.id,
        object_store=object_store,
        legal_hold_check=not_held,
        current_evidence_check=lambda *_args: not_held(None),
    )
    assert (await retention.delete_if_eligible(artifact_ids[0])).eligible
    assert object_store.read(output) == b"shared-cas-output"
    assert (await retention.delete_if_eligible(artifact_ids[1])).eligible
    with pytest.raises(StudioStorageError) as missing:
        object_store.read(output)
    assert missing.value.code == "object_missing"


async def test_retention_refreshes_clock_after_hold_and_storage_boundaries(
    db_session, test_engine, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)
    output = store.put(
        test_tenant.id, b"retention-clock-output", media_type="application/pdf"
    )
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "retention-clock"
    )
    lease = await service.claim(accepted.job_id, owner="retention-clock-worker")
    artifact_id, _ = await service.adopt_output(
        lease,
        output,
        object_store=store,
        artifact_kind="test_render",
        runtime_manifest_sha256=lease.payload.runtime_manifest_sha256,
        artifact_ttl_seconds=300,
        **_geometry_kwargs(),
    )
    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    artifact.adoption_outcome = "stale_output"
    artifact.content_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    hold_started = asyncio.Event()
    hold_release = asyncio.Event()
    delete_started = threading.Event()
    delete_release = threading.Event()

    async def blocking_not_held(_candidate):
        hold_started.set()
        await hold_release.wait()
        return False

    async def not_current(_tenant_id, _artifact_id):
        return False

    retention = StudioArtifactRetentionService(
        db_session,
        tenant_id=test_tenant.id,
        object_store=_BlockingDeleteStore(store, delete_started, delete_release),
        legal_hold_check=blocking_not_held,
        current_evidence_check=not_current,
    )
    cleanup = asyncio.create_task(retention.delete_if_eligible(artifact_id))
    await asyncio.wait_for(hold_started.wait(), timeout=2)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as independent:
        hold_boundary = await independent.scalar(select(func.clock_timestamp()))
    hold_release.set()
    assert await asyncio.to_thread(delete_started.wait, 2)
    async with factory() as independent:
        delete_boundary = await independent.scalar(select(func.clock_timestamp()))
    delete_release.set()
    assert (await cleanup).eligible is True
    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    await db_session.refresh(artifact)
    assert artifact.delete_requested_at >= hold_boundary
    assert artifact.deleted_at >= delete_boundary


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
        runtime_manifest_sha256=lease.payload.runtime_manifest_sha256,
        artifact_ttl_seconds=300,
        **_geometry_kwargs(),
    )
    now = datetime.now(timezone.utc)
    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    artifact.adoption_outcome = "stale_output"
    artifact.content_expires_at = now - timedelta(seconds=1)
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
        current_evidence_check=lambda *_args: not_held(None),
    )
    restored = await retention.delete_if_eligible(artifact_id)
    assert restored.reason == "legal_hold"
    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    assert artifact.storage_state == "active"
    assert artifact.delete_requested_at is None

    artifact.storage_state = "delete_pending"
    artifact.delete_requested_at = now
    object_store.delete(output)
    await db_session.commit()
    pending = await retention.delete_if_eligible(artifact_id)
    assert pending.reason == "storage_delete_pending"
    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    assert artifact.storage_state == "delete_pending"


@pytest.mark.parametrize("exit_mode", ["timeout", "cancel"])
async def test_late_stage_keeps_object_fence_until_cross_worker_adoption_is_safe(
    db_session, test_engine, test_tenant, test_user, tmp_path, exit_mode
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    first_service, first_job = await _enqueue(
        db_session, test_tenant, test_user, foundation, f"late-stage-first-{exit_mode}"
    )
    second_service, second_job = await _enqueue(
        db_session, test_tenant, test_user, foundation, f"late-stage-second-{exit_mode}"
    )
    first_lease = await first_service.claim(
        first_job.job_id, owner=f"late-stage-first-{exit_mode}"
    )
    second_lease = await second_service.claim(
        second_job.job_id, owner=f"late-stage-second-{exit_mode}"
    )
    assert first_lease is not None and second_lease is not None
    content = f"late-stage-{exit_mode}".encode()
    content_sha256 = hashlib.sha256(content).hexdigest()
    delegate = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)
    started = threading.Event()
    release = threading.Event()
    object_store = _FirstBlockingStageStore(delegate, started, release)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def adopt(lease):
        async with factory() as session:
            worker = StudioRenderWorkerService(
                session, tenant_id=test_tenant.id
            )
            return await worker.stage_and_adopt_output(
                lease,
                content,
                object_store=object_store,
                media_type="application/pdf",
                content_sha256=content_sha256,
                artifact_kind="test_render",
                runtime_manifest_sha256=lease.payload.runtime_manifest_sha256,
                artifact_ttl_seconds=300,
        **_geometry_kwargs(),
            )

    timeout = 0.05 if exit_mode == "timeout" else 30.0
    with patch(
        "app.services.studio_render_jobs._STORAGE_STAGE_TIMEOUT_SECONDS",
        timeout,
    ):
        first = asyncio.create_task(adopt(first_lease))
        assert await asyncio.to_thread(started.wait, 2)
        if exit_mode == "cancel":
            first.cancel()
        second = asyncio.create_task(adopt(second_lease))
        await asyncio.sleep(0.1)
        assert not first.done()
        assert not second.done()
        release.set()
        if exit_mode == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            with pytest.raises(TimeoutError):
                await first
        artifact_id, _outcome, staged = await second

    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    assert artifact.job_id == second_job.job_id
    assert artifact.storage_state == "active"
    assert artifact.object_key == staged.object_ref.object_key
    assert delegate.read(staged.object_ref) == content


async def test_cancelled_staged_delete_holds_fence_until_republish_is_durable(
    db_session, test_engine, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "late-staged-delete"
    )
    lease = await service.claim(
        accepted.job_id, owner="late-staged-delete-worker"
    )
    assert lease is not None
    content = b"late-staged-delete-output"
    content_sha256 = hashlib.sha256(content).hexdigest()
    delegate = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)
    orphan = delegate.stage(
        test_tenant.id,
        content,
        job_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        reconcile_after=datetime.now(timezone.utc) - timedelta(minutes=1),
        media_type="application/pdf",
        expected_sha256=content_sha256,
    )
    delete_started = threading.Event()
    delete_release = threading.Event()
    object_store = _FirstBlockingStagedDeleteStore(
        delegate, delete_started, delete_release
    )
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def not_held(_candidate):
        return False

    async def not_current(_tenant_id, _artifact_id):
        return False

    async def reconcile():
        async with factory() as session:
            return await StudioStagedReceiptReconciler(
                session,
                tenant_id=test_tenant.id,
                object_store=object_store,
                legal_hold_check=not_held,
                current_evidence_check=not_current,
            ).reconcile_batch(limit=10)

    async def adopt():
        async with factory() as session:
            return await StudioRenderWorkerService(
                session, tenant_id=test_tenant.id
            ).stage_and_adopt_output(
                lease,
                content,
                object_store=object_store,
                media_type="application/pdf",
                content_sha256=content_sha256,
                artifact_kind="test_render",
                runtime_manifest_sha256=lease.payload.runtime_manifest_sha256,
                artifact_ttl_seconds=300,
        **_geometry_kwargs(),
            )

    cleanup = asyncio.create_task(reconcile())
    assert await asyncio.to_thread(delete_started.wait, 2)
    cleanup.cancel()
    replacement = asyncio.create_task(adopt())
    await asyncio.sleep(0.05)
    assert not cleanup.done()
    assert not replacement.done()
    delete_release.set()
    with pytest.raises(asyncio.CancelledError):
        await cleanup
    artifact_id, _outcome, staged = await replacement

    artifact = await db_session.get(StudioRenderArtifact, artifact_id)
    assert artifact.job_id == accepted.job_id
    assert artifact.object_key == orphan.object_ref.object_key
    assert artifact.object_key == staged.object_ref.object_key
    assert artifact.storage_state == "active"
    assert delegate.read(staged.object_ref) == content


async def test_production_stage_reconciler_covers_pre_adoption_and_post_commit_crashes(
    db_session, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    object_store = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)

    service, abandoned_job = await _enqueue(
        db_session, test_tenant, test_user, foundation, "stage-abandoned"
    )
    abandoned_lease = await service.claim(
        abandoned_job.job_id, owner="stage-abandoned-worker"
    )
    abandoned_content = b"abandoned-output"
    abandoned_stage = object_store.stage(
        test_tenant.id,
        abandoned_content,
        job_id=abandoned_job.job_id,
        lease_token=abandoned_lease.token,
        reconcile_after=datetime.now(timezone.utc) - timedelta(seconds=1),
        media_type="application/pdf",
        expected_sha256=hashlib.sha256(abandoned_content).hexdigest(),
    )

    async def not_held(_candidate):
        return False

    async def not_current(_tenant_id, _artifact_id):
        return False

    reconciler = StudioStagedReceiptReconciler(
        db_session,
        tenant_id=test_tenant.id,
        object_store=object_store,
        legal_hold_check=not_held,
        current_evidence_check=not_current,
    )
    kept = await reconciler.reconcile_batch(limit=10)
    assert [(item.action, item.reason) for item in kept] == [
        ("kept", "lease_active")
    ]

    abandoned_row = await db_session.get(DurableJob, abandoned_job.job_id)
    abandoned_row.payload = {
        **abandoned_row.payload,
        "lease_expires_at": (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat(),
    }
    await db_session.commit()
    deleted = await reconciler.reconcile_batch(limit=10)
    assert [(item.action, item.reason) for item in deleted] == [
        ("deleted", "unreferenced")
    ]
    with pytest.raises(StudioStorageError):
        object_store.read(abandoned_stage.object_ref)

    _, committed_job = await _enqueue(
        db_session, test_tenant, test_user, foundation, "stage-committed"
    )
    committed_lease = await service.claim(
        committed_job.job_id, owner="stage-committed-worker"
    )
    committed_content = b"committed-output"
    committed_stage = object_store.stage(
        test_tenant.id,
        committed_content,
        job_id=committed_job.job_id,
        lease_token=committed_lease.token,
        reconcile_after=datetime.now(timezone.utc) - timedelta(seconds=1),
        media_type="application/pdf",
        expected_sha256=hashlib.sha256(committed_content).hexdigest(),
    )
    await service.adopt_output(
        committed_lease,
        committed_stage.object_ref,
        object_store=object_store,
        artifact_kind="test_render",
        runtime_manifest_sha256=committed_lease.payload.runtime_manifest_sha256,
        artifact_ttl_seconds=300,
        **_geometry_kwargs(),
    )
    acknowledged = await reconciler.reconcile_batch(limit=10)
    assert [(item.action, item.reason) for item in acknowledged] == [
        ("acknowledged", "artifact_referenced")
    ]
    assert object_store.read(committed_stage.object_ref) == committed_content


async def test_force_rls_rebinds_cache_retention_and_worker_transactions(
    db_session, test_engine, test_tenant, test_user, tmp_path
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    object_store = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)

    service, cache_job = await _enqueue(
        db_session, test_tenant, test_user, foundation, "rls-cache"
    )
    cache_lease = await service.claim(cache_job.job_id, owner="rls-cache-worker")
    cache_ref = object_store.put(
        test_tenant.id, b"rls-cache-output", media_type="application/pdf"
    )
    await service.adopt_output(
        cache_lease,
        cache_ref,
        object_store=object_store,
        artifact_kind="test_render",
        runtime_manifest_sha256=cache_lease.payload.runtime_manifest_sha256,
        artifact_ttl_seconds=3600,
        **_geometry_kwargs(),
    )

    _, cleanup_job = await _enqueue(
        db_session, test_tenant, test_user, foundation, "rls-cleanup"
    )
    cleanup_lease = await service.claim(
        cleanup_job.job_id, owner="rls-cleanup-worker"
    )
    cleanup_ref = object_store.put(
        test_tenant.id, b"rls-cleanup-output", media_type="application/pdf"
    )
    cleanup_artifact_id, _ = await service.adopt_output(
        cleanup_lease,
        cleanup_ref,
        object_store=object_store,
        artifact_kind="test_render",
        runtime_manifest_sha256=cleanup_lease.payload.runtime_manifest_sha256,
        artifact_ttl_seconds=300,
        **_geometry_kwargs(),
    )
    cleanup_artifact = await db_session.get(
        StudioRenderArtifact, cleanup_artifact_id
    )
    cleanup_artifact.adoption_outcome = "stale_output"
    cleanup_artifact.content_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    _, worker_job = await _enqueue(
        db_session, test_tenant, test_user, foundation, "rls-worker"
    )
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="RLS other tenant",
        domain=f"rls-other-{uuid.uuid4()}.invalid",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(other_tenant)
    await db_session.flush()
    worker_row = await db_session.get(DurableJob, worker_job.job_id)
    hidden_job = DurableJob(
        tenant_id=other_tenant.id,
        kind=worker_row.kind,
        idempotency_key="studio-render:" + "f" * 64,
        payload=dict(worker_row.payload),
        status="pending",
        available_at=datetime.now(timezone.utc),
    )
    db_session.add(hidden_job)
    await db_session.commit()

    tables = (
        "durable_jobs",
        "studio_render_artifacts",
        "studio_preferred_render_evidence",
    )
    original_rls = {}
    async with test_engine.begin() as conn:
        for table in tables:
            original_rls[table] = tuple(
                (
                    await conn.execute(
                        text(
                            "SELECT relrowsecurity, relforcerowsecurity "
                            "FROM pg_class WHERE oid = CAST(:table AS regclass)"
                        ),
                        {"table": table},
                    )
                ).one()
            )
            await conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
            await conn.execute(
                text(f"DROP POLICY IF EXISTS studio_render_probe_{table} ON {table}")
            )
            await conn.execute(
                text(
                    f"CREATE POLICY studio_render_probe_{table} ON {table} "
                    "USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid) "
                    "WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)"
                )
            )
        await conn.execute(
            text(
                f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{_RLS_ROLE}') "
                f"THEN CREATE ROLE {_RLS_ROLE} LOGIN PASSWORD '{_RLS_PASSWORD}' "
                "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE; END IF; END $$"
            )
        )
        await conn.execute(
            text(f"ALTER ROLE {_RLS_ROLE} PASSWORD '{_RLS_PASSWORD}'")
        )
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {_RLS_ROLE}"))
        for table in tables:
            await conn.execute(
                text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {_RLS_ROLE}")
            )

    role_url = (
        make_url(_TEST_DB_URL)
        .set(username=_RLS_ROLE, password=_RLS_PASSWORD)
        .render_as_string(hide_password=False)
    )
    role_engine = create_async_engine(role_url, echo=False)
    try:
        factory = async_sessionmaker(role_engine, expire_on_commit=False)
        async with factory() as session:
            flags = (
                await session.execute(
                    text(
                        "SELECT rolsuper, rolbypassrls FROM pg_roles "
                        "WHERE rolname = current_user"
                    )
                )
            ).one()
            assert tuple(flags) == (False, False)
            await set_tenant_context(session, str(test_tenant.id))

            worker_service = StudioRenderWorkerService(
                session, tenant_id=test_tenant.id
            )
            lease = await worker_service.claim(
                worker_job.job_id, owner="rls-bound-worker", lease_seconds=30
            )
            assert lease is not None
            assert await worker_service.renew_lease(lease)
            assert (
                await worker_service.claim(
                    hidden_job.id, owner="cross-tenant-worker", lease_seconds=30
                )
                is None
            )

            cached = await worker_service.find_cached_output(
                cache_lease.payload.cache_key,
                object_store=object_store,
                max_bytes=4096,
            )
            assert cached is not None
            assert cached.object_ref == cache_ref
            await session.rollback()

            async def not_held(_candidate):
                return False

            async def not_current(_tenant_id, _artifact_id):
                return False

            retention = StudioArtifactRetentionService(
                session,
                tenant_id=test_tenant.id,
                object_store=object_store,
                legal_hold_check=not_held,
                current_evidence_check=not_current,
            )
            decision = await retention.delete_if_eligible(cleanup_artifact_id)
            assert decision.eligible is True
            with pytest.raises(StudioStorageError):
                object_store.read(cleanup_ref)
    finally:
        await role_engine.dispose()
        async with test_engine.begin() as conn:
            for table in tables:
                await conn.execute(
                    text(
                        f"DROP POLICY IF EXISTS studio_render_probe_{table} ON {table}"
                    )
                )
                row_security, force_security = original_rls[table]
                await conn.execute(
                    text(
                        f"ALTER TABLE {table} "
                        f"{'ENABLE' if row_security else 'DISABLE'} ROW LEVEL SECURITY"
                    )
                )
                await conn.execute(
                    text(
                        f"ALTER TABLE {table} "
                        f"{'FORCE' if force_security else 'NO FORCE'} ROW LEVEL SECURITY"
                    )
                )
            await conn.execute(text(f"DROP OWNED BY {_RLS_ROLE}"))
            await conn.execute(text(f"DROP ROLE IF EXISTS {_RLS_ROLE}"))


async def test_consumer_audit_transaction_is_atomic_and_replay_safe(
    db_session, test_tenant, test_user
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service = StudioRenderJobService(
        db_session,
        tenant_id=test_tenant.id,
        actor_user_id=test_user.id,
        renderer_manifest=_manifest(),
    )

    async def audit(event, job_id):
        db_session.add(
            StudioDraftAuditEvent(
                tenant_id=test_tenant.id,
                draft_id=foundation["draft_id"],
                event_type=event,
                revision=1,
                actor_user_id=test_user.id,
                detail={"job_id": str(job_id)},
            )
        )
        await db_session.flush()

    request = _request(foundation, test_user.id)
    with pytest.raises(TypeError):
        await service.enqueue_test_render(
            request,
            idempotency_key="consumer-audit-omitted",
        )
    assert not db_session.in_transaction()
    with pytest.raises(StudioRenderServiceError) as unavailable:
        await service.enqueue_test_render(
            request,
            idempotency_key="consumer-audit-none",
            audit=None,
        )
    assert unavailable.value.code == "audit_unavailable"
    assert not db_session.in_transaction()

    accepted = await run_studio_consumer_transaction(
        db_session,
        lambda: service.enqueue_test_render(
            request,
            idempotency_key="consumer-audit-success",
            audit=audit,
        ),
    )
    events = list(
        (
            await db_session.scalars(
                select(StudioDraftAuditEvent).where(
                    StudioDraftAuditEvent.event_type == "studio_render_enqueued"
                )
            )
        ).all()
    )
    assert [event.detail["job_id"] for event in events] == [str(accepted.job_id)]
    await db_session.rollback()

    replay_calls = []

    async def replay_audit(event, job_id):
        replay_calls.append((event, job_id))

    replayed = await run_studio_consumer_transaction(
        db_session,
        lambda: service.enqueue_test_render(
            request,
            idempotency_key="consumer-audit-success",
            audit=replay_audit,
        ),
    )
    assert replayed.job_id == accepted.job_id
    assert replay_calls == []

    async def failing_audit(event, job_id):
        await audit(event, job_id)
        raise RuntimeError("audit sink unavailable")

    with pytest.raises(RuntimeError, match="audit sink unavailable"):
        await run_studio_consumer_transaction(
            db_session,
            lambda: service.enqueue_test_render(
                request,
                idempotency_key="consumer-audit-rollback",
                audit=failing_audit,
            ),
        )
    rolled_back = await db_session.scalar(
        select(DurableJob).where(
            DurableJob.tenant_id == test_tenant.id,
            DurableJob.idempotency_key.endswith(
                hashlib.sha256(b"consumer-audit-rollback").hexdigest()
            ),
        )
    )
    assert rolled_back is None
    await db_session.rollback()

    lease = await StudioRenderWorkerService(
        db_session, tenant_id=test_tenant.id
    ).claim(accepted.job_id, owner="consumer-cancel-worker")
    assert lease is not None
    with pytest.raises(TypeError):
        await service.request_cancel(accepted.job_id)
    with pytest.raises(StudioRenderServiceError) as cancel_unavailable:
        await service.request_cancel(accepted.job_id, audit=None)
    assert cancel_unavailable.value.code == "audit_unavailable"
    with pytest.raises(RuntimeError, match="audit sink unavailable"):
        await run_studio_consumer_transaction(
            db_session,
            lambda: service.request_cancel(
                accepted.job_id,
                audit=failing_audit,
            ),
        )
    row = await db_session.get(DurableJob, accepted.job_id)
    assert row.status == "running"
    await db_session.rollback()

    cancelled = await run_studio_consumer_transaction(
        db_session,
        lambda: service.request_cancel(
            accepted.job_id,
            audit=audit,
        ),
    )
    assert cancelled.state == "cancel_requested"
    cancel_events = list(
        (
            await db_session.scalars(
                select(StudioDraftAuditEvent).where(
                    StudioDraftAuditEvent.event_type
                    == "studio_render_cancel_requested"
                )
            )
        ).all()
    )
    assert [event.detail["job_id"] for event in cancel_events] == [
        str(accepted.job_id)
    ]
    await db_session.rollback()

    cancel_replay_calls = []

    async def cancel_replay_audit(event, job_id):
        cancel_replay_calls.append((event, job_id))

    replayed_cancel = await run_studio_consumer_transaction(
        db_session,
        lambda: service.request_cancel(
            accepted.job_id,
            audit=cancel_replay_audit,
        ),
    )
    assert replayed_cancel.state == "cancel_requested"
    assert cancel_replay_calls == []


async def test_consumer_transaction_persists_sanitized_poison_terminalization(
    db_session, test_tenant, test_user
):
    foundation = await _foundation(db_session, test_tenant, test_user)
    service, accepted = await _enqueue(
        db_session, test_tenant, test_user, foundation, "poison-terminalization"
    )
    row = await db_session.get(DurableJob, accepted.job_id)
    row.payload = {"contract_version": 1, "raw_exception": "C:/private/source"}
    await db_session.commit()

    with pytest.raises(StudioRenderServiceError) as caught:
        await run_studio_consumer_transaction(
            db_session,
            lambda: service.status(accepted.job_id),
        )
    assert caught.value.code == "job_data_unavailable"
    assert caught.value.durable_state_changed is True
    await db_session.refresh(row)
    assert row.status == "failed"
    assert row.result == {"error_code": "job_data_unavailable"}
    assert "private" not in str(row.result).lower()

    _, kind_job = await _enqueue(
        db_session,
        test_tenant,
        test_user,
        foundation,
        "poison-kind-mismatch",
    )
    kind_row = await db_session.get(DurableJob, kind_job.job_id)
    kind_row.kind = "studio_template_ocr"
    await db_session.commit()
    with pytest.raises(StudioRenderServiceError) as kind_error:
        await run_studio_consumer_transaction(
            db_session,
            lambda: service.status(kind_job.job_id),
        )
    assert kind_error.value.code == "job_data_unavailable"
    await db_session.refresh(kind_row)
    assert kind_row.status == "failed"
    assert kind_row.result == {"error_code": "job_data_unavailable"}

    _, admission_poison = await _enqueue(
        db_session,
        test_tenant,
        test_user,
        foundation,
        "poison-admission-source",
    )
    admission_row = await db_session.get(DurableJob, admission_poison.job_id)
    admission_row.payload = {"raw_document": "never expose this value"}
    await db_session.commit()
    with pytest.raises(StudioRenderServiceError) as admission_error:
        await run_studio_consumer_transaction(
            db_session,
            lambda: service.enqueue_test_render(
                _request(foundation, test_user.id),
                idempotency_key="poison-admission-followup",
                audit=_noop_audit,
            ),
        )
    assert admission_error.value.durable_state_changed is True
    await db_session.refresh(admission_row)
    assert admission_row.status == "failed"
    assert admission_row.result == {"error_code": "job_data_unavailable"}
    assert admission_row.payload == {}
