"""Database-free retention and legal-hold policy checks."""

import asyncio
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services.studio_artifact_retention import (
    StudioArtifactRetentionService,
    StudioCleanupCandidate,
    bounded_cleanup_candidates,
    cleanup_decision,
)

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
        (_candidate(now, adoption_outcome="current_evidence"), "current_evidence"),
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

