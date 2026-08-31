"""Tenant-scoped, content-addressed storage for Studio render artifacts.

The local implementation is intentionally small and synchronous.  It is used
behind the render worker boundary, where each operation is bounded and object
references are server-owned capabilities rather than client input.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_OBJECT_KEY = re.compile(r"^studio-content/v1/[0-9a-f]{2}/[0-9a-f]{64}$")
_MEDIA_TYPE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,29}/"
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,89}$"
)


class StudioStorageError(RuntimeError):
    """A sanitized storage error safe to map to an operational error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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


class StudioObjectStore(Protocol):
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


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


class LocalStudioObjectStore:
    """Atomic tenant-local CAS with verified cache hits and bounded reads.

    This rejects symlinks and junctions inside the configured storage root.
    That protects the owned directory layout; deployment must also ensure the
    configured root itself is private and not replaceable by untrusted users.
    """

    def __init__(self, root: str | Path, *, max_object_bytes: int):
        supplied_root = Path(root).absolute()
        if max_object_bytes < 1:
            raise ValueError("max_object_bytes must be positive")
        if supplied_root.exists() and _is_link(supplied_root):
            raise StudioStorageError(
                "unsafe_storage_root", "Studio storage root is not a safe directory"
            )
        self.root = supplied_root
        self.max_object_bytes = int(max_object_bytes)
        self._lock = threading.RLock()

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

    def _bounded_verified_read(
        self, path: Path, *, digest: str, expected_size: int | None, limit: int
    ) -> bytes:
        if limit < 1:
            raise StudioStorageError("invalid_read_limit", "invalid Studio read limit")
        if _is_link(path):
            raise StudioStorageError("unsafe_object_path", "Studio object path is not safe")
        try:
            size = path.stat().st_size
        except FileNotFoundError as exc:
            raise StudioStorageError("object_missing", "Studio output is unavailable") from exc
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
                    os.replace(temporary_name, target)
                    temporary_name = None
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
            if int(max_bytes) < 1:
                raise StudioStorageError("invalid_read_limit", "invalid Studio read limit")
            limit = min(limit, int(max_bytes))
        target = self._path(ref.tenant_id, ref.object_key)
        with self._lock:
            self._ensure_safe_directory(target.parent)
            return self._bounded_verified_read(
                target,
                digest=ref.sha256,
                expected_size=ref.byte_size,
                limit=limit,
            )

    def delete(self, ref: StudioObjectRef) -> bool:
        target = self._path(ref.tenant_id, ref.object_key)
        with self._lock:
            self._ensure_safe_directory(target.parent)
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
