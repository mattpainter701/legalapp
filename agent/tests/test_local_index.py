"""Focused tests for the agent-local private full-text index."""

from __future__ import annotations

import time
import sqlite3
from pathlib import Path

import pytest
from clarity_agent.local_index import LocalSearchIndex, read_index_stats

SHARE_A = r"\\SERVER\Firm"
SHARE_B = r"\\SERVER\Archive"
ASSIGNED = [
    {"share_id": "firm", "share_path": SHARE_A},
    {"share_id": "archive", "share_path": SHARE_B},
]


async def _allow_path(job):
    return True


async def _make_index(tmp_path, fetcher, **kwargs):
    index = LocalSearchIndex(str(tmp_path / "search-index.db"), **kwargs)
    await index.init()
    assert index.available
    index.start(fetcher, path_validator=_allow_path)
    return index


async def _wait(index: LocalSearchIndex):
    await index.wait_until_idle()


def _file(path, share_id, content_hash="hash", ext=None, size_bytes=None):
    suffix = ext or path.rsplit(".", 1)[-1]
    return {
        "path": path,
        "share_id": share_id,
        "content_hash": content_hash,
        "size_bytes": len(content_hash) if size_bytes is None else size_bytes,
        "modified_time": "2026-08-28T00:00:00Z",
        "ext": "." + suffix.lstrip("."),
    }


@pytest.mark.asyncio
async def test_indexes_and_searches_full_body_with_match_centered_snippet(tmp_path):
    path = SHARE_A + r"\Cases\historic.txt"
    body = "background " * 350 + "NEGLIGENT SPOLIATION" + " conclusion " * 350

    async def fetcher(job):
        assert job["path"] == path
        return body.encode()

    index = await _make_index(tmp_path, fetcher)
    try:
        await index.enqueue(_file(path, "firm"))
        await _wait(index)
        result = await index.search(
            "negligent spoliation", [{"share_id": "firm"}], ASSIGNED, None, 10
        )
        assert result["index_state"] == "ready"
        assert result["indexed_files"] == 1
        assert result["pending_files"] == 0
        assert len(result["hits"]) == 1
        hit = result["hits"][0]
        assert hit["relative_path"] == r"Cases\historic.txt"
        assert "NEGLIGENT" in hit["snippet"]
        assert len(hit["snippet"]) <= 1000
    finally:
        await index.close()


@pytest.mark.asyncio
async def test_legal_citation_punctuation_is_plain_query_not_fts_syntax(tmp_path):
    path = SHARE_A + r"\Cases\citation.txt"

    async def fetcher(job):
        return b"The claim arises under 42 U.S.C. section 1983 and was timely filed."

    index = await _make_index(tmp_path, fetcher)
    try:
        await index.enqueue(_file(path, "firm"))
        await _wait(index)
        result = await index.search(
            "42 U.S.C. \u00a7 1983", [{"share_id": "firm"}], ASSIGNED, None, 10
        )
        assert result["hits"]
        assert result["hits"][0]["relative_path"] == r"Cases\citation.txt"
    finally:
        await index.close()


@pytest.mark.asyncio
async def test_natural_question_uses_bounded_recall_fallback(tmp_path):
    relaxed_path = SHARE_A + r"\Cases\sanctions.txt"
    strict_path = SHARE_A + r"\Cases\bad-faith.txt"
    files = {
        relaxed_path: b"The court imposed sanctions after spoliation of the records.",
        strict_path: b"Bad faith spoliation warranted sanctions under the order.",
    }

    async def fetcher(job):
        return files[job["path"]]

    index = await _make_index(tmp_path, fetcher)
    try:
        await index.enqueue_many([_file(path, "firm") for path in files])
        await _wait(index)
        result = await index.search(
            "please find matters about spoliation sanctions and bad faith",
            [{"share_id": "firm"}],
            ASSIGNED,
            None,
            10,
        )
        assert [hit["relative_path"] for hit in result["hits"]] == [
            r"Cases\bad-faith.txt",
            r"Cases\sanctions.txt",
        ]
        assert result["hits"][0]["score"] > result["hits"][1]["score"]
    finally:
        await index.close()


@pytest.mark.asyncio
async def test_multiple_share_folder_scopes_are_or_pairs_and_extensions_filter(
    tmp_path,
):
    files = {
        SHARE_A + r"\Open\one.txt": b"shared privilege discussion",
        SHARE_A + r"\Closed\two.txt": b"shared privilege but excluded folder",
        SHARE_B + r"\Open\three.rtf": rb"{\rtf1\ansi shared privilege in archive}",
        SHARE_B + r"\Open\four.txt": b"shared privilege in another text file",
    }

    async def fetcher(job):
        return files[job["path"]]

    index = await _make_index(tmp_path, fetcher)
    try:
        await index.enqueue_many(
            [
                _file(path, "firm" if path.startswith(SHARE_A) else "archive")
                for path in files
            ]
        )
        await _wait(index)
        result = await index.search(
            "shared privilege",
            [
                {"share_id": "firm", "folder_path": "Open"},
                {"share_id": "archive", "folder_path": "Open"},
            ],
            ASSIGNED,
            None,
            10,
        )
        assert {hit["relative_path"] for hit in result["hits"]} == {
            r"Open\one.txt",
            r"Open\three.rtf",
            r"Open\four.txt",
        }
        filtered = await index.search(
            "shared privilege",
            [
                {"share_id": "firm", "folder_path": "Open"},
                {"share_id": "archive", "folder_path": "Open"},
            ],
            ASSIGNED,
            [".rtf"],
            10,
        )
        assert [hit["relative_path"] for hit in filtered["hits"]] == [r"Open\three.rtf"]
    finally:
        await index.close()


@pytest.mark.asyncio
async def test_relative_path_safety_drops_tampered_outside_hit(tmp_path):
    path = SHARE_A + r"\Cases\safe.txt"

    async def fetcher(job):
        return b"confidential target"

    index = await _make_index(tmp_path, fetcher)
    try:
        await index.enqueue(_file(path, "firm"))
        await _wait(index)
        assert (
            await index.search(
                "confidential", [{"share_id": "firm"}], ASSIGNED, None, 10
            )
        )["hits"]
        async with index._db_lock:
            await index._db.execute(
                "UPDATE index_fts SET path=? WHERE path=?",
                (r"\\SERVER\Other\secret.txt", path),
            )
            await index._db.commit()
        assert (
            await index.search(
                "confidential", [{"share_id": "firm"}], ASSIGNED, None, 10
            )
        )["hits"] == []
    finally:
        await index.close()


@pytest.mark.asyncio
async def test_missing_index_seeding_only_inserts_missing_rows(tmp_path):
    existing = SHARE_A + r"\existing.txt"
    missing = SHARE_A + r"\missing.txt"

    async def fetcher(job):
        return b"existing body"

    index = await _make_index(tmp_path, fetcher)
    try:
        await index.enqueue(_file(existing, "firm"))
        await _wait(index)
        await index.enqueue_many(
            [_file(existing, "firm", content_hash="new"), _file(missing, "firm")],
            only_if_missing=True,
        )
        await _wait(index)
        async with index._db.execute(
            "SELECT content_hash,status FROM index_files WHERE path=?", (existing,)
        ) as cursor:
            row = await cursor.fetchone()
        assert row["content_hash"] == "hash"
        async with index._db.execute(
            "SELECT status FROM index_files WHERE path=?", (missing,)
        ) as cursor:
            row = await cursor.fetchone()
        assert row["status"] == "ready"
    finally:
        await index.close()


@pytest.mark.asyncio
async def test_delete_removes_file_and_fts_rows(tmp_path):
    path = SHARE_A + r"\Cases\remove.txt"

    async def fetcher(job):
        return b"remove me"

    index = await _make_index(tmp_path, fetcher)
    try:
        await index.enqueue(_file(path, "firm"))
        await _wait(index)
        await index.delete([path])
        result = await index.search(
            "remove", [{"share_id": "firm"}], ASSIGNED, None, 10
        )
        assert result["hits"] == []
        async with index._db.execute("SELECT count(*) FROM index_files") as cursor:
            assert (await cursor.fetchone())[0] == 0
    finally:
        await index.close()


@pytest.mark.asyncio
async def test_unsupported_oversized_and_no_text_are_explicit_statuses(tmp_path):
    files = {
        SHARE_A + r"\bad.doc": b"not indexed",
        SHARE_A + r"\large.txt": b"large",
        SHARE_A + r"\empty.txt": b"",
    }

    async def fetcher(job):
        return files[job["path"]]

    index = await _make_index(tmp_path, fetcher, max_file_bytes=4)
    try:
        await index.enqueue_many(
            [
                _file(
                    path,
                    "firm",
                    ext=path.rsplit(".", 1)[-1],
                    size_bytes=5 if path.endswith("large.txt") else None,
                )
                for path in files
            ]
        )
        await _wait(index)
        async with index._db.execute(
            "SELECT path,status,extraction_error FROM index_files ORDER BY path"
        ) as cursor:
            rows = {row["path"]: row for row in await cursor.fetchall()}
        assert rows[SHARE_A + r"\bad.doc"]["status"] == "unsupported"
        assert rows[SHARE_A + r"\bad.doc"]["extraction_error"] == "unsupported_format"
        assert rows[SHARE_A + r"\large.txt"]["status"] == "error"
        assert rows[SHARE_A + r"\large.txt"]["extraction_error"] == "file_too_large"
        assert rows[SHARE_A + r"\empty.txt"]["status"] == "error"
        assert (
            rows[SHARE_A + r"\empty.txt"]["extraction_error"] == "no_extractable_text"
        )
        stats = await index.stats()
        assert stats["available"] is True
        assert stats["statuses"]["unsupported"]["files"] == 1
        assert stats["statuses"]["error"]["files"] == 2
        assert stats["by_extension"][".doc"]["unsupported"]["files"] == 1
        assert stats["by_extension"][".txt"]["error"]["files"] == 2
        assert stats["database_bytes"] > 0
        snapshot = await read_index_stats(str(tmp_path / "search-index.db"))
        assert snapshot["available"] is True
        assert snapshot["statuses"] == stats["statuses"]
        assert snapshot["by_extension"] == stats["by_extension"]
        degraded = await index.search(
            "anything", [{"share_id": "firm"}], ASSIGNED, None, 10
        )
        assert degraded["index_state"] == "degraded"
    finally:
        await index.close()


@pytest.mark.asyncio
async def test_index_uses_wal_and_bounds_worker_count(tmp_path):
    async def fetcher(job):
        return b"worker pool"

    index = LocalSearchIndex(str(tmp_path / "search-index.db"))
    await index.init()
    try:
        async with index._db.execute("PRAGMA journal_mode") as cursor:
            assert (await cursor.fetchone())[0].lower() == "wal"
        index.start(fetcher, path_validator=_allow_path, worker_count=99)
        assert len(index._workers) == 4
    finally:
        await index.close()


@pytest.mark.asyncio
async def test_index_requires_absolute_path_in_a_dedicated_directory(tmp_path):
    relative = LocalSearchIndex("search-index.db")
    with pytest.raises(ValueError, match="absolute"):
        await relative.init()

    root_level = LocalSearchIndex(str(Path(tmp_path.anchor) / "search-index.db"))
    with pytest.raises(ValueError, match="dedicated subdirectory"):
        await root_level.init()

    unc = LocalSearchIndex(r"\\server\share\search-index.db")
    with pytest.raises(ValueError, match="local disk"):
        await unc.init()


@pytest.mark.asyncio
async def test_index_fails_closed_when_acl_restriction_fails(tmp_path, monkeypatch):
    calls = []

    def deny_restriction(path, *, required=False):
        calls.append((path, required))
        raise PermissionError("ACL hardening failed")

    monkeypatch.setattr("clarity_agent.local_index._restrict", deny_restriction)
    index = LocalSearchIndex(str(tmp_path / "private" / "search-index.db"))

    with pytest.raises(PermissionError, match="ACL hardening failed"):
        await index.init()

    assert calls and calls[0][1] is True
    assert index._db is None
    assert not index.available


@pytest.mark.asyncio
async def test_readonly_index_rejects_all_mutators_and_sidecar_creation(tmp_path):
    path = SHARE_A + r"\Cases\readonly.txt"

    async def fetcher(job):
        return b"readonly benchmark"

    writable = await _make_index(tmp_path, fetcher)
    await writable.enqueue(_file(path, "firm"))
    await _wait(writable)
    await writable.close()
    before = {item.name for item in tmp_path.iterdir()}

    readonly = LocalSearchIndex(str(tmp_path / "search-index.db"))
    await readonly.init_readonly()
    try:
        result = await readonly.search(
            "readonly", [{"share_id": "firm"}], ASSIGNED, None, 10
        )
        assert result["hits"]
        with pytest.raises(RuntimeError, match="read-only"):
            readonly.start(fetcher, path_validator=_allow_path)
        with pytest.raises(RuntimeError, match="read-only"):
            await readonly.enqueue(_file(path, "firm"))
        with pytest.raises(RuntimeError, match="read-only"):
            await readonly.delete([path])
    finally:
        await readonly.close()
    assert {item.name for item in tmp_path.iterdir()} == before


@pytest.mark.asyncio
async def test_readonly_index_rejects_an_unknown_schema_version(tmp_path):
    db_path = tmp_path / "search-index.db"
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA user_version=999")
    connection.commit()
    connection.close()

    index = LocalSearchIndex(str(db_path))
    with pytest.raises(RuntimeError, match="schema version"):
        await index.init_readonly()


@pytest.mark.asyncio
async def test_index_rejects_repeated_initialization(tmp_path):
    index = LocalSearchIndex(str(tmp_path / "search-index.db"))
    await index.init()
    try:
        with pytest.raises(RuntimeError, match="already initialized"):
            await index.init()
        with pytest.raises(RuntimeError, match="already initialized"):
            await index.init_readonly()
    finally:
        await index.close()


@pytest.mark.asyncio
async def test_version_zero_manifest_backfills_extension(tmp_path):
    db_path = tmp_path / "search-index.db"
    path = SHARE_A + r"\Cases\legacy.PDF"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE index_files (
            path TEXT PRIMARY KEY, share_id TEXT NOT NULL, content_hash TEXT,
            size_bytes INTEGER, modified_time TEXT, status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, lease_until REAL,
            next_attempt_at REAL, page_count INTEGER, extraction_error TEXT,
            indexed_at TEXT
        );
        CREATE VIRTUAL TABLE index_fts USING fts5(
            text, path UNINDEXED, share_id UNINDEXED, page_no UNINDEXED,
            ordinal UNINDEXED, ext UNINDEXED
        );
        """
    )
    connection.execute(
        "INSERT INTO index_files(path,share_id,status) VALUES(?, 'firm', 'ready')",
        (path,),
    )
    connection.commit()
    connection.close()

    index = LocalSearchIndex(str(db_path))
    await index.init()
    try:
        async with index._db.execute(
            "SELECT ext FROM index_files WHERE path=?", (path,)
        ) as cursor:
            assert (await cursor.fetchone())["ext"] == ".pdf"
    finally:
        await index.close()


@pytest.mark.asyncio
async def test_worker_rejects_job_when_path_validator_fails(tmp_path):
    fetched = False

    async def fetcher(job):
        nonlocal fetched
        fetched = True
        return b"must not be read"

    async def reject(job):
        return False

    index = LocalSearchIndex(str(tmp_path / "search-index.db"))
    await index.init()
    index.start(fetcher, path_validator=reject)
    try:
        path = SHARE_A + r"\Cases\outside.txt"
        await index.enqueue(_file(path, "firm"))
        await index.wait_until_idle()
        async with index._db.execute(
            "SELECT status,extraction_error FROM index_files WHERE path=?", (path,)
        ) as cursor:
            row = await cursor.fetchone()
        assert row["status"] == "error"
        assert row["extraction_error"] == "path_outside_assigned_share"
        assert fetched is False
    finally:
        await index.close()


@pytest.mark.asyncio
async def test_pending_index_reports_building(tmp_path):
    index = LocalSearchIndex(str(tmp_path / "search-index.db"))
    await index.init()
    try:
        await index.enqueue(_file(SHARE_A + r"\pending.txt", "firm"))
        result = await index.search(
            "pending", [{"share_id": "firm"}], ASSIGNED, None, 10
        )
        assert result["index_state"] == "building"
        assert result["pending_files"] == 1
    finally:
        await index.close()


@pytest.mark.asyncio
async def test_long_documents_do_not_crowd_distinct_files_out_of_results(tmp_path):
    index = LocalSearchIndex(str(tmp_path / "search-index.db"))
    await index.init()
    paths = [SHARE_A + rf"\Cases\crowded-{number}.txt" for number in range(3)]
    try:
        async with index._db_lock:
            await index._db.executemany(
                """INSERT INTO index_files(path,share_id,ext,status,size_bytes)
                   VALUES(?, 'firm', '.txt', 'ready', 1)""",
                [(path,) for path in paths],
            )
            chunks = [
                ("crowded", paths[0], "firm", None, number, ".txt")
                for number in range(254)
            ]
            chunks.extend(
                ("crowded", paths[1], "firm", None, number, ".txt")
                for number in range(254)
            )
            chunks.append(("crowded", paths[2], "firm", None, 0, ".txt"))
            await index._db.executemany(
                """INSERT INTO index_fts(text,path,share_id,page_no,ordinal,ext)
                   VALUES(?,?,?,?,?,?)""",
                chunks,
            )
            await index._db.commit()
        result = await index.search(
            "crowded", [{"share_id": "firm"}], ASSIGNED, None, 3
        )
        assert {hit["relative_path"] for hit in result["hits"]} == {
            r"Cases\crowded-0.txt",
            r"Cases\crowded-1.txt",
            r"Cases\crowded-2.txt",
        }
    finally:
        await index.close()


@pytest.mark.asyncio
async def test_expired_running_lease_can_be_reclaimed(tmp_path):
    path = SHARE_A + r"\Cases\lease.txt"

    async def fetcher(job):
        return b"lease recovery"

    index = await _make_index(tmp_path, fetcher)
    try:
        await index.enqueue(_file(path, "firm"))
        await index.close()
        # Re-open without a worker, claim an expired row, and let init's
        # restart recovery put it back into the durable queue.
        recovered = LocalSearchIndex(str(tmp_path / "search-index.db"))
        await recovered.init()
        async with recovered._db_lock:
            await recovered._db.execute(
                "UPDATE index_files SET status='running', lease_until=? WHERE path=?",
                (time.time() - 1, path),
            )
            await recovered._db.commit()
        await recovered.close()
        restarted = LocalSearchIndex(str(tmp_path / "search-index.db"))
        await restarted.init()
        async with restarted._db.execute(
            "SELECT status,lease_until FROM index_files WHERE path=?", (path,)
        ) as cursor:
            row = await cursor.fetchone()
        assert row["status"] == "pending"
        assert row["lease_until"] is None
    finally:
        # The original index is already closed; this is deliberately safe for
        # failures before the close above.
        await index.close()
        if "restarted" in locals():
            await restarted.close()
