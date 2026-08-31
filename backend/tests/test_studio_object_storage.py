"""Integrity, atomicity, and tenant-cache checks for Studio CAS storage."""

import asyncio
import hashlib
import threading
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.services.studio_object_storage import (
    LocalStudioObjectStore,
    StudioObjectRef,
    StudioStorageError,
    run_storage_mutation_to_completion,
    run_storage_operation_to_completion,
)


@pytest.mark.asyncio
async def test_timed_out_storage_mutation_is_drained_before_timeout_propagates():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def mutate():
        started.set()
        release.wait(timeout=2)
        finished.set()
        return "materialized"

    task = asyncio.create_task(
        run_storage_mutation_to_completion(mutate, timeout_seconds=0.01)
    )
    assert await asyncio.to_thread(started.wait, 1)
    await asyncio.sleep(0.02)
    assert not task.done()
    release.set()
    with pytest.raises(TimeoutError):
        await task
    assert finished.is_set()


@pytest.mark.asyncio
async def test_cancelled_storage_operation_is_drained_before_cancellation():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def read():
        started.set()
        release.wait(timeout=2)
        finished.set()
        return b"verified"

    task = asyncio.create_task(run_storage_operation_to_completion(read))
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


def _process_put(root, tenant_id, content):
    store = LocalStudioObjectStore(root, max_object_bytes=1024)
    return store.put(
        uuid.UUID(tenant_id),
        content,
        media_type="application/pdf",
    ).object_key


def test_verified_content_addressed_cache_is_tenant_scoped(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    first_tenant = uuid.uuid4()
    second_tenant = uuid.uuid4()
    content = b"immutable Studio output"
    digest = hashlib.sha256(content).hexdigest()
    first = store.put(
        first_tenant,
        content,
        media_type="application/pdf",
        expected_sha256=digest,
    )
    replay = store.put(first_tenant, content, media_type="application/pdf")
    other = store.put(second_tenant, content, media_type="application/pdf")
    assert first.object_key == replay.object_key == other.object_key
    assert first.tenant_id != other.tenant_id
    assert store.read(first) == content
    assert store.read(other) == content
    assert len(list(tmp_path.rglob(digest))) == 2


def test_concurrent_identical_writes_publish_one_verified_object(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=4096)
    tenant_id = uuid.uuid4()
    content = b"concurrent output" * 20

    def put():
        return store.put(tenant_id, content, media_type="application/pdf")

    with ThreadPoolExecutor(max_workers=8) as pool:
        refs = list(pool.map(lambda _index: put(), range(16)))
    assert len({ref.object_key for ref in refs}) == 1
    assert store.read(refs[0]) == content
    assert not list(tmp_path.rglob(".studio-*"))


def test_multi_process_first_write_is_idempotent(tmp_path):
    tenant_id = uuid.uuid4()
    root = tmp_path / "multi-process-store"
    content = b"same bytes from independent workers"
    with ProcessPoolExecutor(max_workers=4) as pool:
        keys = list(
            pool.map(
                _process_put,
                [str(root)] * 8,
                [str(tenant_id)] * 8,
                [content] * 8,
            )
        )
    assert len(set(keys)) == 1
    store = LocalStudioObjectStore(root, max_object_bytes=1024)
    digest = hashlib.sha256(content).hexdigest()
    ref = StudioObjectRef(
        tenant_id=tenant_id,
        object_key=keys[0],
        sha256=digest,
        byte_size=len(content),
        media_type="application/pdf",
    )
    assert store.read(ref) == content


def test_hash_size_and_corrupted_cache_fail_closed(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=32)
    tenant_id = uuid.uuid4()
    with pytest.raises(StudioStorageError) as mismatch:
        store.put(
            tenant_id,
            b"safe",
            media_type="application/pdf",
            expected_sha256="0" * 64,
        )
    assert mismatch.value.code == "hash_mismatch"
    with pytest.raises(StudioStorageError) as oversized:
        store.put(tenant_id, b"x" * 33, media_type="application/pdf")
    assert oversized.value.code == "object_too_large"

    ref = store.put(tenant_id, b"verified", media_type="application/pdf")
    target = (
        tmp_path
        / str(tenant_id)
        / "studio-objects"
        / ref.object_key
    )
    target.write_bytes(b"tampered")
    with pytest.raises(StudioStorageError) as corrupted:
        store.read(ref)
    assert corrupted.value.code == "hash_mismatch"


def test_bounded_read_and_reference_shape(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=128)
    ref = store.put(uuid.uuid4(), b"12345678", media_type="application/pdf")
    with pytest.raises(StudioStorageError) as bounded:
        store.read(ref, max_bytes=4)
    assert bounded.value.code == "object_too_large"
    with pytest.raises(StudioStorageError) as invalid_limit:
        store.read(ref, max_bytes=True)
    assert invalid_limit.value.code == "invalid_read_limit"
    with pytest.raises(ValueError):
        StudioObjectRef(
            tenant_id=ref.tenant_id,
            object_key="../../private/source.pdf",
            sha256=ref.sha256,
            byte_size=ref.byte_size,
            media_type=ref.media_type,
        )


def test_docx_media_type_is_supported_without_control_text(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=128)
    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    ref = store.put(uuid.uuid4(), b"docx-output", media_type=media_type)
    assert ref.media_type == media_type
    with pytest.raises(StudioStorageError) as invalid:
        store.put(uuid.uuid4(), b"bad", media_type="application/pdf\r\nsecret")
    assert invalid.value.code == "invalid_media_type"


def test_pre_adoption_stage_is_durable_and_acknowledged_after_commit(tmp_path):
    tenant_id = uuid.uuid4()
    job_id = uuid.uuid4()
    lease_token = uuid.uuid4()
    now = datetime.now(timezone.utc)
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    content = b"durably staged output"
    digest = hashlib.sha256(content).hexdigest()
    stage = store.stage(
        tenant_id,
        content,
        job_id=job_id,
        lease_token=lease_token,
        reconcile_after=now,
        media_type="application/pdf",
        expected_sha256=digest,
    )
    assert stage.state == "materialized"
    assert store.read(stage.object_ref) == content

    restarted = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    pending = restarted.list_staged(
        tenant_id,
        reconcile_before=now + timedelta(seconds=1),
        limit=10,
    )
    assert pending == [stage]
    assert restarted.acknowledge_stage(stage) is True
    assert restarted.list_staged(
        tenant_id,
        reconcile_before=now + timedelta(seconds=1),
        limit=10,
    ) == []


def test_oversized_stage_is_rejected_before_receipt_creation(tmp_path):
    tenant_id = uuid.uuid4()
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=4)
    content = b"12345"
    with pytest.raises(StudioStorageError) as oversized:
        store.stage(
            tenant_id,
            content,
            job_id=uuid.uuid4(),
            lease_token=uuid.uuid4(),
            reconcile_after=datetime.now(timezone.utc),
            media_type="application/pdf",
            expected_sha256=hashlib.sha256(content).hexdigest(),
        )
    assert oversized.value.code == "object_too_large"
    assert not list(tmp_path.rglob("*.json"))


def test_shared_cas_stages_prevent_early_orphan_deletion(tmp_path):
    tenant_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    content = b"shared staged output"
    digest = hashlib.sha256(content).hexdigest()
    first = store.stage(
        tenant_id,
        content,
        job_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        reconcile_after=now,
        media_type="application/pdf",
        expected_sha256=digest,
    )
    second = store.stage(
        tenant_id,
        content,
        job_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        reconcile_after=now,
        media_type="application/pdf",
        expected_sha256=digest,
    )
    assert store.has_other_stages(first) is True
    with pytest.raises(StudioStorageError) as staged_delete:
        store.delete(first.object_ref)
    assert staged_delete.value.code == "object_staged"
    with pytest.raises(StudioStorageError) as sibling_delete:
        store.delete_staged(first)
    assert sibling_delete.value.code == "object_staged"
    assert store.acknowledge_stage(first) is True
    assert store.has_other_stages(second) is False
    assert store.read(second.object_ref) == content
    assert store.delete_staged(second) is True
    assert store.has_stages(second.object_ref) is False
    with pytest.raises(StudioStorageError) as missing:
        store.read(second.object_ref)
    assert missing.value.code == "object_missing"
