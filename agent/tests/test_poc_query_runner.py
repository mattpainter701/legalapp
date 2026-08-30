from __future__ import annotations

import json
import sqlite3
from io import StringIO

import pytest
from clarity_agent.poc_query_runner import opaque_doc_id, run_queries


def _create_index(db_path):
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        PRAGMA user_version=1;
        CREATE TABLE index_files(path TEXT PRIMARY KEY, share_id TEXT, ext TEXT,
            status TEXT,
            size_bytes INTEGER, modified_time TEXT, attempts INTEGER, lease_until REAL,
            next_attempt_at REAL, page_count INTEGER, extraction_error TEXT, indexed_at TEXT,
            content_hash TEXT);
        CREATE VIRTUAL TABLE index_fts USING fts5(text, path UNINDEXED, share_id UNINDEXED,
            page_no UNINDEXED, ordinal UNINDEXED, ext UNINDEXED);
        INSERT INTO index_files(path,share_id,ext,status)
            VALUES('\\\\server\\Firm\\Cases\\secret.txt','firm','.txt','ready');
        INSERT INTO index_fts(text,path,share_id,page_no,ordinal,ext)
            VALUES('unique benchmark phrase','\\\\server\\Firm\\Cases\\secret.txt','firm',7,0,'.txt');
        """
    )
    connection.commit()
    before = connection.execute(
        "SELECT name,sql FROM sqlite_master ORDER BY name"
    ).fetchall()
    connection.close()
    return before


@pytest.mark.asyncio
async def test_runner_emits_only_opaque_results_and_does_not_mutate_index(tmp_path):
    db_path = tmp_path / "index.db"
    before = _create_index(db_path)
    output = StringIO()
    await run_queries(
        db_path,
        [
            json.dumps(
                {
                    "query_id": "q1",
                    "query": "unique benchmark phrase",
                    "share_id": "firm",
                    "share_path": "\\\\server\\Firm",
                    "folder": "Cases",
                    "extensions": ["txt"],
                    "limit": 5,
                }
            )
        ],
        output,
    )
    record = json.loads(output.getvalue())
    assert set(record) == {"query_id", "latency_ms", "results"}
    assert record["query_id"] == "q1"
    assert record["results"] == [
        {"doc_id": opaque_doc_id("firm", r"Cases\secret.txt"), "page": 7}
    ]
    assert "secret.txt" not in json.dumps(record)
    assert "unique benchmark phrase" not in json.dumps(record)
    connection = sqlite3.connect(db_path)
    assert (
        connection.execute(
            "SELECT name,sql FROM sqlite_master ORDER BY name"
        ).fetchall()
        == before
    )
    connection.close()


@pytest.mark.asyncio
async def test_runner_rejects_meaningful_query_ids(tmp_path):
    db_path = tmp_path / "index.db"
    _create_index(db_path)
    query = {
        "query_id": "acme-spoliation-motion",
        "query": "unique benchmark phrase",
        "share_id": "firm",
        "share_path": "\\\\server\\Firm",
    }
    with pytest.raises(ValueError, match="opaque query_id"):
        await run_queries(db_path, [json.dumps(query)], StringIO())
