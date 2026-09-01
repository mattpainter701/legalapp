"""Tenant-scoped, content-addressed storage for Studio render artifacts.

The local implementation is intentionally small and synchronous.  It is used
behind the render worker boundary, where each operation is bounded and object
references are server-owned capabilities rather than client input.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol, TypeVar


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_OBJECT_KEY = re.compile(r"^studio-content/v1/[0-9a-f]{2}/[0-9a-f]{64}$")
_MEDIA_TYPE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,29}/" r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,89}$"
)
_StorageMutationResult = TypeVar("_StorageMutationResult")


class StudioStorageError(RuntimeError):
    """A sanitized storage error safe to map to an operational error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


async def run_storage_operation_to_completion(
    operation: Callable[[], _StorageMutationResult],
    *,
    timeout_seconds: float | None = None,
) -> _StorageMutationResult:
    """Drain a bounded synchronous storage operation before returning.

    Cancelling ``asyncio.to_thread`` never stops its worker thread.  Mutation
    callers use this primitive while holding a database-backed object fence,
    so timeout or cancellation is propagated only after the thread finishes.
    """

    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("storage mutation timeout must be positive")
    task = asyncio.create_task(asyncio.to_thread(operation))
    cancellation_requested = False
    timed_out = False
    try:
        if timeout_seconds is None:
            return await asyncio.shield(task)
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
    except TimeoutError:
        if task.done():
            return task.result()
        timed_out = True
    except asyncio.CancelledError:
        cancellation_requested = True

    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancellation_requested = True

    if cancellation_requested:
        try:
            task.result()
        except BaseException:
            pass
        raise asyncio.CancelledError
    if timed_out:
        try:
            task.result()
        except BaseException:
            pass
        raise TimeoutError
    return task.result()


async def run_storage_mutation_to_completion(
    operation: Callable[[], _StorageMutationResult],
    *,
    timeout_seconds: float | None = None,
) -> _StorageMutationResult:
    """Compatibility name emphasizing fenced mutating callers."""

    return await run_storage_operation_to_completion(
        operation, timeout_seconds=timeout_seconds
    )


@dataclass(frozen=True)
class StudioObjectRef:
    tenant_id: uuid.UUID
    object_key: str
    sha256: str
    byte_size: int
    media_type: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", uuid.UUID(str(self.tenant_id)))
        if not _OBJECT_KEY.fullmatch(self.object_key):
            raise ValueError("invalid Studio object key")
        if not _DIGEST.fullmatch(self.sha256):
            raise ValueError("invalid Studio object digest")
        if self.object_key.rsplit("/", 1)[-1] != self.sha256:
            raise ValueError("Studio object key does not match its digest")
        if self.byte_size < 1:
            raise ValueError("Studio object size must be positive")
        if len(self.media_type) > 100 or not _MEDIA_TYPE.fullmatch(self.media_type):
            raise ValueError("invalid Studio object media type")


@dataclass(frozen=True)
class StudioStagedObject:
    """Durable receipt created before CAS publication and cleared after adoption."""

    stage_id: uuid.UUID
    job_id: uuid.UUID
    lease_token: uuid.UUID
    object_ref: StudioObjectRef
    reconcile_after: datetime
    state: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage_id", uuid.UUID(str(self.stage_id)))
        object.__setattr__(self, "job_id", uuid.UUID(str(self.job_id)))
        object.__setattr__(self, "lease_token", uuid.UUID(str(self.lease_token)))
        if self.reconcile_after.tzinfo is None:
            raise ValueError("Studio stage reconciliation time must be timezone-aware")
        if self.state not in {"reserved", "materialized"}:
            raise ValueError("invalid Studio stage state")


class StudioObjectStore(Protocol):
    def touch_worker_heartbeat(self, *, healthy: bool = True) -> None: ...

    def worker_heartbeat_fresh(self, *, max_age_seconds: int) -> bool: ...

    def put(
        self,
        tenant_id: uuid.UUID,
        content: bytes,
        *,
        media_type: str,
        expected_sha256: str | None = None,
    ) -> StudioObjectRef: ...

    def read(self, ref: StudioObjectRef, *, max_bytes: int | None = None) -> bytes: ...

    def delete(self, ref: StudioObjectRef) -> bool: ...

    def stage(
        self,
        tenant_id: uuid.UUID,
        content: bytes,
        *,
        job_id: uuid.UUID,
        lease_token: uuid.UUID,
        reconcile_after: datetime,
        media_type: str,
        expected_sha256: str,
    ) -> StudioStagedObject: ...

    def acknowledge_stage(self, stage: StudioStagedObject) -> bool: ...

    def defer_stage(
        self, stage: StudioStagedObject, *, reconcile_after: datetime
    ) -> bool: ...

    def has_stages(self, ref: StudioObjectRef) -> bool: ...

    def list_staged(
        self,
        tenant_id: uuid.UUID,
        *,
        reconcile_before: datetime,
        limit: int,
    ) -> list[StudioStagedObject]: ...

    def has_other_stages(self, stage: StudioStagedObject) -> bool: ...

    def delete_staged(self, stage: StudioStagedObject) -> bool: ...


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


class LocalStudioObjectStore:
    """Atomic tenant-local CAS with verified cache hits and bounded reads.

    This rejects symlinks and junctions in and above the configured root.
    Deployment must also keep the explicit single-host root private.
    """

    def __init__(self, root: str | Path, *, max_object_bytes: int):
        supplied_root = Path(root).absolute()
        if max_object_bytes < 1:
            raise ValueError("max_object_bytes must be positive")
        existing_ancestors = (
            path for path in (supplied_root, *supplied_root.parents) if path.exists()
        )
        if any(_is_link(path) for path in existing_ancestors):
            raise StudioStorageError(
                "unsafe_storage_root", "Studio storage root is not a safe directory"
            )
        self.root = supplied_root
        self.max_object_bytes = int(max_object_bytes)
        self._lock = threading.RLock()
        self._stage_scan_cursors: dict[uuid.UUID, str] = {}

    def touch_worker_heartbeat(self, *, healthy: bool = True) -> None:
        """Atomically publish liveness on the API/worker shared CAS."""

        with self._lock:
            self._ensure_safe_directory(self.root)
            target = self.root / ".studio-render-worker-heartbeat"
            if target.exists() and _is_link(target):
                raise StudioStorageError(
                    "unsafe_storage_root", "Studio worker heartbeat is unsafe"
                )
            descriptor, temporary = tempfile.mkstemp(
                prefix=".studio-heartbeat-", dir=self.root
            )
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    state = "ok" if healthy else "maintenance_failed"
                    handle.write(f"{state}:{time.time_ns()}".encode("ascii"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def worker_heartbeat_fresh(self, *, max_age_seconds: int) -> bool:
        if not 20 <= max_age_seconds <= 600:
            raise ValueError("Studio worker heartbeat age is invalid")
        target = self.root / ".studio-render-worker-heartbeat"
        try:
            if not target.is_file() or _is_link(target) or target.stat().st_size > 64:
                return False
            state = target.read_bytes()
            age = time.time() - target.stat().st_mtime
            return state.startswith(b"ok:") and -5 <= age <= max_age_seconds
        except OSError:
            return False

    @staticmethod
    def object_key(sha256: str) -> str:
        if not _DIGEST.fullmatch(sha256):
            raise StudioStorageError("invalid_hash", "invalid Studio content digest")
        return f"studio-content/v1/{sha256[:2]}/{sha256}"

    @staticmethod
    def _digest(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _validate_media_type(media_type: str) -> str:
        normalized = str(media_type).strip().lower()
        if len(normalized) > 100 or not _MEDIA_TYPE.fullmatch(normalized):
            raise StudioStorageError("invalid_media_type", "invalid Studio media type")
        return normalized

    def _ensure_safe_directory(self, directory: Path) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o750)
        if not self.root.is_dir() or _is_link(self.root):
            raise StudioStorageError(
                "unsafe_storage_root", "Studio storage root is not a safe directory"
            )
        relative = directory.relative_to(self.root)
        current = self.root
        for component in relative.parts:
            current = current / component
            if current.exists():
                if not current.is_dir() or _is_link(current):
                    raise StudioStorageError(
                        "unsafe_object_path", "Studio object path is not safe"
                    )
            else:
                current.mkdir(mode=0o750, exist_ok=True)
                if not current.is_dir() or _is_link(current):
                    raise StudioStorageError(
                        "unsafe_object_path", "Studio object path is not safe"
                    )

    def _path(self, tenant_id: uuid.UUID, object_key: str) -> Path:
        tenant = uuid.UUID(str(tenant_id))
        if not _OBJECT_KEY.fullmatch(object_key):
            raise StudioStorageError(
                "invalid_object_ref", "invalid Studio object reference"
            )
        digest = object_key.rsplit("/", 1)[-1]
        if object_key.split("/")[2] != digest[:2]:
            raise StudioStorageError(
                "invalid_object_ref", "invalid Studio object reference"
            )
        target = self.root / str(tenant) / "studio-objects" / Path(object_key)
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise StudioStorageError(
                "invalid_object_ref", "invalid Studio object reference"
            ) from exc
        return target

    def _stage_path(
        self,
        tenant_id: uuid.UUID,
        sha256: str,
        stage_id: uuid.UUID,
    ) -> Path:
        tenant = uuid.UUID(str(tenant_id))
        if not _DIGEST.fullmatch(sha256):
            raise StudioStorageError("invalid_hash", "invalid Studio content digest")
        stage = uuid.UUID(str(stage_id))
        target = (
            self.root
            / str(tenant)
            / "studio-stages"
            / sha256[:2]
            / sha256
            / f"{stage}.json"
        )
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise StudioStorageError(
                "invalid_stage", "invalid Studio staging receipt"
            ) from exc
        return target

    def _write_stage(self, stage: StudioStagedObject) -> None:
        target = self._stage_path(
            stage.object_ref.tenant_id,
            stage.object_ref.sha256,
            stage.stage_id,
        )
        self._ensure_safe_directory(target.parent)
        payload = {
            "contract_version": 1,
            "stage_id": str(stage.stage_id),
            "job_id": str(stage.job_id),
            "lease_token": str(stage.lease_token),
            "tenant_id": str(stage.object_ref.tenant_id),
            "object_key": stage.object_ref.object_key,
            "sha256": stage.object_ref.sha256,
            "byte_size": stage.object_ref.byte_size,
            "media_type": stage.object_ref.media_type,
            "reconcile_after": stage.reconcile_after.isoformat(),
            "state": stage.state,
        }
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=".studio-stage-",
                delete=False,
            ) as handle:
                temporary_name = handle.name
                json.dump(
                    payload,
                    handle,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
            temporary_name = None
            self._sync_directory(target.parent)
        finally:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink()
                except FileNotFoundError:
                    pass

    def _read_stage(self, path: Path) -> StudioStagedObject:
        if _is_link(path):
            raise StudioStorageError(
                "unsafe_object_path", "Studio stage path is not safe"
            )
        try:
            raw = path.read_bytes()
            if len(raw) > 4096:
                raise ValueError("stage receipt too large")
            payload = json.loads(raw.decode("utf-8"))
            if (
                set(payload)
                != {
                    "contract_version",
                    "stage_id",
                    "job_id",
                    "lease_token",
                    "tenant_id",
                    "object_key",
                    "sha256",
                    "byte_size",
                    "media_type",
                    "reconcile_after",
                    "state",
                }
                or payload["contract_version"] != 1
            ):
                raise ValueError("invalid stage receipt")
            ref = StudioObjectRef(
                tenant_id=payload["tenant_id"],
                object_key=payload["object_key"],
                sha256=payload["sha256"],
                byte_size=payload["byte_size"],
                media_type=payload["media_type"],
            )
            stage = StudioStagedObject(
                stage_id=payload["stage_id"],
                job_id=payload["job_id"],
                lease_token=payload["lease_token"],
                object_ref=ref,
                reconcile_after=datetime.fromisoformat(payload["reconcile_after"]),
                state=payload["state"],
            )
            expected = self._stage_path(ref.tenant_id, ref.sha256, stage.stage_id)
            if expected != path:
                raise ValueError("stage receipt path mismatch")
            return stage
        except StudioStorageError:
            raise
        except Exception as exc:
            raise StudioStorageError(
                "invalid_stage", "Studio staging receipt is invalid"
            ) from exc

    def _bounded_verified_read(
        self, path: Path, *, digest: str, expected_size: int | None, limit: int
    ) -> bytes:
        if limit < 1:
            raise StudioStorageError("invalid_read_limit", "invalid Studio read limit")
        if _is_link(path):
            raise StudioStorageError(
                "unsafe_object_path", "Studio object path is not safe"
            )
        try:
            size = path.stat().st_size
        except FileNotFoundError as exc:
            raise StudioStorageError(
                "object_missing", "Studio output is unavailable"
            ) from exc
        if size > limit:
            raise StudioStorageError(
                "object_too_large", "Studio output exceeds its size limit"
            )
        if expected_size is not None and size != expected_size:
            raise StudioStorageError(
                "hash_mismatch", "Studio output failed its integrity check"
            )
        with path.open("rb") as handle:
            content = handle.read(limit + 1)
        if len(content) > limit:
            raise StudioStorageError(
                "object_too_large", "Studio output exceeds its size limit"
            )
        if expected_size is not None and len(content) != expected_size:
            raise StudioStorageError(
                "hash_mismatch", "Studio output failed its integrity check"
            )
        if self._digest(content) != digest:
            raise StudioStorageError(
                "hash_mismatch", "Studio output failed its integrity check"
            )
        return content

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
        try:
            descriptor = os.open(directory, flags)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def put(
        self,
        tenant_id: uuid.UUID,
        content: bytes,
        *,
        media_type: str,
        expected_sha256: str | None = None,
    ) -> StudioObjectRef:
        if not isinstance(content, bytes) or not content:
            raise StudioStorageError("empty_object", "Studio output is empty")
        if len(content) > self.max_object_bytes:
            raise StudioStorageError(
                "object_too_large", "Studio output exceeds its size limit"
            )
        digest = self._digest(content)
        if expected_sha256 is not None and expected_sha256 != digest:
            raise StudioStorageError(
                "hash_mismatch", "Studio output failed its integrity check"
            )
        normalized_media_type = self._validate_media_type(media_type)
        object_key = self.object_key(digest)
        target = self._path(tenant_id, object_key)

        with self._lock:
            self._ensure_safe_directory(target.parent)
            if target.exists():
                self._bounded_verified_read(
                    target,
                    digest=digest,
                    expected_size=len(content),
                    limit=self.max_object_bytes,
                )
            else:
                temporary_name: str | None = None
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="wb", dir=target.parent, prefix=".studio-", delete=False
                    ) as handle:
                        temporary_name = handle.name
                        handle.write(content)
                        handle.flush()
                        os.fsync(handle.fileno())
                    if _is_link(target.parent):
                        raise StudioStorageError(
                            "unsafe_object_path", "Studio object path is not safe"
                        )
                    try:
                        # Publish-if-absent keeps the winning CAS inode stable.
                        # Unconditional replace lets another process swap the
                        # final path while a winner is verifying it on Windows.
                        os.link(temporary_name, target)
                    except FileExistsError:
                        pass
                    Path(temporary_name).unlink()
                    temporary_name = None
                    # A loser can durably flush the winning directory entry if
                    # the winner exits between link publication and fsync.
                    self._sync_directory(target.parent)
                    self._bounded_verified_read(
                        target,
                        digest=digest,
                        expected_size=len(content),
                        limit=self.max_object_bytes,
                    )
                finally:
                    if temporary_name is not None:
                        try:
                            Path(temporary_name).unlink()
                        except FileNotFoundError:
                            pass

        return StudioObjectRef(
            tenant_id=uuid.UUID(str(tenant_id)),
            object_key=object_key,
            sha256=digest,
            byte_size=len(content),
            media_type=normalized_media_type,
        )

    def read(self, ref: StudioObjectRef, *, max_bytes: int | None = None) -> bytes:
        limit = self.max_object_bytes
        if max_bytes is not None:
            if (
                isinstance(max_bytes, bool)
                or not isinstance(max_bytes, int)
                or max_bytes < 1
            ):
                raise StudioStorageError(
                    "invalid_read_limit", "invalid Studio read limit"
                )
            limit = min(limit, max_bytes)
        target = self._path(ref.tenant_id, ref.object_key)
        with self._lock:
            self._ensure_safe_directory(target.parent)
            return self._bounded_verified_read(
                target,
                digest=ref.sha256,
                expected_size=ref.byte_size,
                limit=limit,
            )

    def stage(
        self,
        tenant_id: uuid.UUID,
        content: bytes,
        *,
        job_id: uuid.UUID,
        lease_token: uuid.UUID,
        reconcile_after: datetime,
        media_type: str,
        expected_sha256: str,
    ) -> StudioStagedObject:
        if not isinstance(content, bytes) or not content:
            raise StudioStorageError("empty_object", "Studio output is empty")
        if len(content) > self.max_object_bytes:
            raise StudioStorageError(
                "object_too_large", "Studio output exceeds its size limit"
            )
        digest = self._digest(content)
        if expected_sha256 != digest:
            raise StudioStorageError(
                "hash_mismatch", "Studio output failed its integrity check"
            )
        ref = StudioObjectRef(
            tenant_id=tenant_id,
            object_key=self.object_key(digest),
            sha256=digest,
            byte_size=len(content),
            media_type=self._validate_media_type(media_type),
        )
        reserved = StudioStagedObject(
            stage_id=uuid.uuid4(),
            job_id=job_id,
            lease_token=lease_token,
            object_ref=ref,
            reconcile_after=reconcile_after,
            state="reserved",
        )
        with self._lock:
            self._write_stage(reserved)
            materialized_ref = self.put(
                tenant_id,
                content,
                media_type=media_type,
                expected_sha256=expected_sha256,
            )
            materialized = StudioStagedObject(
                stage_id=reserved.stage_id,
                job_id=reserved.job_id,
                lease_token=reserved.lease_token,
                object_ref=materialized_ref,
                reconcile_after=reserved.reconcile_after,
                state="materialized",
            )
            self._write_stage(materialized)
            return materialized

    def acknowledge_stage(self, stage: StudioStagedObject) -> bool:
        target = self._stage_path(
            stage.object_ref.tenant_id,
            stage.object_ref.sha256,
            stage.stage_id,
        )
        with self._lock:
            if not target.exists():
                return False
            persisted = self._read_stage(target)
            if persisted != stage:
                raise StudioStorageError(
                    "invalid_stage", "Studio staging receipt is invalid"
                )
            try:
                target.unlink()
            except FileNotFoundError:
                return False
            self._sync_directory(target.parent)
            return True

    def defer_stage(
        self, stage: StudioStagedObject, *, reconcile_after: datetime
    ) -> bool:
        """Durably move a retained receipt out of the next reconciliation batch."""

        if reconcile_after.tzinfo is None:
            raise ValueError("reconcile_after must be timezone-aware")
        if reconcile_after <= stage.reconcile_after:
            raise ValueError("deferred reconciliation must move forward")
        target = self._stage_path(
            stage.object_ref.tenant_id,
            stage.object_ref.sha256,
            stage.stage_id,
        )
        with self._lock:
            if not target.exists():
                return False
            persisted = self._read_stage(target)
            if persisted != stage:
                return False
            self._write_stage(
                StudioStagedObject(
                    stage_id=stage.stage_id,
                    job_id=stage.job_id,
                    lease_token=stage.lease_token,
                    object_ref=stage.object_ref,
                    reconcile_after=reconcile_after,
                    state=stage.state,
                )
            )
            return True

    def list_staged(
        self,
        tenant_id: uuid.UUID,
        *,
        reconcile_before: datetime,
        limit: int,
    ) -> list[StudioStagedObject]:
        if reconcile_before.tzinfo is None:
            raise ValueError("reconcile_before must be timezone-aware")
        if not 1 <= limit <= 500:
            raise ValueError("stage scan limit must be between 1 and 500")
        tenant = uuid.UUID(str(tenant_id))
        stage_root = self.root / str(tenant) / "studio-stages"
        with self._lock:
            if not stage_root.exists():
                return []
            if not stage_root.is_dir() or _is_link(stage_root):
                raise StudioStorageError(
                    "unsafe_object_path", "Studio stage path is not safe"
                )
            found: list[StudioStagedObject] = []
            scanned = 0
            scan_budget = max(5_000, limit * 100)
            cursor = self._stage_scan_cursors.get(tenant)
            for directory, directories, files in os.walk(stage_root, followlinks=False):
                base = Path(directory)
                if _is_link(base):
                    raise StudioStorageError(
                        "unsafe_object_path", "Studio stage path is not safe"
                    )
                unsafe_directories = [
                    name for name in directories if _is_link(base / name)
                ]
                if unsafe_directories:
                    raise StudioStorageError(
                        "unsafe_object_path", "Studio stage path is not safe"
                    )
                directories[:] = sorted(directories)
                for name in sorted(files):
                    if not name.endswith(".json"):
                        continue
                    relative_name = (base / name).relative_to(stage_root).as_posix()
                    if cursor is not None and relative_name <= cursor:
                        continue
                    scanned += 1
                    self._stage_scan_cursors[tenant] = relative_name
                    stage = self._read_stage(base / name)
                    if stage.object_ref.tenant_id != tenant:
                        raise StudioStorageError(
                            "invalid_stage", "Studio staging receipt is invalid"
                        )
                    if stage.reconcile_after <= reconcile_before:
                        found.append(stage)
                        if len(found) >= limit:
                            return found
                    if scanned >= scan_budget:
                        return found
            self._stage_scan_cursors.pop(tenant, None)
            return found

    def has_other_stages(self, stage: StudioStagedObject) -> bool:
        directory = self._stage_path(
            stage.object_ref.tenant_id,
            stage.object_ref.sha256,
            stage.stage_id,
        ).parent
        with self._lock:
            return self._has_stage_receipts(
                stage.object_ref,
                directory=directory,
                exclude_stage_id=stage.stage_id,
            )

    def _has_stage_receipts(
        self,
        ref: StudioObjectRef,
        *,
        directory: Path | None = None,
        exclude_stage_id: uuid.UUID | None = None,
    ) -> bool:
        directory = (
            directory
            or self._stage_path(
                ref.tenant_id,
                ref.sha256,
                uuid.UUID(int=0),
            ).parent
        )
        if not directory.exists():
            return False
        if not directory.is_dir() or _is_link(directory):
            raise StudioStorageError(
                "unsafe_object_path", "Studio stage path is not safe"
            )
        seen = 0
        for path in directory.glob("*.json"):
            seen += 1
            if seen > 1_000:
                raise StudioStorageError(
                    "stage_scan_limit",
                    "Studio staging receipts exceed their scan limit",
                )
            persisted = self._read_stage(path)
            if (
                persisted.object_ref.tenant_id != ref.tenant_id
                or persisted.object_ref.sha256 != ref.sha256
            ):
                raise StudioStorageError(
                    "invalid_stage", "Studio staging receipt is invalid"
                )
            if persisted.stage_id != exclude_stage_id:
                return True
        return False

    def has_stages(self, ref: StudioObjectRef) -> bool:
        with self._lock:
            return self._has_stage_receipts(ref)

    def delete_staged(self, stage: StudioStagedObject) -> bool:
        """Delete an unreferenced last-stage CAS object and its receipt together."""

        receipt = self._stage_path(
            stage.object_ref.tenant_id,
            stage.object_ref.sha256,
            stage.stage_id,
        )
        target = self._path(stage.object_ref.tenant_id, stage.object_ref.object_key)
        with self._lock:
            if not receipt.exists() or self._read_stage(receipt) != stage:
                raise StudioStorageError(
                    "invalid_stage", "Studio staging receipt is invalid"
                )
            if self._has_stage_receipts(
                stage.object_ref,
                directory=receipt.parent,
                exclude_stage_id=stage.stage_id,
            ):
                raise StudioStorageError(
                    "object_staged", "Studio output is still being materialized"
                )
            if _is_link(target):
                raise StudioStorageError(
                    "unsafe_object_path", "Studio object path is not safe"
                )
            removed = True
            try:
                target.unlink()
            except FileNotFoundError:
                removed = False
            receipt.unlink()
            self._sync_directory(target.parent)
            self._sync_directory(receipt.parent)
            return removed

    def delete(self, ref: StudioObjectRef) -> bool:
        target = self._path(ref.tenant_id, ref.object_key)
        with self._lock:
            self._ensure_safe_directory(target.parent)
            if self._has_stage_receipts(ref):
                raise StudioStorageError(
                    "object_staged", "Studio output is still being materialized"
                )
            if _is_link(target):
                raise StudioStorageError(
                    "unsafe_object_path", "Studio object path is not safe"
                )
            try:
                target.unlink()
            except FileNotFoundError:
                return False
            self._sync_directory(target.parent)
            return True
