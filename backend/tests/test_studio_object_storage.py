"""Integrity, atomicity, and tenant-cache checks for Studio CAS storage."""

import asyncio
import hashlib
import json
import os
import threading
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.services.studio_object_storage import (
    LocalStudioObjectStore,
    StudioObjectRef,
    StudioStagedObject,
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
    target = tmp_path / str(tenant_id) / "studio-objects" / ref.object_key
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
    assert (
        restarted.list_staged(
            tenant_id,
            reconcile_before=now + timedelta(seconds=1),
            limit=10,
        )
        == []
    )


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


def _valid_object_ref(tenant_id, content=b"x", media_type="application/pdf"):
    digest = hashlib.sha256(content).hexdigest()
    return StudioObjectRef(
        tenant_id=tenant_id,
        object_key=LocalStudioObjectStore.object_key(digest),
        sha256=digest,
        byte_size=len(content),
        media_type=media_type,
    )


def test_studio_object_ref_validation_rejects_invalid_shapes():
    tenant_id = uuid.uuid4()
    digest = hashlib.sha256(b"x").hexdigest()
    key = LocalStudioObjectStore.object_key(digest)
    base = {
        "tenant_id": tenant_id,
        "object_key": key,
        "sha256": digest,
        "byte_size": 1,
        "media_type": "application/pdf",
    }
    invalid_cases = [
        ({**base, "object_key": "not-a-valid-key"}, "invalid Studio object key"),
        ({**base, "sha256": "0" * 63}, "invalid Studio object digest"),
        (
            {**base, "object_key": key.replace(digest, "0" * 64)},
            "does not match its digest",
        ),
        ({**base, "byte_size": 0}, "size must be positive"),
        ({**base, "byte_size": -1}, "size must be positive"),
        ({**base, "media_type": ""}, "invalid Studio object media type"),
        ({**base, "media_type": "a" * 101}, "invalid Studio object media type"),
        ({**base, "media_type": "application\\pdf"}, "invalid Studio object media type"),
    ]
    for overrides, message in invalid_cases:
        with pytest.raises(ValueError, match=message):
            StudioObjectRef(**overrides)


def test_studio_staged_object_validation_rejects_bad_state_and_naive_time():
    ref = _valid_object_ref(uuid.uuid4())
    aware = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="timezone-aware"):
        StudioStagedObject(
            stage_id=uuid.uuid4(),
            job_id=uuid.uuid4(),
            lease_token=uuid.uuid4(),
            object_ref=ref,
            reconcile_after=datetime.now(),
            state="materialized",
        )
    with pytest.raises(ValueError, match="invalid Studio stage state"):
        StudioStagedObject(
            stage_id=uuid.uuid4(),
            job_id=uuid.uuid4(),
            lease_token=uuid.uuid4(),
            object_ref=ref,
            reconcile_after=aware,
            state="invalid",
        )


def test_local_store_init_rejects_invalid_config_and_unsafe_root(tmp_path):
    with pytest.raises(ValueError, match="max_object_bytes must be positive"):
        LocalStudioObjectStore(tmp_path, max_object_bytes=0)
    with pytest.raises(ValueError, match="max_object_bytes must be positive"):
        LocalStudioObjectStore(tmp_path, max_object_bytes=-1)
    with patch("app.services.studio_object_storage._is_link", return_value=True):
        with pytest.raises(StudioStorageError) as unsafe:
            LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
        assert unsafe.value.code == "unsafe_storage_root"


def test_object_key_and_path_and_stage_path_validation(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    with pytest.raises(StudioStorageError) as bad_digest:
        store.object_key("not-a-hash")
    assert bad_digest.value.code == "invalid_hash"
    with pytest.raises(StudioStorageError) as bad_key:
        store._path(uuid.uuid4(), "not-a-valid-key")
    assert bad_key.value.code == "invalid_object_ref"
    key = store.object_key(hashlib.sha256(b"x").hexdigest())
    # Prefix is the second path segment (e.g. "2d"); mutate it to fail validation.
    prefix_boundary = key.index("/", key.index("/") + 1) + 1
    mangled_prefix = key[:prefix_boundary] + "ff" + key[prefix_boundary + 2 :]
    with pytest.raises(StudioStorageError) as prefix_mismatch:
        store._path(uuid.uuid4(), mangled_prefix)
    assert prefix_mismatch.value.code == "invalid_object_ref"
    with pytest.raises(StudioStorageError) as bad_stage_hash:
        store._stage_path(uuid.uuid4(), "not-a-hash", uuid.uuid4())
    assert bad_stage_hash.value.code == "invalid_hash"


def test_validate_media_type_rejects_invalid_values():
    normalized = LocalStudioObjectStore._validate_media_type("  Application/PDF  ")
    assert normalized == "application/pdf"
    with pytest.raises(StudioStorageError) as invalid:
        LocalStudioObjectStore._validate_media_type("application/pdf\r\nsecret")
    assert invalid.value.code == "invalid_media_type"
    with pytest.raises(StudioStorageError) as invalid:
        LocalStudioObjectStore._validate_media_type("a" * 101)
    assert invalid.value.code == "invalid_media_type"


def test_ensure_safe_directory_rejects_existing_file_component(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    tenant_id = uuid.uuid4()
    file_component = tmp_path / str(tenant_id) / "studio-objects"
    file_component.parent.mkdir(parents=True, exist_ok=True)
    file_component.touch()
    ref = _valid_object_ref(tenant_id)
    with pytest.raises(StudioStorageError) as unsafe:
        store.read(ref)
    assert unsafe.value.code == "unsafe_object_path"


def test_ensure_safe_directory_rejects_symlink_components(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    ref = _valid_object_ref(uuid.uuid4())

    def is_link_below_root(path):
        return path != store.root

    with patch(
        "app.services.studio_object_storage._is_link", side_effect=is_link_below_root
    ):
        with pytest.raises(StudioStorageError) as unsafe:
            store.read(ref)
        assert unsafe.value.code == "unsafe_object_path"


def test_worker_heartbeat_lifecycle(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    with pytest.raises(ValueError, match="heartbeat age is invalid"):
        store.worker_heartbeat_fresh(max_age_seconds=10)
    with pytest.raises(ValueError, match="heartbeat age is invalid"):
        store.worker_heartbeat_fresh(max_age_seconds=601)
    assert store.worker_heartbeat_fresh(max_age_seconds=30) is False

    store.touch_worker_heartbeat(healthy=True)
    assert store.worker_heartbeat_fresh(max_age_seconds=30) is True

    store.touch_worker_heartbeat(healthy=False)
    assert store.worker_heartbeat_fresh(max_age_seconds=30) is False

    heartbeat = tmp_path / ".studio-render-worker-heartbeat"
    heartbeat.write_bytes(b"x" * 65)
    assert store.worker_heartbeat_fresh(max_age_seconds=30) is False


def test_worker_heartbeat_fresh_rejects_symlink_heartbeat(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    store.touch_worker_heartbeat(healthy=True)
    with patch("app.services.studio_object_storage._is_link", return_value=True):
        assert store.worker_heartbeat_fresh(max_age_seconds=30) is False


def test_touch_worker_heartbeat_rejects_unsafe_existing_heartbeat(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    store.touch_worker_heartbeat(healthy=True)
    with patch("app.services.studio_object_storage._is_link", return_value=True):
        with pytest.raises(StudioStorageError) as unsafe:
            store.touch_worker_heartbeat(healthy=True)
        assert unsafe.value.code == "unsafe_storage_root"


def test_read_rejects_invalid_limits_and_missing_objects(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    ref = store.put(uuid.uuid4(), b"content", media_type="application/pdf")
    with pytest.raises(StudioStorageError) as invalid:
        store.read(ref, max_bytes=0)
    assert invalid.value.code == "invalid_read_limit"
    with pytest.raises(StudioStorageError) as invalid:
        store.read(ref, max_bytes=-1)
    assert invalid.value.code == "invalid_read_limit"
    missing_ref = _valid_object_ref(ref.tenant_id, content=b"other")
    with pytest.raises(StudioStorageError) as missing:
        store.read(missing_ref)
    assert missing.value.code == "object_missing"


def test_read_rejects_symlink_object(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    ref = store.put(uuid.uuid4(), b"content", media_type="application/pdf")
    target = (
        tmp_path / str(ref.tenant_id) / "studio-objects" / ref.object_key.replace("/", os.sep)
    )

    def is_link(path):
        return path == target

    with patch("app.services.studio_object_storage._is_link", side_effect=is_link):
        with pytest.raises(StudioStorageError) as unsafe:
            store.read(ref)
        assert unsafe.value.code == "unsafe_object_path"


def test_put_rejects_invalid_inputs(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    tenant_id = uuid.uuid4()
    with pytest.raises(StudioStorageError) as empty:
        store.put(tenant_id, b"", media_type="application/pdf")
    assert empty.value.code == "empty_object"
    with pytest.raises(StudioStorageError) as not_bytes:
        store.put(tenant_id, "not-bytes", media_type="application/pdf")
    assert not_bytes.value.code == "empty_object"
    with pytest.raises(StudioStorageError) as oversized:
        store.put(tenant_id, b"x" * 1025, media_type="application/pdf")
    assert oversized.value.code == "object_too_large"
    with pytest.raises(StudioStorageError) as bad_hash:
        store.put(
            tenant_id,
            b"x",
            media_type="application/pdf",
            expected_sha256="0" * 64,
        )
    assert bad_hash.value.code == "hash_mismatch"
    with pytest.raises(StudioStorageError) as bad_media:
        store.put(tenant_id, b"x", media_type="application/pdf\r\nsecret")
    assert bad_media.value.code == "invalid_media_type"


def test_stage_rejects_invalid_inputs(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    tenant_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    content = b"valid"
    digest = hashlib.sha256(content).hexdigest()
    with pytest.raises(StudioStorageError) as empty:
        store.stage(
            tenant_id,
            b"",
            job_id=uuid.uuid4(),
            lease_token=uuid.uuid4(),
            reconcile_after=now,
            media_type="application/pdf",
            expected_sha256=digest,
        )
    assert empty.value.code == "empty_object"
    with pytest.raises(StudioStorageError) as oversized:
        store.stage(
            tenant_id,
            b"x" * 1025,
            job_id=uuid.uuid4(),
            lease_token=uuid.uuid4(),
            reconcile_after=now,
            media_type="application/pdf",
            expected_sha256=hashlib.sha256(b"x" * 1025).hexdigest(),
        )
    assert oversized.value.code == "object_too_large"
    with pytest.raises(StudioStorageError) as bad_hash:
        store.stage(
            tenant_id,
            content,
            job_id=uuid.uuid4(),
            lease_token=uuid.uuid4(),
            reconcile_after=now,
            media_type="application/pdf",
            expected_sha256="0" * 64,
        )
    assert bad_hash.value.code == "hash_mismatch"
    with pytest.raises(StudioStorageError) as bad_media:
        store.stage(
            tenant_id,
            content,
            job_id=uuid.uuid4(),
            lease_token=uuid.uuid4(),
            reconcile_after=now,
            media_type="application/pdf\r\nsecret",
            expected_sha256=digest,
        )
    assert bad_media.value.code == "invalid_media_type"


def test_defer_stage_validation_and_missing_or_invalid_receipt(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    tenant_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    content = b"defer-me"
    digest = hashlib.sha256(content).hexdigest()
    valid = store.stage(
        tenant_id,
        content,
        job_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        reconcile_after=now,
        media_type="application/pdf",
        expected_sha256=digest,
    )
    with pytest.raises(ValueError, match="reconcile_after must be timezone-aware"):
        store.defer_stage(valid, reconcile_after=datetime.now())
    with pytest.raises(ValueError, match="deferred reconciliation must move forward"):
        store.defer_stage(valid, reconcile_after=now)

    later = now + timedelta(seconds=1)
    assert store.defer_stage(valid, reconcile_after=later) is True

    tampered = StudioStagedObject(
        stage_id=valid.stage_id,
        job_id=uuid.uuid4(),
        lease_token=valid.lease_token,
        object_ref=valid.object_ref,
        reconcile_after=later,
        state=valid.state,
    )
    assert (
        store.defer_stage(
            tampered, reconcile_after=later + timedelta(seconds=1)
        )
        is False
    )
    missing = StudioStagedObject(
        stage_id=uuid.uuid4(),
        job_id=valid.job_id,
        lease_token=valid.lease_token,
        object_ref=valid.object_ref,
        reconcile_after=later,
        state=valid.state,
    )
    assert (
        store.defer_stage(missing, reconcile_after=later + timedelta(seconds=1))
        is False
    )


def test_list_staged_validation_and_tenant_mismatch(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    tenant_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="reconcile_before must be timezone-aware"):
        store.list_staged(tenant_id, reconcile_before=datetime.now(), limit=10)
    with pytest.raises(ValueError, match="stage scan limit"):
        store.list_staged(tenant_id, reconcile_before=now, limit=0)
    with pytest.raises(ValueError, match="stage scan limit"):
        store.list_staged(tenant_id, reconcile_before=now, limit=501)
    assert store.list_staged(tenant_id, reconcile_before=now, limit=10) == []

    other_tenant = uuid.uuid4()
    content = b"mismatch"
    stage = store.stage(
        tenant_id,
        content,
        job_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        reconcile_after=now,
        media_type="application/pdf",
        expected_sha256=hashlib.sha256(content).hexdigest(),
    )
    receipt_path = store._stage_path(
        tenant_id, stage.object_ref.sha256, stage.stage_id
    )
    payload = json.loads(receipt_path.read_bytes())
    payload["tenant_id"] = str(other_tenant)
    receipt_path.write_text(json.dumps(payload))
    with pytest.raises(StudioStorageError) as mismatch:
        store.list_staged(
            tenant_id, reconcile_before=now + timedelta(seconds=1), limit=10
        )
    assert mismatch.value.code == "invalid_stage"


def test_delete_and_delete_staged_error_paths(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    tenant_id = uuid.uuid4()
    ref = store.put(tenant_id, b"content", media_type="application/pdf")
    missing_ref = _valid_object_ref(tenant_id, content=b"other")
    assert store.delete(missing_ref) is False

    staged_content = b"staged"
    stage = store.stage(
        tenant_id,
        staged_content,
        job_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        reconcile_after=datetime.now(timezone.utc),
        media_type="application/pdf",
        expected_sha256=hashlib.sha256(staged_content).hexdigest(),
    )
    with pytest.raises(StudioStorageError) as staged:
        store.delete(stage.object_ref)
    assert staged.value.code == "object_staged"

    sibling = store.stage(
        tenant_id,
        staged_content,
        job_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        reconcile_after=datetime.now(timezone.utc),
        media_type="application/pdf",
        expected_sha256=hashlib.sha256(staged_content).hexdigest(),
    )
    with pytest.raises(StudioStorageError) as sibling_error:
        store.delete_staged(stage)
    assert sibling_error.value.code == "object_staged"

    tampered = StudioStagedObject(
        stage_id=stage.stage_id,
        job_id=uuid.uuid4(),
        lease_token=stage.lease_token,
        object_ref=stage.object_ref,
        reconcile_after=stage.reconcile_after,
        state=stage.state,
    )
    with pytest.raises(StudioStorageError) as invalid:
        store.delete_staged(tampered)
    assert invalid.value.code == "invalid_stage"

    target = (
        tmp_path / str(ref.tenant_id) / "studio-objects" / ref.object_key.replace("/", os.sep)
    )

    def is_target_link(path):
        return path == target

    with patch(
        "app.services.studio_object_storage._is_link", side_effect=is_target_link
    ):
        with pytest.raises(StudioStorageError) as unsafe:
            store.delete(ref)
        assert unsafe.value.code == "unsafe_object_path"

    store.acknowledge_stage(stage)
    store.acknowledge_stage(sibling)
    with pytest.raises(StudioStorageError) as missing_receipt:
        store.delete_staged(stage)
    assert missing_receipt.value.code == "invalid_stage"

    fresh_content = b"fresh-deletion"
    fresh_stage = store.stage(
        tenant_id,
        fresh_content,
        job_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        reconcile_after=datetime.now(timezone.utc),
        media_type="application/pdf",
        expected_sha256=hashlib.sha256(fresh_content).hexdigest(),
    )
    target = (
        tmp_path
        / str(fresh_stage.object_ref.tenant_id)
        / "studio-objects"
        / fresh_stage.object_ref.object_key.replace("/", os.sep)
    )
    target.unlink()
    assert store.delete_staged(fresh_stage) is False


def test_read_stage_rejects_corrupt_receipts(tmp_path):
    store = LocalStudioObjectStore(tmp_path, max_object_bytes=1024)
    tenant_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    def _stage_for(content):
        return store.stage(
            tenant_id,
            content,
            job_id=uuid.uuid4(),
            lease_token=uuid.uuid4(),
            reconcile_after=now,
            media_type="application/pdf",
            expected_sha256=hashlib.sha256(content).hexdigest(),
        )

    def _corrupt(stage, transform):
        path = store._stage_path(tenant_id, stage.object_ref.sha256, stage.stage_id)
        payload = json.loads(path.read_bytes())
        transform(payload)
        path.write_text(json.dumps(payload))

    stage_bad_version = _stage_for(b"bad-version")
    _corrupt(stage_bad_version, lambda p: p.__setitem__("contract_version", 2))

    stage_missing_key = _stage_for(b"missing-key")
    _corrupt(stage_missing_key, lambda p: p.pop("state"))

    stage_extra_key = _stage_for(b"extra-key")
    _corrupt(stage_extra_key, lambda p: p.__setitem__("extra", "value"))

    stage_oversized = _stage_for(b"oversized")
    path = store._stage_path(tenant_id, stage_oversized.object_ref.sha256, stage_oversized.stage_id)
    path.write_bytes(b"x" * 4097)

    for stage in (stage_bad_version, stage_missing_key, stage_extra_key, stage_oversized):
        with pytest.raises(StudioStorageError) as invalid:
            store.list_staged(
                tenant_id, reconcile_before=now + timedelta(seconds=1), limit=10
            )
        assert invalid.value.code == "invalid_stage", stage.stage_id
