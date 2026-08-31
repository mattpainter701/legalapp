import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.opinion_backfill import (  # noqa: E402
    BatchResult,
    OpinionBackfillConfig,
    QUERY_PREFIX,
    QUEUE_INDEX,
    embed_batch,
    selection_sql,
    stage_insert_sql,
)
from mcp_server.schema import SCHEMA_SQL  # noqa: E402
import mcp_server.opinion_backfill as opinion_backfill  # noqa: E402


class RecordingModel:
    def __init__(self):
        self.texts = None

    def encode(self, texts, **_kwargs):
        self.texts = texts
        return [[0.0] * 1024 for _ in texts]


class QueueIndexCursor:
    def __init__(self, row):
        self.row = row
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _sql, params):
        self.params = params

    def fetchone(self):
        return self.row


class QueueIndexConnection:
    def __init__(self, row):
        self.cursor_instance = QueueIndexCursor(row)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_instance


def test_stage_schema_is_durable_and_dimension_checked():
    assert "CREATE TABLE IF NOT EXISTS opinion_embedding_backfill_stage" in SCHEMA_SQL
    assert (
        "chunk_id uuid PRIMARY KEY REFERENCES opinion_chunks(id) ON DELETE CASCADE"
        in SCHEMA_SQL
    )
    assert "CHECK (vector_dims(embedding) = 1024)" in SCHEMA_SQL
    assert (
        "UNLOGGED"
        not in SCHEMA_SQL.split("opinion_embedding_backfill_stage", 1)[1].split(";", 1)[
            0
        ]
    )


def test_stage_selection_uses_shared_skip_locked_queue():
    sql = selection_sql()
    assert "oc.embedding IS NULL" in sql
    assert "NOT EXISTS" in sql
    assert "FOR UPDATE OF oc SKIP LOCKED" in sql
    assert "HASHTEXT" not in sql


def test_stage_selection_supports_indexed_keyset_progress():
    sql = selection_sql(after_cursor=True)
    assert "(oc.created_at, oc.id) > (%s::timestamptz, %s::uuid)" in sql
    assert "ORDER BY oc.created_at, oc.id" in sql
    assert QUEUE_INDEX not in SCHEMA_SQL


def test_queue_index_validation_accepts_expected_access_path(monkeypatch):
    connection = QueueIndexConnection((True, True, True, True, True, True))
    monkeypatch.setattr(opinion_backfill, "connect", lambda _url: connection)
    opinion_backfill.require_queue_index("postgresql://db")
    assert connection.cursor_instance.params == [f"public.{QUEUE_INDEX}"]


@pytest.mark.parametrize(
    "row",
    [
        None,
        (False, True, True, True, True, True),
        (True, False, True, True, True, True),
        (True, True, False, True, True, True),
        (True, True, True, False, True, True),
        (True, True, True, True, False, True),
        (True, True, True, True, True, False),
    ],
)
def test_queue_index_validation_rejects_missing_or_wrong_index(monkeypatch, row):
    monkeypatch.setattr(
        opinion_backfill, "connect", lambda _url: QueueIndexConnection(row)
    )
    with pytest.raises(RuntimeError, match=QUEUE_INDEX):
        opinion_backfill.require_queue_index("postgresql://db")


def test_stage_insert_is_idempotent():
    sql = stage_insert_sql()
    assert "ON CONFLICT (chunk_id) DO NOTHING" in sql
    assert "RETURNING chunk_id" in sql


def test_embedding_prefix_is_applied_exactly_once():
    model = RecordingModel()
    vectors = embed_batch(model, ["Texas probate"], 32)
    assert len(vectors[0]) == 1024
    assert model.texts == [QUERY_PREFIX + "Texas probate"]
    assert model.texts[0].count(QUERY_PREFIX) == 1


def test_config_rejects_contract_drift():
    with pytest.raises(ValueError, match="mxbai v1"):
        OpinionBackfillConfig(0, 1, 32, "postgresql://db", dim=768).validate()


def test_batch_result_reports_measured_rate():
    result = BatchResult(128, 128, 0.1, 1.0, 0.1, 0.8, 2.0)
    assert result.chunks_per_second == 64
    assert '"write_seconds": 0.8' in result.log_line(1)
