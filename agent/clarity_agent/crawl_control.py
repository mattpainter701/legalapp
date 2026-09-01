"""Durable source-root crawl and freshness control plane.

This module deliberately owns no parser or search-engine implementation.  It
turns source observations into leased, idempotent handoffs that local extractor
and index adapters can consume.  SQLite is the authority for restart recovery;
change notifications are only hints and full reconciliation remains mandatory.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol

import aiosqlite

from clarity_agent.schedule import due_for_scan
from clarity_agent.search_control import require_local_control_path
from clarity_agent.config import _restrict


SCHEMA_VERSION = 4
DEFAULT_MAX_FILE_SIZE = 32 * 1024 * 1024
COMPLETED_JOB_RETENTION = 10_000
ARTIFACT_REFERENCE_RE = re.compile(r"artifact:[0-9a-f]{64}\Z")
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
STABLE_ERROR_CODES = frozenset(
    {
        "access_denied",
        "invalid_input",
        "lease_lost",
        "source_io",
        "internal_error",
        "migrated_error",
    }
)


def stable_error_code(error: BaseException) -> str:
    if isinstance(error, LeaseLost):
        return "lease_lost"
    if isinstance(error, PermissionError):
        return "access_denied"
    if isinstance(error, OSError):
        return "source_io"
    if isinstance(error, ValueError):
        return "invalid_input"
    return "internal_error"


def valid_artifact_fields(reference: str | None, digest: str | None) -> bool:
    return bool(
        reference
        and digest
        and ARTIFACT_REFERENCE_RE.fullmatch(reference)
        and DIGEST_RE.fullmatch(digest)
    )


class JobKind(StrEnum):
    DISCOVER = "discover"
    STAT = "stat"
    EXTRACT = "extract"
    INDEX = "index"
    DELETE = "delete"
    ACL_REFRESH = "acl_refresh"


class JobState(StrEnum):
    READY = "ready"
    LEASED = "leased"
    RETRY = "retry"
    DEAD = "dead"
    DONE = "done"


@dataclass(frozen=True)
class SourceRoot:
    source_id: str
    root: str
    schedule: str = "*/15 * * * *"
    matter_ids: tuple[str, ...] = ()
    max_workers: int = 2
    max_open_handles: int = 4
    read_bytes_per_second: int = 8 * 1024 * 1024
    max_pending_jobs: int = 10_000
    max_file_size: int = DEFAULT_MAX_FILE_SIZE
    case_sensitive_paths: bool = False
    enabled: bool = True


@dataclass(frozen=True)
class FileStat:
    path: str
    size: int
    modified_ns: int
    created_ns: int | None = None
    stable_id: str | None = None
    acl_version: str | None = None

    @property
    def identity(self) -> str:
        if self.stable_id:
            return f"stable:{self.stable_id}"
        normalized = str(PureWindowsPath(self.path)).casefold()
        return "path:" + hashlib.sha256(normalized.encode()).hexdigest()

    def identity_for(self, source: SourceRoot) -> str:
        if self.stable_id:
            return f"stable:{self.stable_id}"
        normalized = str(PurePosixPath(self.path))
        if not source.case_sensitive_paths:
            normalized = normalized.casefold()
        return "path:" + hashlib.sha256(normalized.encode()).hexdigest()

    @property
    def stat_version(self) -> str:
        payload = f"{self.size}:{self.modified_ns}:{self.created_ns or 0}"
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class StableContent:
    source_id: str
    file_id: str
    path: str
    content_version: str
    mutation_generation: int
    fingerprint: str
    content: bytes
    matter_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractedDocument:
    content_version: str
    mutation_generation: int
    artifact_ref: str
    artifact_hash: str


@dataclass(frozen=True)
class IndexDocument:
    source_id: str
    file_id: str
    path: str
    content_version: str
    mutation_generation: int
    artifact_ref: str
    artifact_hash: str
    acl_version: str | None
    matter_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChangeHint:
    source_id: str
    path: str | None = None
    cursor: str | None = None
    overflow: bool = False
    disconnected: bool = False


class SourceAdapter(Protocol):
    async def discover(self, source: SourceRoot) -> AsyncIterator[FileStat]: ...

    async def stat(self, source: SourceRoot, path: str) -> FileStat | None: ...

    def read_chunks(
        self, source: SourceRoot, path: str, chunk_size: int
    ) -> AsyncIterator[bytes]: ...


class ExtractionSink(Protocol):
    async def extract(self, request: StableContent) -> ExtractedDocument: ...


class IndexSink(Protocol):
    async def upsert(self, document: IndexDocument) -> None: ...

    async def delete(
        self, source_id: str, file_id: str, mutation_generation: int
    ) -> None: ...


class AclRefreshSink(Protocol):
    """Metadata handoff only; authorization trimming remains out of scope."""

    async def refresh(
        self,
        source_id: str,
        file_id: str,
        path: str,
        acl_version: str | None,
        mutation_generation: int,
    ) -> None: ...


class ChangeHintAdapter(Protocol):
    """Capability adapter for Windows USN, SMB change notify, or equivalents."""

    capability: str

    async def changes(
        self, source: SourceRoot, cursor: str | None
    ) -> AsyncIterator[ChangeHint]: ...


class CallbackHintAdapter:
    """Thin capability boundary around an OS/provider-specific hint stream."""

    def __init__(
        self,
        capability: str,
        provider: Callable[[SourceRoot, str | None], AsyncIterator[ChangeHint]],
    ) -> None:
        if capability not in {"windows_usn", "smb_change_notify"}:
            raise ValueError("unsupported change-hint capability")
        self.capability = capability
        self._provider = provider

    def changes(
        self, source: SourceRoot, cursor: str | None
    ) -> AsyncIterator[ChangeHint]:
        return self._provider(source, cursor)


@dataclass(frozen=True)
class LeasedJob:
    job_id: int
    kind: JobKind
    source_id: str
    file_id: str | None
    path: str | None
    content_version: str | None
    mutation_generation: int | None
    attempts: int
    lease_token: str
    artifact_ref: str | None
    artifact_hash: str | None


class LeaseLost(RuntimeError):
    """The durable lease was replaced or expired while work was running."""


def canonical_relative_path(source: SourceRoot, value: str) -> str:
    """Return a traversal-free path relative to the configured source root."""
    if not value or "\x00" in value:
        raise ValueError("source path is empty or invalid")
    windows = "\\" in value or "\\" in source.root or PureWindowsPath(value).anchor
    if windows:
        candidate = PureWindowsPath(value)
        root = PureWindowsPath(source.root)
        if candidate.anchor:
            if not root.anchor:
                raise ValueError(
                    "absolute source path requires an absolute source root"
                )
            try:
                candidate = candidate.relative_to(root)
            except ValueError as exc:
                raise ValueError("source path escapes configured root") from exc
        parts = candidate.parts
    else:
        candidate = PurePosixPath(value)
        root = PurePosixPath(source.root)
        if candidate.is_absolute():
            if not root.is_absolute():
                raise ValueError(
                    "absolute source path requires an absolute source root"
                )
            try:
                candidate = candidate.relative_to(root)
            except ValueError as exc:
                raise ValueError("source path escapes configured root") from exc
        parts = candidate.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("source path must identify a descendant file")
    if any(":" in part for part in parts):
        raise ValueError("source path contains an unsupported stream or drive segment")
    return str(PurePosixPath(*parts))


class CrawlManifest:
    """SQLite/WAL manifest and idempotent multi-stage queue."""

    def __init__(self, path: str) -> None:
        db_path = require_local_control_path(path)
        if db_path.parent == Path(db_path.anchor):
            raise ValueError("crawl manifest must use a dedicated directory")
        self.path = db_path
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _restrict(self.path.parent, required=True)
        await self._preflight_schema_version()
        scrub_required = False
        async with self._connect() as db:
            await db.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    root TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    cursor TEXT,
                    reconciliation_required INTEGER NOT NULL DEFAULT 1,
                    paused INTEGER NOT NULL DEFAULT 0,
                    last_reconcile_started REAL,
                    last_reconcile_completed REAL,
                    last_error TEXT,
                    active_reconcile_token TEXT,
                    reconcile_lease_until REAL,
                    reconcile_generation INTEGER NOT NULL DEFAULT 0,
                    hint_generation INTEGER NOT NULL DEFAULT 0,
                    reconciliation_signal_generation INTEGER NOT NULL DEFAULT 0,
                    active_reconcile_signal_generation INTEGER
                );
                CREATE TABLE IF NOT EXISTS files (
                    source_id TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    stable_id TEXT,
                    size INTEGER NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    created_ns INTEGER,
                    stat_version TEXT NOT NULL,
                    fingerprint TEXT,
                    content_version TEXT NOT NULL,
                    mutation_generation INTEGER NOT NULL DEFAULT 1,
                    acl_version TEXT,
                    matter_ids_json TEXT NOT NULL DEFAULT '[]',
                    last_seen_run TEXT,
                    tombstoned INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(source_id, file_id),
                    UNIQUE(source_id, path),
                    FOREIGN KEY(source_id) REFERENCES sources(source_id)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    file_id TEXT,
                    path TEXT,
                    content_version TEXT,
                    mutation_generation INTEGER,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    priority INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL DEFAULT 'ready',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at REAL NOT NULL DEFAULT 0,
                    lease_until REAL,
                    lease_token TEXT,
                    last_error TEXT,
                    artifact_ref TEXT,
                    artifact_hash TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    CHECK(artifact_ref IS NULL OR (
                        length(artifact_ref)=73 AND substr(artifact_ref,1,9)='artifact:'
                        AND substr(artifact_ref,10) NOT GLOB '*[^0-9a-f]*'
                    )),
                    CHECK(artifact_hash IS NULL OR (
                        length(artifact_hash)=64
                        AND artifact_hash NOT GLOB '*[^0-9a-f]*'
                    )),
                    FOREIGN KEY(source_id) REFERENCES sources(source_id)
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_claim
                    ON jobs(state, kind, available_at, priority DESC, job_id);
                CREATE INDEX IF NOT EXISTS idx_files_seen
                    ON files(source_id, last_seen_run, tombstoned);
                """
            )
            version_row = await (
                await db.execute(
                    "SELECT value FROM metadata WHERE key='schema_version'"
                )
            ).fetchone()
            existing_version = int(version_row[0]) if version_row else 0
            if existing_version > SCHEMA_VERSION:
                raise RuntimeError("crawl manifest schema is newer than this agent")
            migrated = await self._migrate_schema(db, existing_version)
            await self._assert_sanitized_schema(db)
            await db.execute(
                """INSERT INTO metadata(key,value) VALUES('schema_version',?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (str(SCHEMA_VERSION),),
            )
            await db.commit()
            marker = await (
                await db.execute(
                    "SELECT value FROM metadata WHERE key='scrub_required'"
                )
            ).fetchone()
            scrub_required = migrated or (marker is not None and marker[0] == "1")
        if scrub_required:
            await self._physical_scrub()
            async with self._connect() as db:
                await db.execute(
                    """INSERT INTO metadata(key,value) VALUES('scrub_required','0')
                       ON CONFLICT(key) DO UPDATE SET value='0'"""
                )
                await db.commit()
        _restrict(self.path, required=True)

    async def _preflight_schema_version(self) -> None:
        if not self.path.exists():
            return
        uri = f"{self.path.as_uri()}?mode=ro"
        db = await aiosqlite.connect(uri, uri=True)
        try:
            tables = {
                row[0]
                for row in await (
                    await db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )
                ).fetchall()
            }
            metadata = await (
                await db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
                )
            ).fetchone()
            if metadata is None:
                if tables:
                    raise RuntimeError("crawl manifest schema version is missing")
                return
            row = await (
                await db.execute(
                    "SELECT value FROM metadata WHERE key='schema_version'"
                )
            ).fetchone()
            if row is None:
                raise RuntimeError("crawl manifest schema version is missing")
            if row is not None and int(row[0]) > SCHEMA_VERSION:
                raise RuntimeError("crawl manifest schema is newer than this agent")
        finally:
            await db.close()

    async def _assert_sanitized_schema(self, db: aiosqlite.Connection) -> None:
        columns = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(jobs)")).fetchall()
        }
        if "payload_json" in columns:
            raise RuntimeError(
                "crawl manifest contains a forbidden durable payload column"
            )

    async def _physical_scrub(self) -> None:
        vacuum_db = await aiosqlite.connect(self.path, timeout=30)
        try:
            checkpoint = await vacuum_db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            rows = await checkpoint.fetchall()
            await checkpoint.close()
            if not rows or int(rows[0][0]) != 0 or int(rows[0][1]) != int(rows[0][2]):
                raise RuntimeError("crawl manifest WAL scrub could not complete")
            vacuum = await vacuum_db.execute("VACUUM")
            await vacuum.close()
        finally:
            await vacuum_db.close()

    async def _migrate_schema(
        self, db: aiosqlite.Connection, existing_version: int
    ) -> bool:
        await db.execute("PRAGMA secure_delete=ON")
        additions = {
            "sources": {
                "active_reconcile_token": "TEXT",
                "reconcile_lease_until": "REAL",
                "reconcile_generation": "INTEGER NOT NULL DEFAULT 0",
                "hint_generation": "INTEGER NOT NULL DEFAULT 0",
                "reconciliation_signal_generation": "INTEGER NOT NULL DEFAULT 0",
                "active_reconcile_signal_generation": "INTEGER",
            },
            "files": {
                "mutation_generation": "INTEGER NOT NULL DEFAULT 1",
            },
            "jobs": {
                "mutation_generation": "INTEGER",
                "artifact_ref": "TEXT",
                "artifact_hash": "TEXT",
            },
        }
        for table, columns in additions.items():
            rows = await (await db.execute(f"PRAGMA table_info({table})")).fetchall()
            present = {row[1] for row in rows}
            for name, definition in columns.items():
                if name not in present:
                    await db.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                    )
        if existing_version >= SCHEMA_VERSION or existing_version == 0:
            return False

        # Version 1 allowed extracted payload JSON in the durable queue. Rebuild
        # the table instead of copying those rows, then regenerate safe,
        # generation-qualified work from the authoritative files table.
        await db.executescript(
            """
            BEGIN IMMEDIATE;
            UPDATE files SET mutation_generation=CASE
                   WHEN tombstoned=1 THEN max(mutation_generation,2)
                   ELSE max(mutation_generation,1) END;
            DROP TABLE IF EXISTS jobs_v2;
            CREATE TABLE jobs_v2 (
                job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                source_id TEXT NOT NULL,
                file_id TEXT,
                path TEXT,
                content_version TEXT,
                mutation_generation INTEGER,
                dedupe_key TEXT NOT NULL UNIQUE,
                priority INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'ready',
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at REAL NOT NULL DEFAULT 0,
                lease_until REAL,
                lease_token TEXT,
                last_error TEXT,
                artifact_ref TEXT,
                artifact_hash TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                CHECK(artifact_ref IS NULL OR (
                    length(artifact_ref)=73 AND substr(artifact_ref,1,9)='artifact:'
                    AND substr(artifact_ref,10) NOT GLOB '*[^0-9a-f]*'
                )),
                CHECK(artifact_hash IS NULL OR (
                    length(artifact_hash)=64
                    AND artifact_hash NOT GLOB '*[^0-9a-f]*'
                )),
                FOREIGN KEY(source_id) REFERENCES sources(source_id)
            );
            INSERT INTO jobs_v2(
                kind,source_id,file_id,path,content_version,mutation_generation,
                dedupe_key,priority,state,attempts,available_at,last_error,
                created_at,updated_at
            )
            SELECT kind,source_id,NULL,path,NULL,NULL,
                   'migrated-control|' || job_id,priority,
                   CASE WHEN state='dead' THEN 'dead' ELSE 'ready' END,
                   attempts,0,
                   CASE WHEN last_error IS NULL THEN NULL ELSE 'migrated_error' END,
                   created_at,updated_at
              FROM jobs
             WHERE kind IN ('discover','stat') AND state<>'done';
            INSERT OR IGNORE INTO jobs_v2(
                kind,source_id,file_id,path,content_version,mutation_generation,
                dedupe_key,priority,state,available_at,created_at,updated_at
            )
            SELECT 'extract',source_id,file_id,path,content_version,
                   mutation_generation,
                   'migrated-extract|' || source_id || '|' || file_id || '|' ||
                       mutation_generation,
                   0,'ready',0,updated_at,updated_at
              FROM files WHERE tombstoned=0;
            INSERT OR IGNORE INTO jobs_v2(
                kind,source_id,file_id,path,content_version,mutation_generation,
                dedupe_key,priority,state,available_at,created_at,updated_at
            )
            SELECT 'delete',source_id,file_id,path,content_version,
                   mutation_generation,
                   'migrated-delete|' || source_id || '|' || file_id || '|' ||
                       mutation_generation,
                   0,'ready',0,updated_at,updated_at
              FROM files WHERE tombstoned=1;
            INSERT OR IGNORE INTO jobs_v2(
                kind,source_id,file_id,path,content_version,mutation_generation,
                dedupe_key,priority,state,available_at,created_at,updated_at
            )
            SELECT 'acl_refresh',source_id,file_id,path,content_version,
                   mutation_generation,
                   'migrated-acl|' || source_id || '|' || file_id || '|' ||
                       mutation_generation,
                   0,'ready',0,updated_at,updated_at
              FROM files WHERE tombstoned=0 AND acl_version IS NOT NULL;
            DROP TABLE jobs;
            ALTER TABLE jobs_v2 RENAME TO jobs;
            CREATE INDEX idx_jobs_claim
                ON jobs(state, kind, available_at, priority DESC, job_id);
            UPDATE sources SET reconciliation_required=1,
                active_reconcile_token=NULL,reconcile_lease_until=NULL,
                last_error=CASE WHEN last_error IS NULL THEN NULL
                                ELSE 'migrated_error' END;
            INSERT INTO metadata(key,value) VALUES('scrub_required','1')
                ON CONFLICT(key) DO UPDATE SET value='1';
            COMMIT;
            """
        )
        return True

    @asynccontextmanager
    async def _connect(self):
        db = await aiosqlite.connect(self.path, timeout=30)
        try:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("PRAGMA synchronous=FULL")
            await db.execute("PRAGMA secure_delete=ON")
            yield db
        finally:
            await db.close()

    async def register_source(self, source: SourceRoot) -> None:
        if (
            min(
                source.max_workers,
                source.max_open_handles,
                source.read_bytes_per_second,
                source.max_pending_jobs,
                source.max_file_size,
            )
            < 1
        ):
            raise ValueError("source resource limits must be positive")
        config = json.dumps(
            {
                "schedule": source.schedule,
                "matter_ids": list(source.matter_ids),
                "max_workers": source.max_workers,
                "max_open_handles": source.max_open_handles,
                "read_bytes_per_second": source.read_bytes_per_second,
                "max_pending_jobs": source.max_pending_jobs,
                "max_file_size": source.max_file_size,
                "case_sensitive_paths": source.case_sensitive_paths,
                "enabled": source.enabled,
            },
            sort_keys=True,
        )
        async with self._write_lock, self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            current = await (
                await db.execute(
                    "SELECT root,config_json FROM sources WHERE source_id=?",
                    (source.source_id,),
                )
            ).fetchone()
            if current is None:
                await db.execute(
                    "INSERT INTO sources(source_id,root,config_json) VALUES(?,?,?)",
                    (source.source_id, source.root, config),
                )
            else:
                current_config = json.loads(current["config_json"])
                current_config.setdefault("max_file_size", DEFAULT_MAX_FILE_SIZE)
                current_config.setdefault("case_sensitive_paths", False)
                if current["root"] != source.root or current_config != json.loads(
                    config
                ):
                    await db.rollback()
                    raise ValueError(
                        "source registration is immutable; use a new source_id for changes"
                    )
                await db.execute(
                    "UPDATE sources SET config_json=? WHERE source_id=?",
                    (config, source.source_id),
                )
            await db.commit()

    async def begin_reconciliation(
        self, source_id: str, *, lease_seconds: float = 120
    ) -> str:
        run_id = uuid.uuid4().hex
        now = time.time()
        async with self._write_lock, self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            state = await (
                await db.execute(
                    """SELECT active_reconcile_token,reconcile_lease_until,
                       reconciliation_signal_generation FROM sources WHERE source_id=?""",
                    (source_id,),
                )
            ).fetchone()
            if state is None:
                await db.rollback()
                raise KeyError(source_id)
            if (
                state["active_reconcile_token"]
                and float(state["reconcile_lease_until"] or 0) >= now
            ):
                await db.rollback()
                raise RuntimeError("source reconciliation is already leased")
            await db.execute(
                """UPDATE sources SET last_reconcile_started=?, last_error=NULL,
                   active_reconcile_token=?, reconcile_lease_until=?,
                   reconcile_generation=reconcile_generation+1,
                   reconciliation_required=1,
                   active_reconcile_signal_generation=reconciliation_signal_generation
                   WHERE source_id=?""",
                (now, run_id, now + max(1, lease_seconds), source_id),
            )
            await db.commit()
        return run_id

    async def renew_reconciliation(
        self, source_id: str, run_id: str, *, lease_seconds: float = 120
    ) -> bool:
        now = time.time()
        async with self._write_lock, self._connect() as db:
            cursor = await db.execute(
                """UPDATE sources SET reconcile_lease_until=?
                   WHERE source_id=? AND active_reconcile_token=?
                     AND reconcile_lease_until>=?""",
                (now + max(1, lease_seconds), source_id, run_id, now),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def observe(
        self, source: SourceRoot, stat: FileStat, run_id: str | None
    ) -> tuple[str, str, bool]:
        """Upsert identity/path state and return (file_id, version, changed)."""
        stat = replace(stat, path=canonical_relative_path(source, stat.path))
        if (
            stat.size < 0
            or stat.modified_ns < 0
            or (stat.created_ns is not None and stat.created_ns < 0)
        ):
            raise ValueError("source stat contains negative values")
        file_id = stat.identity_for(source)
        now = time.time()
        async with self._write_lock, self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            source_lease = await (
                await db.execute(
                    """SELECT active_reconcile_token,reconcile_lease_until
                       FROM sources WHERE source_id=?""",
                    (source.source_id,),
                )
            ).fetchone()
            if source_lease is None:
                await db.rollback()
                raise KeyError(source.source_id)
            seen_run = run_id
            if run_id is not None:
                if (
                    source_lease["active_reconcile_token"] != run_id
                    or float(source_lease["reconcile_lease_until"] or 0) < now
                ):
                    await db.rollback()
                    raise LeaseLost("reconciliation lease was replaced or expired")
            elif (
                source_lease["active_reconcile_token"]
                and float(source_lease["reconcile_lease_until"] or 0) >= now
            ):
                # A targeted stat proves current existence and must not be
                # tombstoned by the authoritative walk running concurrently.
                seen_run = str(source_lease["active_reconcile_token"])
            current = await (
                await db.execute(
                    "SELECT * FROM files WHERE source_id=? AND file_id=?",
                    (source.source_id, file_id),
                )
            ).fetchone()
            occupant = await (
                await db.execute(
                    "SELECT file_id,content_version FROM files WHERE source_id=? AND path=? AND file_id<>?",
                    (source.source_id, stat.path, file_id),
                )
            ).fetchone()
            if occupant:
                # Path reuse: retain the old identity as a tombstone candidate,
                # then free the unique live path for the replacement identity.
                digest = hashlib.sha256(str(occupant["file_id"]).encode()).hexdigest()
                ordinal = 0
                while True:
                    suffix = "" if ordinal == 0 else f"-{ordinal}"
                    retired_path = f".crawl-retired/{digest}{suffix}"
                    collision = await (
                        await db.execute(
                            """SELECT 1 FROM files WHERE source_id=? AND path=?
                               AND file_id<>?""",
                            (source.source_id, retired_path, occupant["file_id"]),
                        )
                    ).fetchone()
                    if collision is None:
                        break
                    ordinal += 1
                await db.execute(
                    """UPDATE files SET path=?,updated_at=?
                       WHERE source_id=? AND file_id=?""",
                    (retired_path, now, source.source_id, occupant["file_id"]),
                )
                if run_id is None:
                    await db.execute(
                        """UPDATE sources SET reconciliation_required=1,
                           reconciliation_signal_generation=
                               reconciliation_signal_generation+1
                           WHERE source_id=?""",
                        (source.source_id,),
                    )
            matter_ids_json = json.dumps(source.matter_ids)
            changed = (
                current is None
                or current["stat_version"] != stat.stat_version
                or current["path"] != stat.path
                or current["matter_ids_json"] != matter_ids_json
                or bool(current["tombstoned"])
                or current["acl_version"] != stat.acl_version
            )
            version = uuid.uuid4().hex if changed else str(current["content_version"])
            generation = 1 if current is None else int(current["mutation_generation"])
            if current is not None and changed:
                generation += 1
            acl_needed = (current is None and stat.acl_version is not None) or (
                current is not None and current["acl_version"] != stat.acl_version
            )
            required_slots = int(changed) + int(acl_needed)
            pending = await (
                await db.execute(
                    """SELECT count(*) FROM jobs WHERE source_id=?
                       AND kind<>'discover' AND state IN ('ready','retry','leased')""",
                    (source.source_id,),
                )
            ).fetchone()
            if int(pending[0]) + required_slots > source.max_pending_jobs:
                await db.rollback()
                raise RuntimeError("source queue backpressure limit reached")
            await db.execute(
                """INSERT INTO files(
                       source_id,file_id,path,stable_id,size,modified_ns,created_ns,
                       stat_version,content_version,mutation_generation,acl_version,matter_ids_json,
                       last_seen_run,tombstoned,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)
                   ON CONFLICT(source_id,file_id) DO UPDATE SET
                       path=excluded.path, stable_id=excluded.stable_id,
                       size=excluded.size, modified_ns=excluded.modified_ns,
                       created_ns=excluded.created_ns, stat_version=excluded.stat_version,
                       content_version=excluded.content_version,
                       mutation_generation=excluded.mutation_generation,
                       acl_version=excluded.acl_version,
                       matter_ids_json=excluded.matter_ids_json,
                       last_seen_run=excluded.last_seen_run, tombstoned=0,
                       updated_at=excluded.updated_at""",
                (
                    source.source_id,
                    file_id,
                    stat.path,
                    stat.stable_id,
                    stat.size,
                    stat.modified_ns,
                    stat.created_ns,
                    stat.stat_version,
                    version,
                    generation,
                    stat.acl_version,
                    matter_ids_json,
                    seen_run,
                    now,
                ),
            )
            if changed:
                await self._enqueue_tx(
                    db,
                    JobKind.EXTRACT,
                    source.source_id,
                    file_id,
                    stat.path,
                    version,
                    generation,
                )
            if acl_needed:
                await self._enqueue_tx(
                    db,
                    JobKind.ACL_REFRESH,
                    source.source_id,
                    file_id,
                    stat.path,
                    version,
                    generation,
                )
            await db.commit()
        return file_id, version, changed

    async def finish_reconciliation(self, source_id: str, run_id: str) -> int:
        """Tombstone unseen files only after a completely successful walk."""
        now = time.time()
        async with self._write_lock, self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            source_row = await (
                await db.execute(
                    """SELECT config_json,active_reconcile_token,
                       reconcile_lease_until,reconciliation_signal_generation,
                       active_reconcile_signal_generation
                       FROM sources WHERE source_id=?""",
                    (source_id,),
                )
            ).fetchone()
            if (
                source_row is None
                or source_row["active_reconcile_token"] != run_id
                or float(source_row["reconcile_lease_until"] or 0) < now
            ):
                await db.rollback()
                raise LeaseLost("reconciliation lease was replaced or expired")
            rows = await (
                await db.execute(
                    """SELECT file_id,path,content_version,mutation_generation FROM files
                       WHERE source_id=? AND tombstoned=0
                         AND (last_seen_run IS NULL OR last_seen_run<>?)""",
                    (source_id, run_id),
                )
            ).fetchall()
            pending = await (
                await db.execute(
                    """SELECT count(*) FROM jobs WHERE source_id=?
                       AND kind<>'discover' AND state IN ('ready','retry','leased')""",
                    (source_id,),
                )
            ).fetchone()
            max_pending = int(json.loads(source_row["config_json"])["max_pending_jobs"])
            if int(pending[0]) + len(rows) > max_pending:
                await db.rollback()
                raise RuntimeError("source queue backpressure limit reached")
            for row in rows:
                generation = int(row["mutation_generation"]) + 1
                await db.execute(
                    """UPDATE files SET tombstoned=1,mutation_generation=?,updated_at=?
                       WHERE source_id=? AND file_id=?""",
                    (generation, now, source_id, row["file_id"]),
                )
                await self._enqueue_tx(
                    db,
                    JobKind.DELETE,
                    source_id,
                    row["file_id"],
                    row["path"],
                    row["content_version"],
                    generation,
                )
            cursor = await db.execute(
                """UPDATE sources SET reconciliation_required=CASE
                       WHEN reconciliation_signal_generation=
                            active_reconcile_signal_generation THEN 0 ELSE 1 END,
                   last_reconcile_completed=?,last_error=CASE
                       WHEN reconciliation_signal_generation=
                            active_reconcile_signal_generation THEN NULL
                       ELSE last_error END,
                   active_reconcile_token=NULL,reconcile_lease_until=NULL,
                   active_reconcile_signal_generation=NULL
                   WHERE source_id=? AND active_reconcile_token=?
                     AND reconcile_lease_until>=?""",
                (now, source_id, run_id, now),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                raise LeaseLost("reconciliation lease was replaced or expired")
            await db.commit()
        return len(rows)

    async def fail_reconciliation(
        self, source_id: str, run_id: str, error: BaseException
    ) -> None:
        async with self._write_lock, self._connect() as db:
            await db.execute(
                """UPDATE sources SET reconciliation_required=1,last_error=?,
                   active_reconcile_token=NULL,reconcile_lease_until=NULL
                   WHERE source_id=? AND active_reconcile_token=?""",
                (stable_error_code(error), source_id, run_id),
            )
            await db.commit()

    async def enqueue(
        self,
        kind: JobKind,
        source_id: str,
        *,
        file_id: str | None = None,
        path: str | None = None,
        content_version: str | None = None,
        mutation_generation: int | None = None,
        priority: int = 0,
        artifact_ref: str | None = None,
        artifact_hash: str | None = None,
        dedupe_suffix: str = "",
        rearm_done: bool = False,
    ) -> None:
        async with self._write_lock, self._connect() as db:
            await self._enqueue_tx(
                db,
                kind,
                source_id,
                file_id,
                path,
                content_version,
                mutation_generation,
                priority=priority,
                artifact_ref=artifact_ref,
                artifact_hash=artifact_hash,
                dedupe_suffix=dedupe_suffix,
                rearm_done=rearm_done,
            )
            await db.commit()

    async def _enqueue_tx(
        self,
        db: aiosqlite.Connection,
        kind: JobKind,
        source_id: str,
        file_id: str | None,
        path: str | None,
        content_version: str | None,
        mutation_generation: int | None,
        *,
        priority: int = 0,
        artifact_ref: str | None = None,
        artifact_hash: str | None = None,
        dedupe_suffix: str = "",
        rearm_done: bool = False,
    ) -> None:
        if kind == JobKind.INDEX:
            if not valid_artifact_fields(artifact_ref, artifact_hash):
                raise ValueError("index jobs require opaque artifact fields")
        elif artifact_ref is not None or artifact_hash is not None:
            raise ValueError("artifact fields are valid only for index jobs")
        dedupe = "|".join(
            [
                kind.value,
                source_id,
                file_id or "",
                content_version or "",
                str(mutation_generation or ""),
                path or "",
                dedupe_suffix,
            ]
        )
        now = time.time()
        await db.execute(
            """INSERT INTO jobs(kind,source_id,file_id,path,content_version,
                   mutation_generation,dedupe_key,priority,state,available_at,
                   artifact_ref,artifact_hash,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,'ready',0,?,?,?,?)
               ON CONFLICT(dedupe_key) DO UPDATE SET
                   priority=max(priority,excluded.priority),
                   state=CASE WHEN jobs.state='done' AND ? THEN 'ready'
                              ELSE jobs.state END,
                   attempts=CASE WHEN jobs.state='done' AND ? THEN 0
                                 ELSE jobs.attempts END,
                   available_at=CASE WHEN jobs.state='done' AND ? THEN 0
                                     ELSE jobs.available_at END,
                   updated_at=excluded.updated_at""",
            (
                kind.value,
                source_id,
                file_id,
                path,
                content_version,
                mutation_generation,
                dedupe,
                priority,
                artifact_ref,
                artifact_hash,
                now,
                now,
                int(rearm_done),
                int(rearm_done),
                int(rearm_done),
            ),
        )

    async def claim(
        self, kind: JobKind, *, lease_seconds: float = 60
    ) -> LeasedJob | None:
        now = time.time()
        token = uuid.uuid4().hex
        async with self._write_lock, self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    """SELECT * FROM jobs WHERE kind=? AND
                       ((state IN ('ready','retry') AND available_at<=?) OR
                        (state='leased' AND lease_until<?))
                       ORDER BY priority DESC, job_id LIMIT 1""",
                    (kind.value, now, now),
                )
            ).fetchone()
            if row is None:
                await db.rollback()
                return None
            if kind == JobKind.INDEX and not valid_artifact_fields(
                row["artifact_ref"], row["artifact_hash"]
            ):
                await db.execute(
                    """UPDATE jobs SET state='dead',artifact_ref=NULL,
                       artifact_hash=NULL,last_error='invalid_input',updated_at=?
                       WHERE job_id=?""",
                    (now, row["job_id"]),
                )
                await db.execute(
                    """INSERT INTO metadata(key,value) VALUES('scrub_required','1')
                       ON CONFLICT(key) DO UPDATE SET value='1'"""
                )
                await db.commit()
                return None
            attempts = int(row["attempts"]) + 1
            await db.execute(
                """UPDATE jobs SET state='leased',attempts=?,lease_until=?,
                   lease_token=?,updated_at=? WHERE job_id=?""",
                (attempts, now + lease_seconds, token, now, row["job_id"]),
            )
            await db.commit()
        return LeasedJob(
            job_id=row["job_id"],
            kind=kind,
            source_id=row["source_id"],
            file_id=row["file_id"],
            path=row["path"],
            content_version=row["content_version"],
            mutation_generation=row["mutation_generation"],
            attempts=attempts,
            lease_token=token,
            artifact_ref=row["artifact_ref"],
            artifact_hash=row["artifact_hash"],
        )

    async def complete(self, job: LeasedJob) -> bool:
        async with self._write_lock, self._connect() as db:
            cursor = await db.execute(
                """UPDATE jobs SET state='done',lease_until=NULL,lease_token=NULL,
                   updated_at=? WHERE job_id=? AND state='leased' AND lease_token=?""",
                (time.time(), job.job_id, job.lease_token),
            )
            if cursor.rowcount == 1:
                await self._compact_completed_tx(
                    db, job.source_id, retain=COMPLETED_JOB_RETENTION
                )
            await db.commit()
            return cursor.rowcount == 1

    async def compact_completed(self, source_id: str, *, retain: int = 10_000) -> int:
        if retain < 0:
            raise ValueError("completed-job retention cannot be negative")
        async with self._write_lock, self._connect() as db:
            deleted = await self._compact_completed_tx(db, source_id, retain=retain)
            await db.commit()
            return deleted

    async def _compact_completed_tx(
        self, db: aiosqlite.Connection, source_id: str, *, retain: int
    ) -> int:
        cursor = await db.execute(
            """DELETE FROM jobs WHERE source_id=? AND state='done'
               AND job_id NOT IN (
                   SELECT job_id FROM jobs WHERE source_id=? AND state='done'
                   ORDER BY job_id DESC LIMIT ?
               )""",
            (source_id, source_id, retain),
        )
        return cursor.rowcount

    async def renew(self, job: LeasedJob, *, lease_seconds: float = 60) -> bool:
        """Extend only the exact lease generation held by ``job``."""
        now = time.time()
        async with self._write_lock, self._connect() as db:
            cursor = await db.execute(
                """UPDATE jobs SET lease_until=?,updated_at=?
                   WHERE job_id=? AND state='leased' AND lease_token=?
                     AND lease_until>=?""",
                (
                    now + max(1, lease_seconds),
                    now,
                    job.job_id,
                    job.lease_token,
                    now,
                ),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def lease_active(self, job: LeasedJob) -> bool:
        now = time.time()
        async with self._connect() as db:
            row = await (
                await db.execute(
                    """SELECT 1 FROM jobs WHERE job_id=? AND state='leased'
                       AND lease_token=? AND lease_until>=?""",
                    (job.job_id, job.lease_token, now),
                )
            ).fetchone()
            return row is not None

    async def retry(
        self, job: LeasedJob, error: BaseException, *, max_attempts: int = 5
    ) -> bool:
        state = JobState.DEAD if job.attempts >= max_attempts else JobState.RETRY
        delay = min(300.0, 2.0 ** min(job.attempts, 8))
        async with self._write_lock, self._connect() as db:
            cursor = await db.execute(
                """UPDATE jobs SET state=?,available_at=?,lease_until=NULL,
                   lease_token=NULL,last_error=?,updated_at=?
                   WHERE job_id=? AND state='leased' AND lease_token=?""",
                (
                    state.value,
                    time.time() + delay,
                    stable_error_code(error),
                    time.time(),
                    job.job_id,
                    job.lease_token,
                ),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def rearm_dead(self, job_id: int) -> bool:
        """Rearm terminal work only when its durable generation is still current."""
        now = time.time()
        async with self._write_lock, self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            job = await (
                await db.execute(
                    "SELECT * FROM jobs WHERE job_id=? AND state='dead'", (job_id,)
                )
            ).fetchone()
            if job is None:
                await db.rollback()
                return False
            source = await (
                await db.execute(
                    "SELECT config_json FROM sources WHERE source_id=?",
                    (job["source_id"],),
                )
            ).fetchone()
            if source is None:
                await db.rollback()
                return False
            kind = JobKind(job["kind"])
            if kind != JobKind.DISCOVER:
                pending = await (
                    await db.execute(
                        """SELECT count(*) FROM jobs WHERE source_id=?
                           AND kind<>'discover'
                           AND state IN ('ready','retry','leased')""",
                        (job["source_id"],),
                    )
                ).fetchone()
                limit = int(json.loads(source["config_json"])["max_pending_jobs"])
                if int(pending[0]) >= limit:
                    await db.rollback()
                    raise RuntimeError("source queue backpressure limit reached")
            if kind == JobKind.STAT and not job["path"]:
                await db.rollback()
                return False
            if kind in {
                JobKind.EXTRACT,
                JobKind.INDEX,
                JobKind.DELETE,
                JobKind.ACL_REFRESH,
            }:
                file_row = await (
                    await db.execute(
                        "SELECT * FROM files WHERE source_id=? AND file_id=?",
                        (job["source_id"], job["file_id"]),
                    )
                ).fetchone()
                expected_tombstone = kind == JobKind.DELETE
                if (
                    file_row is None
                    or bool(file_row["tombstoned"]) != expected_tombstone
                    or int(file_row["mutation_generation"])
                    != int(job["mutation_generation"] or 0)
                    or file_row["content_version"] != job["content_version"]
                    or (
                        kind == JobKind.INDEX
                        and not valid_artifact_fields(
                            job["artifact_ref"], job["artifact_hash"]
                        )
                    )
                ):
                    await db.rollback()
                    return False
            cursor = await db.execute(
                """UPDATE jobs SET state='ready',attempts=0,available_at=0,
                   lease_until=NULL,lease_token=NULL,last_error=NULL,updated_at=?
                   WHERE job_id=? AND state='dead'""",
                (now, job_id),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def defer(self, job: LeasedJob, *, seconds: float = 30) -> bool:
        """Return paused work without consuming an attempt."""
        async with self._write_lock, self._connect() as db:
            cursor = await db.execute(
                """UPDATE jobs SET state='retry',attempts=max(0,attempts-1),
                   available_at=?,lease_until=NULL,lease_token=NULL,updated_at=?
                   WHERE job_id=? AND state='leased' AND lease_token=?""",
                (
                    time.time() + max(1, seconds),
                    time.time(),
                    job.job_id,
                    job.lease_token,
                ),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def file(self, source_id: str, file_id: str) -> dict | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute(
                    "SELECT * FROM files WHERE source_id=? AND file_id=?",
                    (source_id, file_id),
                )
            ).fetchone()
            return dict(row) if row else None

    async def set_fingerprint(
        self,
        source_id: str,
        file_id: str,
        content_version: str,
        mutation_generation: int,
        stat_version: str,
        fingerprint: str,
    ) -> bool:
        async with self._write_lock, self._connect() as db:
            cursor = await db.execute(
                """UPDATE files SET fingerprint=?,updated_at=? WHERE source_id=?
                   AND file_id=? AND content_version=? AND mutation_generation=?
                   AND stat_version=? AND tombstoned=0""",
                (
                    fingerprint,
                    time.time(),
                    source_id,
                    file_id,
                    content_version,
                    mutation_generation,
                    stat_version,
                ),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def save_cursor(
        self, source_id: str, cursor: str | None, *, require_reconcile: bool = False
    ) -> None:
        async with self._write_lock, self._connect() as db:
            await db.execute(
                """UPDATE sources SET cursor=?,reconciliation_required=max(
                   reconciliation_required,?),
                   reconciliation_signal_generation=
                       reconciliation_signal_generation+?
                   WHERE source_id=?""",
                (cursor, int(require_reconcile), int(require_reconcile), source_id),
            )
            await db.commit()

    async def require_reconciliation(
        self, source_id: str, *, error_code: str | None = None
    ) -> None:
        if error_code is not None and error_code not in STABLE_ERROR_CODES:
            raise ValueError("unsupported stable error code")
        async with self._write_lock, self._connect() as db:
            await db.execute(
                """UPDATE sources SET reconciliation_required=1,
                   reconciliation_signal_generation=
                       reconciliation_signal_generation+1,
                   last_error=COALESCE(?,last_error) WHERE source_id=?""",
                (error_code, source_id),
            )
            await db.commit()

    async def record_hint(
        self,
        source_id: str,
        path: str,
        cursor: str | None,
        *,
        priority: int = 50,
    ) -> None:
        """Atomically persist a provider cursor and its STAT work."""
        async with self._write_lock, self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    "SELECT hint_generation,config_json FROM sources WHERE source_id=?",
                    (source_id,),
                )
            ).fetchone()
            if row is None:
                await db.rollback()
                raise KeyError(source_id)
            pending = await (
                await db.execute(
                    """SELECT count(*) FROM jobs WHERE source_id=?
                       AND kind<>'discover' AND state IN ('ready','retry','leased')""",
                    (source_id,),
                )
            ).fetchone()
            max_pending = int(json.loads(row["config_json"])["max_pending_jobs"])
            if int(pending[0]) + 1 > max_pending:
                await db.execute(
                    """UPDATE sources SET reconciliation_required=1,
                       reconciliation_signal_generation=
                           reconciliation_signal_generation+1
                       WHERE source_id=?""",
                    (source_id,),
                )
                await db.commit()
                raise RuntimeError("source queue backpressure limit reached")
            generation = int(row["hint_generation"]) + 1
            await db.execute(
                "UPDATE sources SET cursor=?,hint_generation=? WHERE source_id=?",
                (cursor, generation, source_id),
            )
            suffix = f"cursor:{cursor}" if cursor is not None else f"hint:{generation}"
            await self._enqueue_tx(
                db,
                JobKind.STAT,
                source_id,
                None,
                path,
                None,
                None,
                priority=priority,
                dedupe_suffix=suffix,
            )
            await db.commit()

    async def set_paused(self, source_id: str, paused: bool) -> None:
        async with self._write_lock, self._connect() as db:
            await db.execute(
                "UPDATE sources SET paused=? WHERE source_id=?",
                (int(paused), source_id),
            )
            await db.commit()

    async def source_state(self, source_id: str) -> dict:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute(
                    "SELECT * FROM sources WHERE source_id=?", (source_id,)
                )
            ).fetchone()
            if row is None:
                raise KeyError(source_id)
            return dict(row)

    async def status(self) -> dict:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            jobs = await (
                await db.execute(
                    "SELECT state,kind,count(*) AS n FROM jobs GROUP BY state,kind"
                )
            ).fetchall()
            sources = await (
                await db.execute(
                    "SELECT source_id,paused,reconciliation_required,last_error,last_reconcile_completed FROM sources"
                )
            ).fetchall()
            return {
                "jobs": {f"{r['kind']}.{r['state']}": r["n"] for r in jobs},
                "sources": [dict(row) for row in sources],
            }

    async def pending_jobs(self, source_id: str) -> int:
        async with self._connect() as db:
            row = await (
                await db.execute(
                    """SELECT count(*) FROM jobs WHERE source_id=?
                       AND kind<>'discover' AND state IN ('ready','retry','leased')""",
                    (source_id,),
                )
            ).fetchone()
            return int(row[0])


@dataclass
class PipelineMetrics:
    counters: Counter = field(default_factory=Counter)
    gauges: dict[str, float] = field(default_factory=dict)

    def increment(self, name: str, value: int = 1) -> None:
        self.counters[name] += value

    def snapshot(self) -> dict:
        return {"counters": dict(self.counters), "gauges": dict(self.gauges)}


class ReadBudget:
    """Per-source handle and byte-rate budget with interactive backpressure."""

    def __init__(self, source: SourceRoot) -> None:
        self.handles = asyncio.Semaphore(max(1, source.max_open_handles))
        self.worker_slots = asyncio.Semaphore(max(1, source.max_workers))
        self.bytes_per_second = max(1, source.read_bytes_per_second)
        self.paused = asyncio.Event()
        self.paused.set()
        self.durably_paused = False
        self.interactive_waiters = 0
        self._rate_lock = asyncio.Lock()
        self._next_read_at = 0.0

    def set_durable_paused(self, paused: bool) -> None:
        self.durably_paused = paused
        self._sync_runnable()

    def set_interactive(self, active: bool) -> None:
        if active:
            self.interactive_waiters += 1
        else:
            self.interactive_waiters = max(0, self.interactive_waiters - 1)
        self._sync_runnable()

    def _sync_runnable(self) -> None:
        if self.durably_paused or self.interactive_waiters:
            self.paused.clear()
        else:
            self.paused.set()

    async def throttle(self, byte_count: int) -> None:
        # Serialize reservations, not reads: workers may use multiple handles,
        # but aggregate starts are paced to the source's byte budget.
        async with self._rate_lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            start = max(now, self._next_read_at)
            self._next_read_at = start + max(0, byte_count) / self.bytes_per_second
        if start > now:
            await asyncio.sleep(start - now)


class SupersededJob(RuntimeError):
    """A safely discarded queue generation replaced by fresher source state."""


class CrawlPipeline:
    def __init__(
        self,
        manifest: CrawlManifest,
        source_adapter: SourceAdapter,
        extractor: ExtractionSink,
        index_sink: IndexSink,
        *,
        acl_sink: AclRefreshSink | None = None,
        enabled: bool = False,
        max_attempts: int = 5,
        lease_seconds: float = 60,
        reconciliation_lease_seconds: float = 120,
    ) -> None:
        self.manifest = manifest
        self.source_adapter = source_adapter
        self.extractor = extractor
        self.index_sink = index_sink
        self.acl_sink = acl_sink
        self.enabled = enabled
        self.max_attempts = max_attempts
        self.lease_seconds = max(0.15, lease_seconds)
        self.reconciliation_lease_seconds = max(0.15, reconciliation_lease_seconds)
        self.sources: dict[str, SourceRoot] = {}
        self.budgets: dict[str, ReadBudget] = {}
        self._reconcile_locks: dict[str, asyncio.Lock] = {}
        self.metrics = PipelineMetrics()

    async def add_source(self, source: SourceRoot) -> None:
        await self.manifest.register_source(source)
        self.sources[source.source_id] = source
        budget = ReadBudget(source)
        state = await self.manifest.source_state(source.source_id)
        if state["paused"]:
            budget.set_durable_paused(True)
        self.budgets[source.source_id] = budget
        self._reconcile_locks[source.source_id] = asyncio.Lock()

    async def reconcile(self, source_id: str) -> int:
        self._require_enabled()
        source = self.sources[source_id]
        if not source.enabled:
            raise RuntimeError("source root is disabled")
        async with self.budgets[source_id].worker_slots:
            async with self._reconcile_locks[source_id]:
                return await self._reconcile_locked(source)

    async def _reconcile_locked(self, source: SourceRoot) -> int:
        source_id = source.source_id
        run_id = await self.manifest.begin_reconciliation(
            source_id, lease_seconds=self.reconciliation_lease_seconds
        )
        observed = 0

        async def walk() -> int:
            nonlocal observed
            await self.budgets[source_id].paused.wait()
            async with self.budgets[source_id].handles:
                async for stat in self.source_adapter.discover(source):
                    await self.budgets[source_id].paused.wait()
                    try:
                        await self.manifest.observe(source, stat, run_id)
                    except RuntimeError as exc:
                        if str(exc) == "source queue backpressure limit reached":
                            self.metrics.increment("reconciliations_backpressured")
                        raise
                    observed += 1
            deleted = await self.manifest.finish_reconciliation(source_id, run_id)
            self.metrics.increment("reconciliations_completed")
            self.metrics.increment("files_observed", observed)
            self.metrics.increment("tombstones_created", deleted)
            return observed

        try:
            return await self._run_with_renewal(
                walk(),
                lambda: self.manifest.renew_reconciliation(
                    source_id,
                    run_id,
                    lease_seconds=self.reconciliation_lease_seconds,
                ),
                self.reconciliation_lease_seconds,
            )
        except BaseException as exc:
            await self.manifest.fail_reconciliation(source_id, run_id, exc)
            self.metrics.increment("reconciliations_failed")
            raise

    async def apply_hint(self, hint: ChangeHint) -> None:
        self._require_enabled()
        source = self.sources[hint.source_id]
        if not source.enabled:
            raise RuntimeError("source root is disabled")
        if hint.overflow or hint.disconnected:
            await self.manifest.save_cursor(
                hint.source_id, hint.cursor, require_reconcile=True
            )
            self.metrics.increment("hint_reconciliation_required")
            return
        if hint.path:
            path = canonical_relative_path(source, hint.path)
            await self.manifest.record_hint(
                hint.source_id, path, hint.cursor, priority=50
            )
        else:
            await self.manifest.save_cursor(hint.source_id, hint.cursor)
        self.metrics.increment("hints_applied")

    async def pump_hints(self, source_id: str, adapter: ChangeHintAdapter) -> None:
        state = await self.manifest.source_state(source_id)
        try:
            async for hint in adapter.changes(self.sources[source_id], state["cursor"]):
                await self.apply_hint(hint)
        except BaseException:
            await self.manifest.save_cursor(
                source_id, state["cursor"], require_reconcile=True
            )
            self.metrics.increment("hint_stream_failures")
            raise

    async def process_one(self, kind: JobKind) -> bool:
        self._require_enabled()
        job = await self.manifest.claim(kind, lease_seconds=self.lease_seconds)
        if job is None:
            return False
        source = self.sources[job.source_id]
        if (
            not source.enabled
            or (await self.manifest.source_state(job.source_id))["paused"]
        ):
            await self.manifest.defer(job)
            self.metrics.increment(f"jobs.{kind.value}.paused")
            return True
        try:
            async with self.budgets[job.source_id].worker_slots:
                await self._run_with_renewal(
                    self._dispatch(job),
                    lambda: self.manifest.renew(job, lease_seconds=self.lease_seconds),
                    self.lease_seconds,
                )
            if not await self.manifest.complete(job):
                raise LeaseLost("job lease expired before completion")
            self.metrics.increment(f"jobs.{kind.value}.completed")
        except SupersededJob:
            if not await self.manifest.complete(job):
                raise RuntimeError("job lease expired before superseded completion")
            self.metrics.increment(f"jobs.{kind.value}.superseded")
        except BaseException as exc:
            await self.manifest.retry(job, exc, max_attempts=self.max_attempts)
            self.metrics.increment(f"jobs.{kind.value}.failed")
            raise
        return True

    async def _run_with_renewal(self, work, renew, lease_seconds: float):
        task = asyncio.create_task(work)
        interval = max(0.05, lease_seconds / 3)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=interval)
                if task in done:
                    return await task
                if not await renew():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise LeaseLost("durable lease was replaced or expired")
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _require_job_lease(self, job: LeasedJob) -> None:
        if not await self.manifest.lease_active(job):
            raise LeaseLost("job lease was replaced or expired")

    async def _dispatch(self, job: LeasedJob) -> None:
        if job.kind == JobKind.STAT:
            await self._stat(job)
        elif job.kind == JobKind.DISCOVER:
            async with self._reconcile_locks[job.source_id]:
                await self._reconcile_locked(self.sources[job.source_id])
        elif job.kind == JobKind.EXTRACT:
            await self._extract(job)
        elif job.kind == JobKind.INDEX:
            await self._index(job)
        elif job.kind == JobKind.DELETE:
            row = await self.manifest.file(job.source_id, str(job.file_id))
            if (
                row is None
                or not row["tombstoned"]
                or int(row["mutation_generation"]) != int(job.mutation_generation or 0)
            ):
                raise SupersededJob("stale delete generation")
            await self._require_job_lease(job)
            await self.index_sink.delete(
                job.source_id,
                str(job.file_id),
                int(job.mutation_generation or 0),
            )
        elif job.kind == JobKind.ACL_REFRESH:
            if self.acl_sink is None:
                raise RuntimeError("ACL refresh sink is unavailable")
            row = await self.manifest.file(job.source_id, str(job.file_id))
            if (
                row is None
                or row["tombstoned"]
                or int(row["mutation_generation"]) != int(job.mutation_generation or 0)
            ):
                raise SupersededJob("stale ACL refresh generation")
            await self._require_job_lease(job)
            await self.acl_sink.refresh(
                job.source_id,
                str(job.file_id),
                str(row["path"]),
                row["acl_version"],
                int(row["mutation_generation"]),
            )
        else:
            raise ValueError(f"unsupported worker job: {job.kind}")

    async def _stat(self, job: LeasedJob) -> None:
        source = self.sources[job.source_id]
        await self.budgets[job.source_id].paused.wait()
        path = canonical_relative_path(source, str(job.path))
        async with self.budgets[job.source_id].handles:
            stat = await self.source_adapter.stat(source, path)
        if stat is None:
            # Hints cannot prove deletion. They force a complete reconciliation.
            await self.manifest.save_cursor(job.source_id, None, require_reconcile=True)
            return
        await self.manifest.observe(source, stat, None)

    async def _extract(self, job: LeasedJob) -> None:
        source = self.sources[job.source_id]
        budget = self.budgets[job.source_id]
        await budget.paused.wait()
        path = canonical_relative_path(source, str(job.path))
        row = await self.manifest.file(job.source_id, str(job.file_id))
        async with budget.handles:
            before = await self.source_adapter.stat(source, path)
            if before is None:
                await self.manifest.require_reconciliation(job.source_id)
                raise SupersededJob("source disappeared before read")
            before = replace(before, path=canonical_relative_path(source, before.path))
            if before.identity_for(source) != job.file_id:
                await self.manifest.observe(source, before, None)
                raise SupersededJob("source identity changed before read")
            if (
                row is None
                or row["tombstoned"]
                or row["content_version"] != job.content_version
                or int(row["mutation_generation"]) != int(job.mutation_generation or 0)
                or row["stat_version"] != before.stat_version
                or row["path"] != before.path
                or row["acl_version"] != before.acl_version
            ):
                await self.manifest.observe(source, before, None)
                raise SupersededJob("source changed before extraction began")
            if before.size > source.max_file_size:
                raise ValueError("source file exceeds configured extraction limit")
            await budget.throttle(before.size)
            chunks: list[bytes] = []
            total = 0
            async for chunk in self.source_adapter.read_chunks(
                source, path, 1024 * 1024
            ):
                if not isinstance(chunk, bytes):
                    raise TypeError("source adapter yielded a non-bytes chunk")
                total += len(chunk)
                if total > source.max_file_size:
                    raise ValueError("source file exceeds configured extraction limit")
                chunks.append(chunk)
            content = b"".join(chunks)
            if total != before.size:
                raise SupersededJob("source byte count did not match stable stat")
            after = await self.source_adapter.stat(source, path)
            if after is not None:
                after = replace(after, path=canonical_relative_path(source, after.path))
        if (
            after is None
            or before.stat_version != after.stat_version
            or before.identity_for(source) != after.identity_for(source)
            or before.path != after.path
            or before.acl_version != after.acl_version
        ):
            # Pre/post-read stabilization: do not hand a torn read to extraction.
            if after is None:
                await self.manifest.require_reconciliation(
                    job.source_id, error_code="source_io"
                )
            else:
                await self.manifest.observe(source, after, None)
            raise SupersededJob("source changed while being read")
        fingerprint = hashlib.sha256(content).hexdigest()
        if not await self.manifest.set_fingerprint(
            job.source_id,
            str(job.file_id),
            str(job.content_version),
            int(job.mutation_generation or 0),
            before.stat_version,
            fingerprint,
        ):
            raise SupersededJob("stale extraction generation")
        await self._require_job_lease(job)
        extracted = await self.extractor.extract(
            StableContent(
                source_id=job.source_id,
                file_id=str(job.file_id),
                path=str(job.path),
                content_version=str(job.content_version),
                mutation_generation=int(job.mutation_generation or 0),
                fingerprint=fingerprint,
                content=content,
                matter_ids=source.matter_ids,
            )
        )
        if (
            extracted.content_version != job.content_version
            or extracted.mutation_generation != job.mutation_generation
        ):
            raise RuntimeError("extractor returned a mismatched document generation")
        if not valid_artifact_fields(extracted.artifact_ref, extracted.artifact_hash):
            raise ValueError("extractor returned an invalid artifact reference")
        await self._require_job_lease(job)
        await self.manifest.enqueue(
            JobKind.INDEX,
            job.source_id,
            file_id=job.file_id,
            path=job.path,
            content_version=job.content_version,
            mutation_generation=job.mutation_generation,
            artifact_ref=extracted.artifact_ref,
            artifact_hash=extracted.artifact_hash,
        )

    async def _index(self, job: LeasedJob) -> None:
        row = await self.manifest.file(job.source_id, str(job.file_id))
        if (
            row is None
            or row["tombstoned"]
            or row["content_version"] != job.content_version
            or int(row["mutation_generation"]) != int(job.mutation_generation or 0)
            or not valid_artifact_fields(job.artifact_ref, job.artifact_hash)
        ):
            raise SupersededJob("stale index generation")
        source = self.sources[job.source_id]
        await self._require_job_lease(job)
        await self.index_sink.upsert(
            IndexDocument(
                source_id=job.source_id,
                file_id=str(job.file_id),
                path=str(row["path"]),
                content_version=str(job.content_version),
                mutation_generation=int(job.mutation_generation or 0),
                artifact_ref=str(job.artifact_ref),
                artifact_hash=str(job.artifact_hash),
                acl_version=row["acl_version"],
                matter_ids=source.matter_ids,
            )
        )

    async def pause(self, source_id: str) -> None:
        self.budgets[source_id].set_durable_paused(True)
        await self.manifest.set_paused(source_id, True)

    async def resume(self, source_id: str) -> None:
        self.budgets[source_id].set_durable_paused(False)
        await self.manifest.set_paused(source_id, False)

    async def set_interactive_priority(self, source_id: str, active: bool) -> None:
        """Yield crawl reads while latency-sensitive local search is active."""
        self.budgets[source_id].set_interactive(active)

    async def due_sources(self, now) -> list[str]:
        """Return enabled source roots whose individual schedule is due."""
        due: list[str] = []
        for source_id, source in self.sources.items():
            if not source.enabled:
                continue
            state = await self.manifest.source_state(source_id)
            if state["paused"]:
                continue
            if (
                state["active_reconcile_token"]
                and float(state["reconcile_lease_until"] or 0) >= time.time()
            ):
                continue
            timestamp = state["last_reconcile_completed"]
            last_run = None
            if timestamp is not None:
                from datetime import datetime

                last_run = datetime.fromtimestamp(float(timestamp), tz=now.tzinfo)
            if state["reconciliation_required"] or due_for_scan(
                {"share_id": source_id, "scan_schedule": source.schedule},
                last_run,
                now,
            ):
                due.append(source_id)
        return due

    async def enqueue_due_reconciliations(self, now) -> list[str]:
        """Persist scheduled discovery so process restarts cannot lose work."""
        due = await self.due_sources(now)
        for source_id in due:
            await self.manifest.enqueue(
                JobKind.DISCOVER,
                source_id,
                priority=10,
                dedupe_suffix="scheduled",
                rearm_done=True,
            )
        return due

    async def status(self) -> dict:
        status = await self.manifest.status()
        status["enabled"] = self.enabled
        status["metrics"] = self.metrics.snapshot()
        return status

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise RuntimeError("source-root crawl control plane is disabled")
