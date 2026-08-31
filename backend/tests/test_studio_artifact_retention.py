"""Database-free retention and legal-hold policy checks."""

import asyncio
import hashlib
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from app.services.studio_artifact_retention import (
    StudioArtifactRetentionService,
    StudioCleanupCandidate,
    StudioDurableJobCleanupCandidate,
    bounded_durable_job_cleanup,
    bounded_cleanup_candidates,
    cleanup_decision,
    reconcile_staged_batch,
)
from app.services.studio_object_storage import LocalStudioObjectStore

pytestmark = pytest.mark.asyncio


def _candidate(now, **updates):
    values = {
        "tenant_id": uuid.uuid4(),
        "artifact_id": uuid.uuid4(),
        "job_terminal": True,
        "adoption_outcome": "stale_output",
        "retention_class": "review",
        "expires_at": now - timedelta(seconds=1),
        "legal_hold_at": None,
        "live_evidence_reference": False,
    }
    values.update(updates)
    return StudioCleanupCandidate(**values)


async def _not_held(_candidate):
    return False


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
        (_candidate(now, expires_at=now + timedelta(seconds=1)), "not_expired"),
        (_candidate(now, legal_hold_at=now), "legal_hold"),
    ]
    for candidate, reason in cases:
        decision = await cleanup_decision(
            candidate, now=now, legal_hold_check=_not_held
        )
        assert decision.eligible is False
        assert decision.reason == reason


async def test_authoritative_hold_check_fails_closed():
    now = datetime.now(timezone.utc)

    async def held(_candidate):
        return True

    async def unavailable(_candidate):
        raise RuntimeError("provider details must not escape")

    hold = await cleanup_decision(
        _candidate(now), now=now, legal_hold_check=held
    )
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
    assert store.list_staged(
        tenant_id,
        reconcile_before=now + timedelta(seconds=1),
        limit=10,
    ) == []


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
