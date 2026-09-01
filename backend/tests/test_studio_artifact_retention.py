"""Database-free retention and legal-hold policy checks."""

import asyncio
import hashlib
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.studio_artifact_retention import (
    StudioArtifactRetentionService,
    StudioCleanupCandidate,
    StudioDurableJobCleanupCandidate,
    StudioRenderMaintenance,
    StudioStagedReceiptReconciler,
    StudioStagedReconciliationDecision,
    bounded_cleanup_candidates,
    bounded_durable_job_cleanup,
    cleanup_decision,
    durable_job_cleanup_decision,
    metadata_is_retained,
    reconcile_staged_batch,
)
from app.services.studio_object_storage import (
    LocalStudioObjectStore,
    StudioObjectRef,
    StudioStagedObject,
)

pytestmark = pytest.mark.asyncio


async def test_maintenance_accepts_the_configured_tenant_scan_bound():
    maintenance = StudioRenderMaintenance(
        object(), object_store=object(), tenant_batch_size=500
    )
    assert maintenance.tenant_batch_size == 500
    with pytest.raises(ValueError, match="tenant batch"):
        StudioRenderMaintenance(object(), object_store=object(), tenant_batch_size=501)


def _candidate(now, **updates):
    values = {
        "tenant_id": uuid.uuid4(),
        "artifact_id": uuid.uuid4(),
        "job_terminal": True,
        "adoption_outcome": "stale_output",
        "retention_class": "review",
        "content_expires_at": now - timedelta(seconds=1),
        "metadata_expires_at": now + timedelta(days=30),
        "legal_hold_at": None,
        "live_evidence_reference": False,
    }
    values.update(updates)
    return StudioCleanupCandidate(**values)


async def _not_held(_candidate):
    return False


async def _current_false(*_args):
    return False


async def _current_none(*_args):
    return None


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _ExecuteResult:
    def __init__(self, rows, rowcount=1):
        self._rows = rows
        self.rowcount = rowcount

    def all(self):
        return self._rows


async def test_expiry_requires_terminal_non_evidence_without_hold():
    now = datetime.now(timezone.utc)
    eligible = await cleanup_decision(
        _candidate(now), now=now, legal_hold_check=_not_held
    )
    assert eligible.eligible is True
    assert eligible.reason == "expired"
    cases = [
        (_candidate(now, job_terminal=False), "job_not_terminal"),
        (_candidate(now, live_evidence_reference=True), "current_evidence"),
        (_candidate(now, retention_class="evidence"), "evidence_retention"),
        (
            _candidate(now, content_expires_at=now + timedelta(seconds=1)),
            "not_expired",
        ),
        (_candidate(now, legal_hold_at=now), "legal_hold"),
    ]
    for candidate, reason in cases:
        decision = await cleanup_decision(
            candidate, now=now, legal_hold_check=_not_held
        )
        assert decision.eligible is False
        assert decision.reason == reason


async def test_metadata_expiry_respects_exact_evidence_and_legal_hold():
    now = datetime.now(timezone.utc)
    expired = _candidate(
        now,
        metadata_expires_at=now - timedelta(seconds=1),
    )
    assert metadata_is_retained(expired, now=now) is False
    assert (
        metadata_is_retained(
            _candidate(
                now,
                metadata_expires_at=now - timedelta(seconds=1),
                live_evidence_reference=True,
            ),
            now=now,
        )
        is True
    )
    assert (
        metadata_is_retained(
            _candidate(
                now,
                metadata_expires_at=now - timedelta(seconds=1),
                legal_hold_at=now,
            ),
            now=now,
        )
        is True
    )
    assert (
        metadata_is_retained(
            _candidate(
                now,
                metadata_expires_at=now - timedelta(seconds=1),
                live_evidence_reference=None,
            ),
            now=now,
        )
        is True
    )


async def test_authoritative_hold_check_fails_closed():
    now = datetime.now(timezone.utc)

    async def held(_candidate):
        return True

    async def unavailable(_candidate):
        raise RuntimeError("provider details must not escape")

    hold = await cleanup_decision(_candidate(now), now=now, legal_hold_check=held)
    failed = await cleanup_decision(
        _candidate(now), now=now, legal_hold_check=unavailable
    )
    assert (hold.eligible, hold.reason) == (False, "legal_hold")
    assert (failed.eligible, failed.reason) == (False, "hold_check_failed")

    async def hanging(_candidate):
        await asyncio.Event().wait()

    timed_out = await cleanup_decision(
        _candidate(now),
        now=now,
        legal_hold_check=hanging,
        legal_hold_timeout_seconds=0.01,
    )
    assert (timed_out.eligible, timed_out.reason) == (
        False,
        "hold_check_failed",
    )


async def test_cleanup_batch_is_bounded():
    now = datetime.now(timezone.utc)
    decisions = await bounded_cleanup_candidates(
        [_candidate(now) for _ in range(5)],
        now=now,
        legal_hold_check=_not_held,
        limit=2,
    )
    assert len(decisions) == 2
    with pytest.raises(ValueError):
        await bounded_cleanup_candidates(
            [], now=now, legal_hold_check=_not_held, limit=0
        )


async def test_repeatedly_cancelled_delete_waits_for_storage_thread_to_finish():
    started = threading.Event()
    release = threading.Event()

    class BlockingStore:
        def delete(self, _ref):
            started.set()
            release.wait(timeout=2)
            return True

    service = StudioArtifactRetentionService(
        object(),
        tenant_id=uuid.uuid4(),
        object_store=BlockingStore(),
        legal_hold_check=_not_held,
        current_evidence_check=lambda *_args: _not_held(None),
    )
    task = asyncio.create_task(service._delete_to_completion(object()))
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_historical_current_outcome_does_not_replace_live_reference_gate():
    now = datetime.now(timezone.utc)
    historical = await cleanup_decision(
        _candidate(now, adoption_outcome="current_evidence"),
        now=now,
        legal_hold_check=_not_held,
    )
    unknown = await cleanup_decision(
        _candidate(now, live_evidence_reference=None),
        now=now,
        legal_hold_check=_not_held,
    )
    assert (historical.eligible, historical.reason) == (True, "expired")
    assert (unknown.eligible, unknown.reason) == (
        False,
        "evidence_check_unavailable",
    )


async def test_staged_reconciliation_is_bounded_and_deletes_only_last_orphan(tmp_path):
    tenant_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    content = b"orphaned render"
    stage = store.stage(
        tenant_id,
        content,
        job_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        reconcile_after=now,
        media_type="application/pdf",
        expected_sha256=hashlib.sha256(content).hexdigest(),
    )

    @asynccontextmanager
    async def object_lock(_ref):
        yield

    async def inactive(_stage):
        return False

    async def unreferenced(_ref):
        return False

    decisions = await reconcile_staged_batch(
        store,
        tenant_id=tenant_id,
        now=now + timedelta(seconds=1),
        limit=1,
        stage_active_check=inactive,
        object_reference_check=unreferenced,
        object_lock=object_lock,
    )
    assert [(item.stage_id, item.action) for item in decisions] == [
        (stage.stage_id, "deleted")
    ]
    assert (
        store.list_staged(
            tenant_id,
            reconcile_before=now + timedelta(seconds=1),
            limit=10,
        )
        == []
    )


async def test_staged_reconciliation_advances_past_a_permanent_failed_receipt(
    tmp_path,
):
    tenant_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    for index in range(4):
        content = f"orphaned-render-{index}".encode()
        store.stage(
            tenant_id,
            content,
            job_id=uuid.uuid4(),
            lease_token=uuid.uuid4(),
            reconcile_after=now,
            media_type="application/pdf",
            expected_sha256=hashlib.sha256(content).hexdigest(),
        )
    first = LocalStudioObjectStore(tmp_path, max_object_bytes=1024).list_staged(
        tenant_id,
        reconcile_before=now + timedelta(seconds=1),
        limit=1,
    )[0]
    reconciler_store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)

    @asynccontextmanager
    async def object_lock(_ref):
        yield

    async def active_check(stage):
        if stage.stage_id == first.stage_id:
            raise RuntimeError("permanent dependency failure")
        return False

    async def unreferenced(_ref):
        return False

    failed = await reconcile_staged_batch(
        reconciler_store,
        tenant_id=tenant_id,
        now=now + timedelta(seconds=1),
        limit=1,
        stage_active_check=active_check,
        object_reference_check=unreferenced,
        object_lock=object_lock,
    )
    progressed = await reconcile_staged_batch(
        reconciler_store,
        tenant_id=tenant_id,
        now=now + timedelta(seconds=2),
        limit=1,
        stage_active_check=active_check,
        object_reference_check=unreferenced,
        object_lock=object_lock,
    )

    assert [(item.stage_id, item.reason) for item in failed] == [
        (first.stage_id, "check_failed")
    ]
    assert len(progressed) == 1
    assert progressed[0].stage_id != first.stage_id
    assert progressed[0].action == "deleted"


async def test_durable_job_cleanup_requires_no_artifact_or_stage():
    now = datetime.now(timezone.utc)
    base = {
        "job_id": uuid.uuid4(),
        "terminal": True,
        "completed_at": now - timedelta(hours=2),
        "retain_until": now - timedelta(seconds=1),
        "has_artifact": False,
        "has_staged_object": False,
    }
    decisions = bounded_durable_job_cleanup(
        [
            StudioDurableJobCleanupCandidate(**base),
            StudioDurableJobCleanupCandidate(**{**base, "job_id": uuid.uuid4()}),
        ],
        now=now,
        limit=1,
    )
    assert len(decisions) == 1
    assert decisions[0].eligible is True


def _fake_job(status="completed", payload=None):
    job = MagicMock()
    job.status = status
    job.payload = payload or {}
    return job


def _fake_artifact(**overrides):
    now = datetime.now(timezone.utc)
    defaults = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "job_id": uuid.uuid4(),
        "object_key": f"studio-content/v1/ab/{'a' * 64}",
        "content_sha256": "a" * 64,
        "byte_size": 1,
        "media_type": "application/pdf",
        "adoption_outcome": "stale_output",
        "retention_class": "review",
        "content_expires_at": now - timedelta(seconds=1),
        "metadata_expires_at": now + timedelta(days=30),
        "legal_hold_at": None,
        "storage_state": "active",
        "delete_requested_at": None,
        "deleted_at": None,
        "created_at": now,
    }
    defaults.update(overrides)
    artifact = MagicMock()
    for key, value in defaults.items():
        setattr(artifact, key, value)
    return artifact


def _object_ref(tenant_id, seed="a"):
    return StudioObjectRef(
        tenant_id=tenant_id,
        object_key=f"studio-content/v1/{seed * 2}/{seed * 64}",
        sha256=seed * 64,
        byte_size=1,
        media_type="application/pdf",
    )


def _db_mock_for_artifact(artifact, job=None, active_refs=0, delete_rowcount=1):
    clock_times = [
        datetime.now(timezone.utc) + timedelta(seconds=i) for i in range(30)
    ]
    clock_iter = iter(clock_times)

    def scalar(query):
        sql = str(query)
        if "clock_timestamp()" in sql:
            return next(clock_iter)
        if "SELECT studio_render_artifacts" in sql:
            return artifact
        if "SELECT durable_jobs" in sql:
            return job
        if "count" in sql.lower() and "studio_render_artifacts" in sql:
            return active_refs
        return None

    db = AsyncMock()
    db.scalar.side_effect = scalar
    db.scalars.return_value = _ScalarResult([])
    db.execute.return_value = _ExecuteResult([], delete_rowcount)
    return db


async def test_cleanup_decision_input_validation():
    now = datetime.now(timezone.utc)
    candidate = _candidate(now)
    with pytest.raises(ValueError, match="legal hold timeout"):
        await cleanup_decision(
            candidate,
            now=now,
            legal_hold_check=_not_held,
            legal_hold_timeout_seconds=0,
        )
    with pytest.raises(ValueError, match="legal hold timeout"):
        await cleanup_decision(
            candidate,
            now=now,
            legal_hold_check=_not_held,
            legal_hold_timeout_seconds=31,
        )


async def test_durable_job_cleanup_decision_paths():
    now = datetime.now(timezone.utc)
    base = {
        "job_id": uuid.uuid4(),
        "terminal": True,
        "completed_at": now - timedelta(hours=2),
        "retain_until": now - timedelta(seconds=1),
        "has_artifact": False,
        "has_staged_object": False,
    }
    decision = durable_job_cleanup_decision(
        StudioDurableJobCleanupCandidate(**base), now=now
    )
    assert decision.eligible is True
    assert (
        durable_job_cleanup_decision(
            StudioDurableJobCleanupCandidate(**{**base, "terminal": False}), now=now
        ).reason
        == "job_not_terminal"
    )
    assert (
        durable_job_cleanup_decision(
            StudioDurableJobCleanupCandidate(**{**base, "completed_at": None}), now=now
        ).reason
        == "job_not_terminal"
    )
    assert (
        durable_job_cleanup_decision(
            StudioDurableJobCleanupCandidate(
                **{**base, "retain_until": now + timedelta(seconds=1)}
            ),
            now=now,
        ).reason
        == "job_not_expired"
    )
    assert (
        durable_job_cleanup_decision(
            StudioDurableJobCleanupCandidate(**{**base, "has_artifact": True}), now=now
        ).reason
        == "artifact_retained"
    )
    assert (
        durable_job_cleanup_decision(
            StudioDurableJobCleanupCandidate(**{**base, "has_staged_object": True}),
            now=now,
        ).reason
        == "stage_retained"
    )


async def test_bounded_durable_job_cleanup_validation():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="job cleanup limit"):
        bounded_durable_job_cleanup([], now=now, limit=0)
    with pytest.raises(ValueError, match="job cleanup limit"):
        bounded_durable_job_cleanup([], now=now, limit=501)


async def test_reconcile_staged_batch_validation_and_branches(tmp_path):
    tenant_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)

    @asynccontextmanager
    async def object_lock(_ref):
        yield

    async def inactive(_stage):
        return False

    async def unreferenced(_ref):
        return False

    with pytest.raises(ValueError, match="stage reconciliation limit"):
        await reconcile_staged_batch(
            store,
            tenant_id=tenant_id,
            now=now,
            limit=0,
            stage_active_check=inactive,
            object_reference_check=unreferenced,
            object_lock=object_lock,
        )
    with pytest.raises(ValueError, match="stage reconciliation timeout"):
        await reconcile_staged_batch(
            store,
            tenant_id=tenant_id,
            now=now,
            limit=1,
            stage_active_check=inactive,
            object_reference_check=unreferenced,
            object_lock=object_lock,
            check_timeout_seconds=0,
        )

    content_active = b"active"
    stage_active = store.stage(
        tenant_id,
        content_active,
        job_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        reconcile_after=now,
        media_type="application/pdf",
        expected_sha256=hashlib.sha256(content_active).hexdigest(),
    )
    content_referenced = b"referenced"
    stage_referenced = store.stage(
        tenant_id,
        content_referenced,
        job_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        reconcile_after=now,
        media_type="application/pdf",
        expected_sha256=hashlib.sha256(content_referenced).hexdigest(),
    )

    async def active_check(stage):
        return stage.stage_id == stage_active.stage_id

    async def referenced_check(ref):
        return ref.object_key == stage_referenced.object_ref.object_key

    decisions = await reconcile_staged_batch(
        store,
        tenant_id=tenant_id,
        now=now + timedelta(seconds=1),
        limit=10,
        stage_active_check=active_check,
        object_reference_check=referenced_check,
        object_lock=object_lock,
    )
    by_stage = {item.stage_id: item for item in decisions}
    assert by_stage[stage_active.stage_id].action == "kept"
    assert by_stage[stage_active.stage_id].reason == "lease_active"
    assert by_stage[stage_referenced.stage_id].action == "acknowledged"
    assert by_stage[stage_referenced.stage_id].reason == "artifact_referenced"

    content_shared = b"shared"
    stage_first = store.stage(
        tenant_id,
        content_shared,
        job_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        reconcile_after=now,
        media_type="application/pdf",
        expected_sha256=hashlib.sha256(content_shared).hexdigest(),
    )
    _ = store.stage(
        tenant_id,
        content_shared,
        job_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        reconcile_after=now,
        media_type="application/pdf",
        expected_sha256=hashlib.sha256(content_shared).hexdigest(),
    )
    decisions = await reconcile_staged_batch(
        store,
        tenant_id=tenant_id,
        now=now + timedelta(seconds=1),
        limit=10,
        stage_active_check=inactive,
        object_reference_check=unreferenced,
        object_lock=object_lock,
    )
    by_stage = {item.stage_id: item for item in decisions}
    kept = [item for item in decisions if item.action == "acknowledged"]
    deleted = [item for item in decisions if item.action == "deleted"]
    assert len(kept) == 1
    assert len(deleted) == 1
    assert kept[0].reason == "sibling_stage_retained"
    assert deleted[0].reason == "unreferenced"

    class FailingStore:
        def list_staged(self, tenant_id, *, reconcile_before, limit):
            return [stage_first]

        def defer_stage(self, stage, *, reconcile_after):
            return True

        def has_other_stages(self, stage):
            return False

        def delete_staged(self, stage):
            raise RuntimeError("disk failure")

    decisions = await reconcile_staged_batch(
        FailingStore(),
        tenant_id=tenant_id,
        now=now + timedelta(seconds=1),
        limit=10,
        stage_active_check=inactive,
        object_reference_check=unreferenced,
        object_lock=object_lock,
    )
    assert decisions == [
        StudioStagedReconciliationDecision(stage_first.stage_id, "kept", "delete_failed")
    ]


async def test_staged_receipt_reconciler_input_validation_and_object_lock():
    db = AsyncMock()
    db.execute.return_value = None
    tenant_id = uuid.uuid4()
    with pytest.raises(ValueError, match="stage reconciliation timeout"):
        StudioStagedReceiptReconciler(
            db,
            tenant_id=tenant_id,
            object_store=object(),
            legal_hold_check=_not_held,
            current_evidence_check=_current_false,
            check_timeout_seconds=0,
        )
    reconciler = StudioStagedReceiptReconciler(
        db,
        tenant_id=tenant_id,
        object_store=object(),
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    other_tenant = uuid.uuid4()
    ref = _object_ref(other_tenant)
    with pytest.raises(ValueError, match="cross-tenant Studio stage"):
        async with reconciler._object_lock(ref):
            pass


async def test_staged_receipt_reconciler_stage_active_branches():
    tenant_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    ref_other = _object_ref(uuid.uuid4())
    stage_other = StudioStagedObject(
        stage_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        object_ref=ref_other,
        reconcile_after=now,
        state="materialized",
    )

    db = AsyncMock()
    db.execute.return_value = None
    reconciler = StudioStagedReceiptReconciler(
        db,
        tenant_id=tenant_id,
        object_store=object(),
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    assert await reconciler._stage_active(stage_other) is True

    ref = _object_ref(tenant_id, seed="b")
    stage = StudioStagedObject(
        stage_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        object_ref=ref,
        reconcile_after=now,
        state="materialized",
    )

    db = AsyncMock()
    db.execute.return_value = None
    db.scalar.return_value = _fake_job(status="completed")
    reconciler = StudioStagedReceiptReconciler(
        db,
        tenant_id=tenant_id,
        object_store=object(),
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    assert await reconciler._stage_active(stage) is False

    db = AsyncMock()
    db.execute.return_value = None
    db.scalar.side_effect = [
        _fake_job(
            status="running",
            payload={
                "lease_token": str(stage.lease_token),
                "lease_expires_at": (now + timedelta(minutes=1)).isoformat(),
            },
        ),
        now,
    ]
    reconciler = StudioStagedReceiptReconciler(
        db,
        tenant_id=tenant_id,
        object_store=object(),
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    assert await reconciler._stage_active(stage) is True

    db = AsyncMock()
    db.execute.return_value = None
    db.scalar.return_value = _fake_job(status="running", payload={})
    reconciler = StudioStagedReceiptReconciler(
        db,
        tenant_id=tenant_id,
        object_store=object(),
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    assert await reconciler._stage_active(stage) is False

    db = AsyncMock()
    db.execute.return_value = None
    db.scalar.side_effect = [
        _fake_job(
            status="running",
            payload={
                "lease_token": str(stage.lease_token),
                "lease_expires_at": (now - timedelta(minutes=1)).isoformat(),
            },
        ),
        now,
    ]
    reconciler = StudioStagedReceiptReconciler(
        db,
        tenant_id=tenant_id,
        object_store=object(),
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    assert await reconciler._stage_active(stage) is False


async def test_staged_receipt_reconciler_object_referenced_branches():
    tenant_id = uuid.uuid4()
    ref = _object_ref(tenant_id)

    db = AsyncMock()
    db.execute.return_value = None
    reconciler = StudioStagedReceiptReconciler(
        db,
        tenant_id=tenant_id,
        object_store=object(),
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    db.scalars.return_value = _ScalarResult([])
    assert await reconciler._object_referenced(ref) is False

    art = _fake_artifact(tenant_id=tenant_id, storage_state="active")
    db.scalars.return_value = _ScalarResult([art])
    db.scalar.return_value = _fake_job(status="completed")
    assert await reconciler._object_referenced(ref) is True

    art = _fake_artifact(tenant_id=tenant_id, storage_state="delete_pending")
    db.scalars.return_value = _ScalarResult([art])
    assert await reconciler._object_referenced(ref) is True

    art = _fake_artifact(tenant_id=tenant_id, storage_state="deleted")
    db.scalars.return_value = _ScalarResult([art])

    async def fail(_candidate):
        raise RuntimeError("boom")

    reconciler = StudioStagedReceiptReconciler(
        db,
        tenant_id=tenant_id,
        object_store=object(),
        legal_hold_check=fail,
        current_evidence_check=_current_false,
    )
    assert await reconciler._object_referenced(ref) is True

    async def held_false(_candidate):
        return False

    reconciler = StudioStagedReceiptReconciler(
        db,
        tenant_id=tenant_id,
        object_store=object(),
        legal_hold_check=held_false,
        current_evidence_check=_current_false,
    )
    assert await reconciler._object_referenced(ref) is False

    async def held_true(_candidate):
        return True

    reconciler = StudioStagedReceiptReconciler(
        db,
        tenant_id=tenant_id,
        object_store=object(),
        legal_hold_check=held_true,
        current_evidence_check=_current_false,
    )
    assert await reconciler._object_referenced(ref) is True

    reconciler = StudioStagedReceiptReconciler(
        db,
        tenant_id=tenant_id,
        object_store=object(),
        legal_hold_check=held_false,
        current_evidence_check=_current_none,
    )
    assert await reconciler._object_referenced(ref) is True


async def test_artifact_retention_service_input_validation():
    db = AsyncMock()
    tenant_id = uuid.uuid4()
    with pytest.raises(ValueError, match="legal hold timeout"):
        StudioArtifactRetentionService(
            db,
            tenant_id=tenant_id,
            object_store=object(),
            legal_hold_check=_not_held,
            current_evidence_check=_current_false,
            legal_hold_timeout_seconds=0,
        )
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=tenant_id,
        object_store=object(),
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    assert svc.tenant_id == tenant_id


async def test_delete_if_eligible_not_found():
    db = AsyncMock()
    db.scalar.return_value = None
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=uuid.uuid4(),
        object_store=object(),
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    decision = await svc.delete_if_eligible(uuid.uuid4())
    assert (decision.eligible, decision.reason) == (False, "not_found")


async def test_delete_if_eligible_already_deleted_and_not_eligible():
    now = datetime.now(timezone.utc)
    art = _fake_artifact(storage_state="deleted")
    db = AsyncMock()
    db.scalar.return_value = art
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=art.tenant_id,
        object_store=object(),
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    decision = await svc.delete_if_eligible(art.id)
    assert (decision.eligible, decision.reason) == (False, "already_deleted")

    art2 = _fake_artifact(
        storage_state="active",
        content_expires_at=now + timedelta(days=1),
        job_terminal=True,
    )
    db = _db_mock_for_artifact(art2, job=_fake_job(status="completed"))
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=art2.tenant_id,
        object_store=object(),
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    decision = await svc.delete_if_eligible(art2.id)
    assert decision.eligible is False
    assert decision.reason == "not_expired"


async def test_delete_if_eligible_with_active_reference_deletes_metadata_only(
    tmp_path,
):
    now = datetime.now(timezone.utc)
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    content = b"artifact"
    ref = store.put(uuid.uuid4(), content, media_type="application/pdf")
    art = _fake_artifact(
        tenant_id=ref.tenant_id,
        object_key=ref.object_key,
        content_sha256=ref.sha256,
        byte_size=ref.byte_size,
        media_type=ref.media_type,
        storage_state="active",
        content_expires_at=now - timedelta(seconds=1),
        job_terminal=True,
    )
    job = _fake_job(status="completed")
    db = _db_mock_for_artifact(art, job=job, active_refs=1)
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=ref.tenant_id,
        object_store=store,
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    decision = await svc.delete_if_eligible(art.id)
    assert decision.eligible is True
    assert art.storage_state == "deleted"
    assert store.read(ref) == content


async def test_delete_if_eligible_deletes_object_when_unreferenced(tmp_path):
    now = datetime.now(timezone.utc)
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    content = b"artifact"
    ref = store.put(uuid.uuid4(), content, media_type="application/pdf")
    art = _fake_artifact(
        tenant_id=ref.tenant_id,
        object_key=ref.object_key,
        content_sha256=ref.sha256,
        byte_size=ref.byte_size,
        media_type=ref.media_type,
        storage_state="active",
        content_expires_at=now - timedelta(seconds=1),
        job_terminal=True,
    )
    job = _fake_job(status="completed")
    db = _db_mock_for_artifact(art, job=job, active_refs=0)
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=ref.tenant_id,
        object_store=store,
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    decision = await svc.delete_if_eligible(art.id)
    assert decision.eligible is True
    assert art.storage_state == "deleted"
    with pytest.raises(Exception):
        store.read(ref)


async def test_delete_if_eligible_routes_pending_to_finalize(tmp_path):
    now = datetime.now(timezone.utc)
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    content = b"pending-route"
    ref = store.put(uuid.uuid4(), content, media_type="application/pdf")
    art = _fake_artifact(
        tenant_id=ref.tenant_id,
        object_key=ref.object_key,
        content_sha256=ref.sha256,
        byte_size=ref.byte_size,
        media_type=ref.media_type,
        storage_state="delete_pending",
        content_expires_at=now - timedelta(seconds=1),
        job_terminal=True,
    )
    job = _fake_job(status="completed")
    db = _db_mock_for_artifact(art, job=job, active_refs=0)
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=ref.tenant_id,
        object_store=store,
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    decision = await svc.delete_if_eligible(art.id)
    assert decision.eligible is True
    assert art.storage_state == "deleted"
    with pytest.raises(Exception):
        store.read(ref)


async def test_finalize_pending_delete_paths(tmp_path):
    now = datetime.now(timezone.utc)
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    content = b"pending"
    ref = store.put(uuid.uuid4(), content, media_type="application/pdf")
    job = _fake_job(status="completed")

    db = AsyncMock()
    db.scalar.return_value = None
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=ref.tenant_id,
        object_store=store,
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    decision = await svc._finalize_pending_delete(
        uuid.uuid4(), object_key=ref.object_key
    )
    assert decision.reason == "not_found"

    art_deleted = _fake_artifact(storage_state="deleted")
    db = AsyncMock()
    db.scalar.return_value = art_deleted
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=art_deleted.tenant_id,
        object_store=store,
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    decision = await svc._finalize_pending_delete(
        art_deleted.id, object_key=art_deleted.object_key
    )
    assert decision.reason == "already_deleted"

    art_active = _fake_artifact(storage_state="active")
    db = AsyncMock()
    db.scalar.return_value = art_active
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=art_active.tenant_id,
        object_store=store,
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    decision = await svc._finalize_pending_delete(
        art_active.id, object_key=art_active.object_key
    )
    assert decision.reason == "not_delete_pending"

    art_restore = _fake_artifact(
        tenant_id=ref.tenant_id,
        object_key=ref.object_key,
        content_sha256=ref.sha256,
        byte_size=ref.byte_size,
        media_type=ref.media_type,
        storage_state="delete_pending",
        content_expires_at=now + timedelta(days=1),
        job_terminal=True,
    )
    db = _db_mock_for_artifact(art_restore, job=job)
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=ref.tenant_id,
        object_store=store,
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    decision = await svc._finalize_pending_delete(
        art_restore.id, object_key=art_restore.object_key
    )
    assert decision.eligible is False
    assert art_restore.storage_state == "active"
    assert art_restore.delete_requested_at is None

    missing_ref = _object_ref(ref.tenant_id, seed="c")
    art_missing = _fake_artifact(
        tenant_id=ref.tenant_id,
        object_key=missing_ref.object_key,
        content_sha256=missing_ref.sha256,
        byte_size=missing_ref.byte_size,
        media_type=missing_ref.media_type,
        storage_state="delete_pending",
        content_expires_at=now + timedelta(days=1),
        job_terminal=True,
    )
    db = _db_mock_for_artifact(art_missing, job=job)
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=ref.tenant_id,
        object_store=store,
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    decision = await svc._finalize_pending_delete(
        art_missing.id, object_key=art_missing.object_key
    )
    assert decision.reason == "storage_delete_pending"

    art_refs = _fake_artifact(
        tenant_id=ref.tenant_id,
        object_key=ref.object_key,
        content_sha256=ref.sha256,
        byte_size=ref.byte_size,
        media_type=ref.media_type,
        storage_state="delete_pending",
        content_expires_at=now - timedelta(seconds=1),
        job_terminal=True,
    )
    db = _db_mock_for_artifact(art_refs, job=job, active_refs=1)
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=ref.tenant_id,
        object_store=store,
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    decision = await svc._finalize_pending_delete(
        art_refs.id, object_key=art_refs.object_key
    )
    assert decision.eligible is True
    assert art_refs.storage_state == "deleted"
    assert store.read(ref) == content

    art_fresh = _fake_artifact(
        tenant_id=ref.tenant_id,
        object_key=ref.object_key,
        content_sha256=ref.sha256,
        byte_size=ref.byte_size,
        media_type=ref.media_type,
        storage_state="delete_pending",
        content_expires_at=now - timedelta(seconds=1),
        job_terminal=True,
    )

    class FailingStore:
        def delete(self, ref):
            raise RuntimeError("disk failure")

        def read(self, ref, *, max_bytes=None):
            return b""

    db = _db_mock_for_artifact(art_fresh, job=job, active_refs=0)
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=ref.tenant_id,
        object_store=FailingStore(),
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    decision = await svc._finalize_pending_delete(
        art_fresh.id, object_key=art_fresh.object_key
    )
    assert decision.reason == "storage_delete_pending"

    db = _db_mock_for_artifact(art_fresh, job=job, active_refs=0)
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=ref.tenant_id,
        object_store=store,
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    decision = await svc._finalize_pending_delete(
        art_fresh.id, object_key=art_fresh.object_key
    )
    assert decision.eligible is True
    assert art_fresh.storage_state == "deleted"
    with pytest.raises(Exception):
        store.read(ref)


async def test_cleanup_batch_limit_validation():
    db = AsyncMock()
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=uuid.uuid4(),
        object_store=object(),
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    with pytest.raises(ValueError, match="cleanup limit"):
        await svc.cleanup_batch(limit=0)
    with pytest.raises(ValueError, match="cleanup limit"):
        await svc.cleanup_batch(limit=501)


async def test_cleanup_metadata_batch_paths():
    now = datetime.now(timezone.utc)
    art = _fake_artifact(
        storage_state="deleted",
        metadata_expires_at=now - timedelta(seconds=1),
        job_terminal=True,
    )
    tenant_id = art.tenant_id

    def make_db():
        db = AsyncMock()
        db.scalars.return_value = _ScalarResult([art.id])
        db.scalar.side_effect = lambda query: (
            now
            if "clock_timestamp()" in str(query)
            else (
                art
                if "SELECT studio_render_artifacts" in str(query)
                else (
                    _fake_job(status="completed")
                    if "SELECT durable_jobs" in str(query)
                    else None
                )
            )
        )
        return db

    db = make_db()
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=tenant_id,
        object_store=object(),
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    with pytest.raises(ValueError, match="metadata cleanup limit"):
        await svc.cleanup_metadata_batch(limit=0)
    with pytest.raises(ValueError, match="metadata cleanup limit"):
        await svc.cleanup_metadata_batch(limit=501)
    decisions = await svc.cleanup_metadata_batch(limit=1)
    assert decisions[0].eligible is True
    assert decisions[0].reason == "metadata_expired"

    async def current_raises(*_args):
        raise RuntimeError("boom")

    db = make_db()
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=tenant_id,
        object_store=object(),
        legal_hold_check=_not_held,
        current_evidence_check=current_raises,
    )
    decisions = await svc.cleanup_metadata_batch(limit=1)
    assert decisions[0].reason == "metadata_retained"

    async def held_true(_candidate):
        return True

    db = make_db()
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=tenant_id,
        object_store=object(),
        legal_hold_check=held_true,
        current_evidence_check=_current_false,
    )
    decisions = await svc.cleanup_metadata_batch(limit=1)
    assert decisions[0].reason == "legal_hold"


async def test_cleanup_durable_jobs_batch_paths(tmp_path):
    now = datetime.now(timezone.utc)
    tenant_id = uuid.uuid4()
    job_id = uuid.uuid4()
    completed_at = now - timedelta(days=8)
    empty_store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)

    def make_db(delete_rowcount=1):
        db = AsyncMock()
        db.execute.return_value = _ExecuteResult(
            [(job_id, completed_at)], delete_rowcount
        )
        db.scalar.side_effect = lambda query: (
            now if "clock_timestamp()" in str(query) else None
        )
        return db

    db = make_db()
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=tenant_id,
        object_store=empty_store,
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    with pytest.raises(ValueError, match="job cleanup limit"):
        await svc.cleanup_durable_jobs_batch(limit=0)
    with pytest.raises(ValueError, match="job retention"):
        await svc.cleanup_durable_jobs_batch(limit=1, retain_for=timedelta(days=0))

    decisions = await svc.cleanup_durable_jobs_batch(limit=1)
    assert decisions[0].eligible is True
    assert decisions[0].reason == "expired"

    class BrokenStore:
        def list_staged(self, *args, **kwargs):
            raise RuntimeError("boom")

    db = make_db()
    svc_broken = StudioArtifactRetentionService(
        db,
        tenant_id=tenant_id,
        object_store=BrokenStore(),
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    decisions = await svc_broken.cleanup_durable_jobs_batch(limit=1)
    assert decisions[0].eligible is False
    assert decisions[0].reason == "stage_retained"

    db = make_db(delete_rowcount=0)
    svc = StudioArtifactRetentionService(
        db,
        tenant_id=tenant_id,
        object_store=empty_store,
        legal_hold_check=_not_held,
        current_evidence_check=_current_false,
    )
    decisions = await svc.cleanup_durable_jobs_batch(limit=1)
    assert decisions[0].eligible is False
    assert decisions[0].reason == "job_changed"
