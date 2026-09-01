"""SQLite manifest/job control state with a hard no-full-text schema."""

from __future__ import annotations

import asyncio
import ctypes
import os
import sys
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import aiosqlite

from clarity_agent.config import _restrict

CONTROL_SCHEMA_VERSION = 3
STABLE_ERROR_CODES = frozenset(
    {
        "access_denied",
        "delete_failed",
        "extract_failed",
        "index_failed",
        "internal_error",
        "transient_io",
        "unsupported_format",
    }
)


SUPPORTED_LINUX_LOCAL_FILESYSTEMS = frozenset(
    {"btrfs", "ext2", "ext3", "ext4", "f2fs", "xfs", "zfs"}
)


def _windows_nonpersistent_path(path: Path) -> bool:
    if str(path).startswith(("\\\\", "//")) or not path.anchor:
        return True
    # DRIVE_FIXED is the only supported control-state volume. This rejects
    # remote, RAM, removable, optical, and unknown drive types.
    return ctypes.windll.kernel32.GetDriveTypeW(path.anchor) != 3


def _linux_filesystem_type(path: Path) -> str | None:
    try:
        entries = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    target = str(path)
    matches: list[tuple[int, str]] = []
    for line in entries:
        left, separator, right = line.partition(" - ")
        if not separator:
            continue
        fields = left.split()
        filesystem_fields = right.split()
        if len(fields) < 5 or not filesystem_fields:
            continue
        mountpoint = fields[4].replace("\\040", " ")
        if target == mountpoint or target.startswith(mountpoint.rstrip("/") + "/"):
            matches.append((len(mountpoint), filesystem_fields[0]))
    return max(matches, default=(0, None))[1]


def _is_network_filesystem(
    path: Path, *, os_name: str = os.name, platform: str = sys.platform
) -> bool:
    if os_name == "nt":
        return _windows_nonpersistent_path(path)
    if platform.startswith("linux"):
        filesystem = _linux_filesystem_type(path)
        # SQLite WAL safety is allowlisted: unreadable/unknown mount metadata
        # must fail closed rather than guessing that storage is local.
        return filesystem not in SUPPORTED_LINUX_LOCAL_FILESYSTEMS
    return True


def require_local_control_path(value: str) -> Path:
    candidate = Path(value).expanduser()
    if str(candidate).startswith(("\\\\", "//")):
        raise ValueError("search control path must use local storage")
    if not candidate.is_absolute():
        raise ValueError("search control path must be absolute")
    if _is_network_filesystem(candidate):
        raise ValueError("search control path must use local storage")
    resolved = candidate.resolve(strict=False)
    if _is_network_filesystem(resolved):
        raise ValueError("search control path must use local storage")
    return resolved


@dataclass(frozen=True)
class ManifestEntry:
    document_id: str
    share_id: str
    relative_path: str
    content_hash: str
    document_version: str
    modified_at: str
    size_bytes: int
    index_schema_version: int
    indexed_at: str | None = None


@dataclass(frozen=True)
class IndexJob:
    document_id: str
    operation: str
    status: str = "pending"
    attempts: int = 0
    next_attempt_at: float | None = None
    error_code: str | None = None
    lease_token: str | None = None
    generation: int = 0


class ControlState(ABC):
    @abstractmethod
    async def init(self) -> None: ...

    @abstractmethod
    async def upsert_manifest(self, entries: Sequence[ManifestEntry]) -> None: ...

    @abstractmethod
    async def enqueue(self, jobs: Sequence[IndexJob]) -> None: ...

    @abstractmethod
    async def claim(self, lease_seconds: int = 300) -> IndexJob | None: ...

    @abstractmethod
    async def complete(self, document_id: str, lease_token: str) -> None: ...

    @abstractmethod
    async def fail(
        self,
        document_id: str,
        lease_token: str,
        error_code: str,
        retry_at: float | None,
    ) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


class SqliteControlState(ControlState):
    """Durable scheduling state. No body, snippet, or extracted text columns."""

    def __init__(self, path: str):
        self.path = require_local_control_path(path)
        self._db: aiosqlite.Connection | None = None
        self._transaction_lock = asyncio.Lock()

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _restrict(self.path.parent, required=True)
        self._db = await aiosqlite.connect(self.path)
        try:
            _restrict(self.path, required=True)
        except Exception:
            await self._db.close()
            self._db = None
            raise
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS search_control_schema (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS search_manifest (
                document_id TEXT PRIMARY KEY,
                share_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                document_version TEXT NOT NULL,
                modified_at TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                index_schema_version INTEGER NOT NULL,
                indexed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS search_jobs (
                document_id TEXT PRIMARY KEY,
                operation TEXT NOT NULL CHECK(operation IN ('upsert','delete')),
                status TEXT NOT NULL CHECK(status IN ('pending','running','done','error')),
                attempts INTEGER NOT NULL DEFAULT 0,
                generation INTEGER NOT NULL DEFAULT 0,
                lease_until REAL,
                lease_token TEXT,
                next_attempt_at REAL,
                error_code TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_search_jobs_claim
                ON search_jobs(status, next_attempt_at, lease_until);
            """
        )
        row = await (
            await self._db.execute(
                "SELECT version FROM search_control_schema WHERE singleton=1"
            )
        ).fetchone()
        if row and int(row["version"]) > CONTROL_SCHEMA_VERSION:
            raise RuntimeError("search control schema is newer than this agent")
        columns_cursor = await self._db.execute("PRAGMA table_info(search_jobs)")
        columns = {item["name"] for item in await columns_cursor.fetchall()}
        if "lease_token" not in columns:
            await self._db.execute(
                "ALTER TABLE search_jobs ADD COLUMN lease_token TEXT"
            )
        if "generation" not in columns:
            await self._db.execute(
                "ALTER TABLE search_jobs ADD COLUMN generation INTEGER NOT NULL DEFAULT 0"
            )
        await self._db.execute("UPDATE search_jobs SET generation=1 WHERE generation<1")
        await self._db.execute(
            "INSERT INTO search_control_schema(singleton,version) VALUES(1,?) "
            "ON CONFLICT(singleton) DO UPDATE SET version=excluded.version",
            (CONTROL_SCHEMA_VERSION,),
        )
        await self._db.commit()

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("control state is not initialized")
        return self._db

    async def upsert_manifest(self, entries: Sequence[ManifestEntry]) -> None:
        async with self._transaction_lock:
            await self._upsert_manifest_unlocked(entries)

    async def _upsert_manifest_unlocked(self, entries: Sequence[ManifestEntry]) -> None:
        db = self._require_db()
        await db.executemany(
            """INSERT INTO search_manifest(document_id,share_id,relative_path,
                   content_hash,document_version,modified_at,size_bytes,
                   index_schema_version,indexed_at)
               VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(document_id) DO UPDATE SET
                   share_id=excluded.share_id,relative_path=excluded.relative_path,
                   content_hash=excluded.content_hash,
                   document_version=excluded.document_version,
                   modified_at=excluded.modified_at,
                   size_bytes=excluded.size_bytes,
                   index_schema_version=excluded.index_schema_version,
                   indexed_at=excluded.indexed_at""",
            [
                (
                    e.document_id,
                    e.share_id,
                    e.relative_path,
                    e.content_hash,
                    e.document_version,
                    e.modified_at,
                    e.size_bytes,
                    e.index_schema_version,
                    e.indexed_at,
                )
                for e in entries
            ],
        )
        await db.commit()

    async def enqueue(self, jobs: Sequence[IndexJob]) -> None:
        async with self._transaction_lock:
            await self._enqueue_unlocked(jobs)

    async def _enqueue_unlocked(self, jobs: Sequence[IndexJob]) -> None:
        db = self._require_db()
        if any(
            job.status != "pending"
            or job.attempts != 0
            or job.error_code is not None
            or job.lease_token is not None
            or job.generation != 0
            or job.operation not in {"upsert", "delete"}
            for job in jobs
        ):
            raise ValueError("enqueued search jobs must be clean and pending")
        try:
            await db.executemany(
                """INSERT INTO search_jobs(document_id,operation,status,attempts,
                       generation,next_attempt_at,error_code,lease_token)
                   VALUES(?,?,?,?,0,?,?,NULL)
                   ON CONFLICT(document_id) DO UPDATE SET
                       operation=excluded.operation,status='pending',attempts=0,
                       lease_until=NULL,lease_token=NULL,
                       next_attempt_at=excluded.next_attempt_at,
                       error_code=NULL""",
                [
                    (
                        job.document_id,
                        job.operation,
                        job.status,
                        job.attempts,
                        job.next_attempt_at,
                        job.error_code,
                    )
                    for job in jobs
                ],
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    async def claim(self, lease_seconds: int = 300) -> IndexJob | None:
        async with self._transaction_lock:
            return await self._claim_unlocked(lease_seconds)

    async def _claim_unlocked(self, lease_seconds: int) -> IndexJob | None:
        db = self._require_db()
        now = time.time()
        await db.execute("BEGIN IMMEDIATE")
        try:
            row = await (
                await db.execute(
                    """SELECT * FROM search_jobs
                   WHERE (status='pending' AND
                          (next_attempt_at IS NULL OR next_attempt_at<=?))
                      OR (status='running' AND lease_until<=?)
                   ORDER BY rowid LIMIT 1""",
                    (now, now),
                )
            ).fetchone()
            if not row:
                await db.commit()
                return None
            attempts = int(row["attempts"]) + 1
            generation = int(row["generation"]) + 1
            lease_token = uuid.uuid4().hex
            await db.execute(
                "UPDATE search_jobs SET status='running',attempts=?,lease_until=?,"
                "lease_token=?,generation=?,next_attempt_at=NULL WHERE document_id=?",
                (
                    attempts,
                    now + max(1, lease_seconds),
                    lease_token,
                    generation,
                    row["document_id"],
                ),
            )
            await db.commit()
            return IndexJob(
                document_id=row["document_id"],
                operation=row["operation"],
                status="running",
                attempts=attempts,
                lease_token=lease_token,
                generation=generation,
            )
        except Exception:
            await db.rollback()
            raise

    async def complete(self, document_id: str, lease_token: str) -> None:
        async with self._transaction_lock:
            await self._complete_unlocked(document_id, lease_token)

    async def _complete_unlocked(self, document_id: str, lease_token: str) -> None:
        db = self._require_db()
        cursor = await db.execute(
            "UPDATE search_jobs SET status='done',lease_until=NULL,lease_token=NULL,"
            "error_code=NULL WHERE document_id=? AND status='running' AND lease_token=?",
            (document_id, lease_token),
        )
        if cursor.rowcount != 1:
            await db.rollback()
            raise RuntimeError("search job lease is stale")
        await db.commit()

    async def fail(
        self,
        document_id: str,
        lease_token: str,
        error_code: str,
        retry_at: float | None,
    ) -> None:
        async with self._transaction_lock:
            await self._fail_unlocked(document_id, lease_token, error_code, retry_at)

    async def _fail_unlocked(
        self,
        document_id: str,
        lease_token: str,
        error_code: str,
        retry_at: float | None,
    ) -> None:
        db = self._require_db()
        # Persist only stable categories, never parser text or document fragments.
        safe_code = error_code if error_code in STABLE_ERROR_CODES else "internal_error"
        cursor = await db.execute(
            "UPDATE search_jobs SET status=?,lease_until=NULL,lease_token=NULL,"
            "next_attempt_at=?,error_code=? WHERE document_id=? AND status='running' "
            "AND lease_token=?",
            (
                "pending" if retry_at is not None else "error",
                retry_at,
                safe_code,
                document_id,
                lease_token,
            ),
        )
        if cursor.rowcount != 1:
            await db.rollback()
            raise RuntimeError("search job lease is stale")
        await db.commit()

    async def close(self) -> None:
        async with self._transaction_lock:
            if self._db:
                await self._db.close()
                self._db = None
