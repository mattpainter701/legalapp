import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.authority_adapter_store import refresh_source_status


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


def test_refresh_source_status_updates_freshness_and_embedding_counts_once_per_source():
    conn = Connection()

    refresh_source_status(conn, {"cms:transmittals", "govinfo:ecfr"})

    assert len(conn.cursor_obj.executions) == 2
    for sql, params in conn.cursor_obj.executions:
        assert "last_successful_sync_at=now()" in sql
        assert "embedded_chunk_count" in sql
        assert params == [params[0]] * 4
