"""Fail-closed checks for the local content-addressed Studio object store.

The store is the only thing standing between a hostile renderer and the shared
CAS root, so every guard here asserts it refuses rather than degrades: unsafe
paths, digests that stop describing their bytes, oversized reads, and staging
receipts that no longer match what was written.
"""

import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.studio_object_storage import (
    LocalStudioObjectStore,
    StudioObjectRef,
    StudioStagedObject,
    StudioStorageError,
    _is_link,
    run_storage_operation_to_completion,
)

TENANT = uuid.UUID("11111111-1111-4111-8111-111111111111")
CONTENT = b"studio artifact bytes"


@pytest.fixture
def store(tmp_path):
    return LocalStudioObjectStore(tmp_path / "cas", max_object_bytes=4096)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _past():
    return datetime.now(timezone.utc) - timedelta(minutes=5)


def _ref(content: bytes, *, media_type: str = "application/pdf") -> StudioObjectRef:
    digest = _digest(content)
    return StudioObjectRef(
        tenant_id=TENANT,
        object_key=f"studio-content/v1/{digest[:2]}/{digest}",
        sha256=digest,
        byte_size=len(content),
        media_type=media_type,
    )


def _stage(store, content: bytes, *, reconcile_after=None) -> StudioStagedObject:
    return store.stage(
        TENANT,
        content,
        job_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        reconcile_after=(
            reconcile_after
            if reconcile_after is not None
            else datetime.now(timezone.utc) + timedelta(minutes=5)
        ),
        media_type="application/pdf",
        expected_sha256=_digest(content),
    )


def _staged(ref: StudioObjectRef, *, state: str = "reserved", **overrides):
    values = {
        "stage_id": uuid.uuid4(),
        "job_id": uuid.uuid4(),
        "lease_token": uuid.uuid4(),
        "object_ref": ref,
        "reconcile_after": datetime.now(timezone.utc) + timedelta(minutes=5),
        "state": state,
    }
    values.update(overrides)
    return StudioStagedObject(**values)


# --- bounded mutation draining --------------------------------------------


async def test_storage_operation_rejects_a_non_positive_timeout():
    with pytest.raises(ValueError, match="timeout must be positive"):
        await run_storage_operation_to_completion(lambda: None, timeout_seconds=0)


async def test_storage_operation_returns_the_completed_result():
    result = await run_storage_operation_to_completion(lambda: "done")

    assert result == "done"


async def test_storage_operation_drains_its_thread_before_timing_out():
    """A timed-out mutation must not leave its thread writing behind our back."""

    finished = []

    def slow():
        import time

        time.sleep(0.15)
        finished.append(True)

    with pytest.raises(TimeoutError):
        await run_storage_operation_to_completion(slow, timeout_seconds=0.01)

    assert finished == [True]


async def test_storage_operation_drains_its_thread_before_cancelling():
    finished = []

    def slow():
        import time

        time.sleep(0.15)
        finished.append(True)

    task = asyncio.ensure_future(run_storage_operation_to_completion(slow))
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished == [True]


async def test_storage_operation_reports_a_failure_only_after_the_thread_ends():
    finished = []

    def slow_and_failing():
        import time

        time.sleep(0.05)
        finished.append(True)
        raise RuntimeError("storage device fell over")

    with pytest.raises(RuntimeError, match="fell over"):
        await run_storage_operation_to_completion(
            slow_and_failing, timeout_seconds=0.01
        )

    assert finished == [True]


# --- link detection --------------------------------------------------------


def test_is_link_treats_a_symlink_as_unsafe():
    assert _is_link(SimpleNamespace(is_symlink=lambda: True)) is True


def test_is_link_accepts_a_plain_path(tmp_path):
    plain = tmp_path / "plain.bin"
    plain.write_bytes(b"x")

    assert _is_link(plain) is False


# --- worker heartbeat ------------------------------------------------------


def test_heartbeat_publishes_and_reports_freshness(store):
    store.touch_worker_heartbeat(healthy=True)

    assert store.worker_heartbeat_fresh(max_age_seconds=60) is True


def test_unhealthy_heartbeat_is_never_reported_fresh(store):
    store.touch_worker_heartbeat(healthy=False)

    assert store.worker_heartbeat_fresh(max_age_seconds=60) is False


@pytest.mark.parametrize("max_age_seconds", [19, 601])
def test_heartbeat_freshness_window_is_bounded(store, max_age_seconds):
    with pytest.raises(ValueError, match="heartbeat age is invalid"):
        store.worker_heartbeat_fresh(max_age_seconds=max_age_seconds)


def test_heartbeat_refuses_to_write_through_a_link(store):
    store.touch_worker_heartbeat()

    with patch("app.services.studio_object_storage._is_link", return_value=True):
        with pytest.raises(StudioStorageError) as error:
            store.touch_worker_heartbeat()
    assert error.value.code == "unsafe_storage_root"


def test_heartbeat_removes_its_temporary_file_when_publication_fails(store):
    store.root.mkdir(parents=True, exist_ok=True)

    with patch(
        "app.services.studio_object_storage.os.replace",
        side_effect=OSError("cross-device link"),
    ):
        with pytest.raises(OSError):
            store.touch_worker_heartbeat()

    leftovers = list(store.root.glob(".studio-heartbeat-*"))
    assert leftovers == []


def test_heartbeat_freshness_fails_closed_when_the_root_is_unreadable(store):
    store.touch_worker_heartbeat()

    with patch.object(Path, "stat", side_effect=OSError("io error")):
        assert store.worker_heartbeat_fresh(max_age_seconds=60) is False


# --- put and read ----------------------------------------------------------


def test_put_then_read_round_trips_verified_content(store):
    content = b"studio artifact bytes"

    ref = store.put(TENANT, content, media_type="application/pdf")

    assert ref.sha256 == _digest(content)
    assert ref.byte_size == len(content)
    assert store.read(ref) == content


def test_put_is_idempotent_for_identical_content(store):
    content = b"studio artifact bytes"

    first = store.put(TENANT, content, media_type="application/pdf")
    second = store.put(TENANT, content, media_type="application/pdf")

    assert first == second


@pytest.mark.parametrize(
    ("content", "code"),
    [
        pytest.param(b"", "empty_object", id="empty"),
        pytest.param(b"x" * 4097, "object_too_large", id="oversized"),
    ],
)
def test_put_refuses_content_outside_its_bounds(store, content, code):
    with pytest.raises(StudioStorageError) as error:
        store.put(TENANT, content, media_type="application/pdf")
    assert error.value.code == code


def test_put_refuses_content_that_does_not_match_its_promised_digest(store):
    with pytest.raises(StudioStorageError) as error:
        store.put(
            TENANT, b"actual", media_type="application/pdf", expected_sha256="a" * 64
        )
    assert error.value.code == "hash_mismatch"


def test_put_refuses_an_object_path_that_traverses_a_link(store):
    """Any linked component between the root and the object is refused."""

    digest = _digest(CONTENT)
    linked = store.root / str(TENANT) / "studio-objects"

    with patch(
        "app.services.studio_object_storage._is_link",
        side_effect=lambda path: Path(path) == linked,
    ):
        with pytest.raises(StudioStorageError) as error:
            store.put(TENANT, CONTENT, media_type="application/pdf")
    assert error.value.code == "unsafe_object_path"
    assert digest not in str(error.value)


def test_put_refuses_to_publish_into_a_directory_that_became_a_link(store):
    """The parent is re-checked after staging, not just while walking down."""

    digest = _digest(CONTENT)
    target_parent = store._path(TENANT, store.object_key(digest)).parent
    seen = []

    def swapped_after_the_walk(path):
        # Safe while the store walks down; linked by the time it publishes.
        if Path(path) != target_parent:
            return False
        seen.append(path)
        return len(seen) > 1

    with patch("app.services.studio_object_storage._is_link", swapped_after_the_walk):
        with pytest.raises(StudioStorageError) as error:
            store.put(TENANT, CONTENT, media_type="application/pdf")
    assert error.value.code == "unsafe_object_path"
    assert len(seen) > 1


def test_put_tolerates_another_writer_publishing_the_same_object_first(store):
    """Two workers can render identical bytes; the CAS inode must stay stable."""

    content = b"studio artifact bytes"

    def losing_link(source, target):
        # Stand in for the winner publishing between our existence check and
        # our own link call.
        Path(target).write_bytes(content)
        raise FileExistsError

    with patch("app.services.studio_object_storage.os.link", losing_link):
        ref = store.put(TENANT, content, media_type="application/pdf")

    assert store.read(ref) == content


def test_put_cleans_up_its_temporary_file_when_publication_fails(store):
    content = b"studio artifact bytes"
    target_parent = store._path(TENANT, store.object_key(_digest(content))).parent

    with patch(
        "app.services.studio_object_storage.os.link", side_effect=OSError("no space")
    ):
        with pytest.raises(OSError):
            store.put(TENANT, content, media_type="application/pdf")

    assert list(target_parent.glob(".studio-*")) == []


def test_read_refuses_a_limit_below_one_byte(store):
    ref = store.put(TENANT, b"studio bytes", media_type="application/pdf")

    with pytest.raises(StudioStorageError) as error:
        store.read(ref, max_bytes=0)
    assert error.value.code == "invalid_read_limit"


def test_read_refuses_an_object_larger_than_the_caller_allows(store):
    ref = store.put(TENANT, b"studio artifact bytes", media_type="application/pdf")

    with pytest.raises(StudioStorageError) as error:
        store.read(ref, max_bytes=4)
    assert error.value.code == "object_too_large"


def test_read_detects_content_that_changed_underneath_the_digest(store):
    content = b"studio artifact bytes"
    ref = store.put(TENANT, content, media_type="application/pdf")
    path = store._path(TENANT, ref.object_key)
    path.write_bytes(b"tampered payload of a different length")

    with pytest.raises(StudioStorageError) as error:
        store.read(ref)
    assert error.value.code == "hash_mismatch"


def test_read_reports_an_object_that_is_no_longer_present(store):
    ref = store.put(TENANT, b"studio bytes", media_type="application/pdf")
    store._path(TENANT, ref.object_key).unlink()

    with pytest.raises(StudioStorageError) as error:
        store.read(ref)
    assert error.value.code == "object_missing"


def test_directory_sync_is_a_no_op_when_the_directory_is_gone(tmp_path):
    LocalStudioObjectStore._sync_directory(tmp_path / "absent")


# --- object reference contract --------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"object_key": "not/a/key"}, "object key"),
        ({"sha256": "z" * 64}, "object digest"),
        ({"byte_size": 0}, "size must be positive"),
        ({"media_type": "not a media type"}, "media type"),
        ({"media_type": "application/" + "x" * 100}, "media type"),
    ],
)
def test_object_ref_rejects_a_reference_it_cannot_trust(overrides, expected):
    digest = _digest(b"studio bytes")
    values = {
        "tenant_id": TENANT,
        "object_key": f"studio-content/v1/{digest[:2]}/{digest}",
        "sha256": digest,
        "byte_size": 12,
        "media_type": "application/pdf",
    }
    values.update(overrides)

    with pytest.raises(ValueError, match=expected):
        StudioObjectRef(**values)


def test_object_ref_rejects_a_key_that_disagrees_with_its_digest():
    digest = _digest(b"studio bytes")
    other = _digest(b"different bytes")

    with pytest.raises(ValueError, match="does not match its digest"):
        StudioObjectRef(
            tenant_id=TENANT,
            object_key=f"studio-content/v1/{digest[:2]}/{other}",
            sha256=digest,
            byte_size=12,
            media_type="application/pdf",
        )


def test_object_key_rejects_a_digest_that_is_not_a_sha256():
    with pytest.raises(StudioStorageError) as error:
        LocalStudioObjectStore.object_key("nope")
    assert error.value.code == "invalid_hash"


# --- staging receipts ------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (
            {"reconcile_after": datetime(2026, 1, 1)},
            "timezone-aware",
        ),
        ({"state": "adopted"}, "invalid Studio stage state"),
    ],
)
def test_staged_object_rejects_an_untrustworthy_receipt(store, overrides, expected):
    ref = store.put(TENANT, b"studio bytes", media_type="application/pdf")

    with pytest.raises(ValueError, match=expected):
        _staged(ref, **overrides)


def test_acknowledging_an_absent_receipt_reports_no_work(store):
    assert store.acknowledge_stage(_staged(_ref(CONTENT))) is False


def test_staging_publishes_the_object_and_returns_a_materialized_receipt(store):
    stage = _stage(store, CONTENT)

    assert stage.state == "materialized"
    assert stage.object_ref.sha256 == _digest(CONTENT)
    assert store.read(stage.object_ref) == CONTENT
    assert store.acknowledge_stage(stage) is True


def test_acknowledging_a_receipt_that_was_rewritten_is_refused(store):
    stage = _stage(store, CONTENT)
    impostor = _staged(stage.object_ref, stage_id=stage.stage_id)

    with pytest.raises(StudioStorageError) as error:
        store.acknowledge_stage(impostor)
    assert error.value.code == "invalid_stage"


def test_acknowledging_a_receipt_removed_concurrently_reports_no_work(store):
    stage = _stage(store, CONTENT)

    with patch.object(Path, "unlink", side_effect=FileNotFoundError):
        assert store.acknowledge_stage(stage) is False


def test_listing_stages_requires_a_bounded_scan(store):
    with pytest.raises(ValueError, match="scan limit"):
        store.list_staged(
            TENANT, reconcile_before=datetime.now(timezone.utc), limit=501
        )


def test_listing_stages_requires_an_aware_reconciliation_time(store):
    with pytest.raises(ValueError, match="timezone-aware"):
        store.list_staged(TENANT, reconcile_before=datetime(2026, 1, 1), limit=10)


def test_listing_stages_is_empty_before_anything_is_staged(store):
    assert (
        store.list_staged(TENANT, reconcile_before=datetime.now(timezone.utc), limit=10)
        == []
    )


def test_listing_stages_refuses_an_unsafe_stage_root(store):
    _stage(store, CONTENT, reconcile_after=_past())

    with patch("app.services.studio_object_storage._is_link", return_value=True):
        with pytest.raises(StudioStorageError) as error:
            store.list_staged(
                TENANT, reconcile_before=datetime.now(timezone.utc), limit=10
            )
    assert error.value.code == "unsafe_object_path"


def test_listing_stages_returns_due_receipts_and_stops_at_the_limit(store):
    now = datetime.now(timezone.utc)
    for index in range(3):
        _stage(
            store,
            f"studio bytes {index}".encode(),
            reconcile_after=now - timedelta(minutes=5),
        )

    found = store.list_staged(TENANT, reconcile_before=now, limit=2)

    assert len(found) == 2
    assert all(item.reconcile_after <= now for item in found)


def test_listing_stages_ignores_files_that_are_not_receipts(store):
    now = datetime.now(timezone.utc)
    stage = _stage(store, CONTENT, reconcile_after=now - timedelta(minutes=5))
    digest = stage.object_ref.sha256
    stage_root = store.root / str(TENANT) / "studio-stages"
    (stage_root / digest[:2] / digest / "README.txt").write_bytes(b"noise")

    found = store.list_staged(TENANT, reconcile_before=now, limit=10)

    assert [item.stage_id for item in found] == [stage.stage_id]


def test_deferring_a_receipt_must_move_reconciliation_forward(store):
    stage = _stage(store, CONTENT)

    with pytest.raises(ValueError, match="move forward"):
        store.defer_stage(stage, reconcile_after=stage.reconcile_after)


def test_deferring_a_receipt_requires_an_aware_time(store):
    stage = _stage(store, CONTENT)

    with pytest.raises(ValueError, match="timezone-aware"):
        store.defer_stage(stage, reconcile_after=datetime(2030, 1, 1))


def test_deferring_a_receipt_moves_it_out_of_the_next_batch(store):
    now = datetime.now(timezone.utc)
    stage = _stage(store, CONTENT, reconcile_after=now - timedelta(minutes=5))

    assert store.defer_stage(stage, reconcile_after=now + timedelta(hours=1)) is True
    assert store.list_staged(TENANT, reconcile_before=now, limit=10) == []


def test_deferring_an_absent_receipt_reports_no_work(store):
    assert (
        store.defer_stage(
            _staged(_ref(CONTENT)),
            reconcile_after=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        is False
    )
