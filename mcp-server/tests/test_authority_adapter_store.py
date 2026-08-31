import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.authority_adapter_store import (
    AdapterDocument,
    refresh_source_status,
    upsert_adapter_document,
)


class Cursor:
    def __init__(self):
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.executions.append((sql, params))


class Connection:
    def __init__(self):
        self.cursor_obj = Cursor()

    def cursor(self):
        return self.cursor_obj


class UpsertCursor(Cursor):
    def __init__(self):
        super().__init__()
        self.results = [("authority-fixture-v1",), None, (123,)]

    def execute(self, sql, params):
        assert sql.count("%s") == len(params)
        super().execute(sql, params)

    def fetchone(self):
        return self.results.pop(0)


class UpsertConnection:
    def __init__(self):
        self.cursor_obj = UpsertCursor()

    def cursor(self):
        return self.cursor_obj


def test_refresh_source_status_updates_freshness_and_embedding_counts_once_per_source():
    conn = Connection()

    refresh_source_status(conn, {"cms:transmittals", "govinfo:ecfr"})

    assert len(conn.cursor_obj.executions) == 2
    for sql, params in conn.cursor_obj.executions:
        assert "last_successful_sync_at=now()" in sql
        assert "embedded_chunk_count" in sql
        assert params == [params[0]] * 4


def test_upsert_adapter_document_binds_every_insert_column():
    conn = UpsertConnection()
    document = AdapterDocument(
        source_key="govinfo:ecfr",
        external_id="title-26-section-1",
        document_type="regulation",
        title="Section 1",
        citation="26 CFR 1",
        jurisdiction="federal",
        authority_tier="primary",
        canonical_url="https://example.test/26/1",
        text="A test regulation.",
        metadata={
            "namespace": "custom-private:spoof",
            "manifest_reference": "attacker-controlled",
            "harmless": "retained",
        },
    )

    result = upsert_adapter_document(conn, document)

    assert result["changed"] is True
    assert result["chunks_created"] == 1
    assert result["corpus_version"] == "authority-fixture-v1"
    sql = "\n".join(statement for statement, _ in conn.cursor_obj.executions)
    assert "public_authority_source_lineage" in sql
    assert "ON CONFLICT (source_key, external_id, corpus_version)" in sql
    assert "public_namespace, corpus_version" in sql
    insert_params = next(
        params
        for statement, params in conn.cursor_obj.executions
        if "INSERT INTO legal_documents" in statement
    )
    stored_metadata = json.loads(insert_params[-3])
    assert stored_metadata == {
        "harmless": "retained",
        "namespace": "public-authority",
    }
