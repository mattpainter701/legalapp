from __future__ import annotations

import aiosqlite
from pathlib import Path

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS file_ledger (
    path TEXT PRIMARY KEY,
    share_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    ext TEXT,
    mime_type TEXT,
    snippet TEXT,
    owner TEXT,
    size_bytes INTEGER,
    modified_time TEXT,
    created_time TEXT,
    content_hash TEXT,
    dir_mtime TEXT,
    synced_at TEXT,
    is_deleted INTEGER DEFAULT 0
)
"""

_CREATE_IDX_SHARE = (
    "CREATE INDEX IF NOT EXISTS idx_ledger_share ON file_ledger(share_id)"
)
_CREATE_IDX_SYNCED = (
    "CREATE INDEX IF NOT EXISTS idx_ledger_synced ON file_ledger(synced_at)"
)


class FileLedger:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(_CREATE_TABLE)
        await self._db.execute(_CREATE_IDX_SHARE)
        await self._db.execute(_CREATE_IDX_SYNCED)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    def _row_to_dict(self, row: aiosqlite.Row) -> dict:
        d = dict(row)
        d["is_deleted"] = bool(d.get("is_deleted", 0))
        return d

    async def get_file(self, path: str) -> dict | None:
        assert self._db
        cursor = await self._db.execute(
            "SELECT * FROM file_ledger WHERE path = ?", (path,)
        )
        row = await cursor.fetchone()
        return self._row_to_dict(row) if row else None

    async def get_files(self, paths: list[str]) -> dict[str, dict]:
        """Load ledger rows for a scan in bounded SQL batches."""
        assert self._db
        found: dict[str, dict] = {}
        # SQLite has a finite host-parameter limit; stay well below it.
        for offset in range(0, len(paths), 500):
            batch = paths[offset : offset + 500]
            if not batch:
                continue
            placeholders = ",".join("?" for _ in batch)
            cursor = await self._db.execute(
                f"SELECT * FROM file_ledger WHERE path IN ({placeholders})",
                batch,
            )
            for row in await cursor.fetchall():
                found[row["path"]] = self._row_to_dict(row)
        return found

    async def get_dir_mtime(self, dir_path: str) -> str | None:
        assert self._db
        cursor = await self._db.execute(
            "SELECT dir_mtime FROM file_ledger WHERE path = ? AND dir_mtime IS NOT NULL",
            (dir_path,),
        )
        row = await cursor.fetchone()
        return row["dir_mtime"] if row else None

    async def upsert_file(self, file: dict) -> None:
        await self.upsert_files([file])

    async def upsert_files(self, files: list[dict]) -> None:
        """Upsert a batch in one transaction.

        A commit per file makes a first scan of a large share needlessly slow
        and increases the chance of leaving a half-written ledger on shutdown.
        Keep the single-file API for callers, but make bulk indexing atomic.
        """
        assert self._db
        if not files:
            return
        keys = [
            "path",
            "share_id",
            "filename",
            "ext",
            "mime_type",
            "snippet",
            "owner",
            "size_bytes",
            "modified_time",
            "created_time",
            "content_hash",
            "dir_mtime",
            "synced_at",
            "is_deleted",
        ]
        values = [[file.get(k) for k in keys] for file in files]
        placeholders = ", ".join("?" for _ in keys)
        cols = ", ".join(keys)
        updates = ", ".join(f"{k} = excluded.{k}" for k in keys if k != "path")
        sql = f"INSERT INTO file_ledger ({cols}) VALUES ({placeholders}) ON CONFLICT(path) DO UPDATE SET {updates}"
        await self._db.executemany(sql, values)
        await self._db.commit()

    async def mark_deleted(self, path: str) -> None:
        assert self._db
        await self._db.execute(
            "UPDATE file_ledger SET is_deleted = 1 WHERE path = ?", (path,)
        )
        await self._db.commit()

    async def get_all_paths(self, share_id: str) -> set[str]:
        assert self._db
        cursor = await self._db.execute(
            "SELECT path FROM file_ledger "
            "WHERE share_id = ? AND is_deleted = 0 AND ext IS NOT NULL",
            (share_id,),
        )
        rows = await cursor.fetchall()
        return {row["path"] for row in rows}

    async def cleanup_deleted(self, share_id: str, known_paths: set[str]) -> None:
        assert self._db
        cursor = await self._db.execute(
            "SELECT path FROM file_ledger "
            "WHERE share_id = ? AND is_deleted = 0 AND ext IS NOT NULL",
            (share_id,),
        )
        rows = await cursor.fetchall()
        for row in rows:
            if row["path"] not in known_paths:
                await self._db.execute(
                    "UPDATE file_ledger SET is_deleted = 1 WHERE path = ?",
                    (row["path"],),
                )
        await self._db.commit()

    async def mark_deleted_paths(self, paths: list[str]) -> None:
        """Mark only deletions acknowledged by the SaaS sync endpoint."""
        assert self._db
        if not paths:
            return
        await self._db.executemany(
            "UPDATE file_ledger SET is_deleted = 1 WHERE path = ?",
            [(path,) for path in paths],
        )
        await self._db.commit()
