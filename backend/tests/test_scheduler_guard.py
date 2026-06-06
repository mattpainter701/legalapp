"""Unit tests for the cluster-wide job guard (advisory lock).

These tests never touch a live Postgres — the session and its ``execute`` are
mocked, so they exercise only the pure Python control flow of ``_lock_key``,
``job_lock`` and ``_run_guarded``.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import scheduler as sched
from app.services.scheduler import _lock_key, _run_guarded, job_lock

BIGINT_MAX = 2**63 - 1


# ─── _lock_key ────────────────────────────────────────────────────────────────


def test_lock_key_is_deterministic():
    for name in ["renewal-watcher", "reg-monitor", "cloud-sync", ""]:
        assert _lock_key(name) == _lock_key(name)


def test_lock_key_in_bigint_range_and_int():
    names = [
        "renewal-watcher",
        "reg-monitor",
        "docket-watcher",
        "oc-status",
        "task-reminder",
        "estate-deadline-watcher",
        "user-sync",
        "cloud-sync",
        "smb-heartbeat",
    ]
    for name in names:
        key = _lock_key(name)
        assert isinstance(key, int)
        assert 0 <= key <= BIGINT_MAX


def test_lock_key_distinct_for_distinct_names():
    keys = {_lock_key(n) for n in ["a", "b", "renewal-watcher", "reg-monitor"]}
    assert len(keys) == 4


# ─── helpers ──────────────────────────────────────────────────────────────────


def _mock_session_with_results(scalar_values):
    """Build a fake AsyncSession whose successive ``execute().scalar()`` calls
    return the given values. Returns (session, execute_mock)."""
    scalars = list(scalar_values)

    async def _execute(*_args, **_kwargs):
        result = MagicMock()
        # pop next pre-seeded scalar for SELECT pg_try_advisory_lock;
        # unlock calls (no seeded value) just return a benign mock.
        result.scalar.return_value = scalars.pop(0) if scalars else None
        return result

    session = MagicMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.commit = AsyncMock()
    return session


def _patch_session_maker(session):
    """Patch ``async_session_maker`` in the scheduler module to yield ``session``."""

    @asynccontextmanager
    async def _maker():
        yield session

    return patch.object(sched, "async_session_maker", _maker)


# ─── job_lock ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_job_lock_acquired_yields_true_and_unlocks():
    session = _mock_session_with_results([True])
    with _patch_session_maker(session):
        async with job_lock("renewal-watcher") as acquired:
            assert acquired is True

    # Two execute calls: the try_advisory_lock and the advisory_unlock.
    assert session.execute.await_count == 2
    unlock_sql = str(session.execute.await_args_list[1].args[0])
    assert "pg_advisory_unlock" in unlock_sql
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_job_lock_not_acquired_yields_false_and_skips_unlock():
    session = _mock_session_with_results([False])
    with _patch_session_maker(session):
        async with job_lock("renewal-watcher") as acquired:
            assert acquired is False

    # Only the try_advisory_lock call; no unlock when we never held the lock.
    assert session.execute.await_count == 1
    only_sql = str(session.execute.await_args_list[0].args[0])
    assert "pg_try_advisory_lock" in only_sql
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_two_concurrent_runners_first_wins():
    """First runner acquires (True), second is rejected (False)."""
    first = _mock_session_with_results([True])
    second = _mock_session_with_results([False])

    with _patch_session_maker(first):
        async with job_lock("invoice-run") as got1:
            assert got1 is True
    with _patch_session_maker(second):
        async with job_lock("invoice-run") as got2:
            assert got2 is False


# ─── _run_guarded ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_guarded_awaits_when_lock_acquired():
    session = _mock_session_with_results([True])
    coro_fn = AsyncMock(return_value="done")
    with _patch_session_maker(session):
        result = await _run_guarded("job", coro_fn)
    coro_fn.assert_awaited_once()
    assert result == "done"


@pytest.mark.asyncio
async def test_run_guarded_does_not_await_when_lock_not_acquired():
    session = _mock_session_with_results([False])
    coro_fn = AsyncMock(return_value="done")
    with _patch_session_maker(session):
        result = await _run_guarded("job", coro_fn)
    coro_fn.assert_not_awaited()
    assert result is None


@pytest.mark.asyncio
async def test_run_guarded_swallows_exceptions():
    session = _mock_session_with_results([True])
    coro_fn = AsyncMock(side_effect=RuntimeError("boom"))
    with _patch_session_maker(session):
        # Must not raise — a failing job cannot crash the scheduler loop.
        result = await _run_guarded("job", coro_fn)
    assert result is None
    coro_fn.assert_awaited_once()
