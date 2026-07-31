import sys
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REPO_ROOT = ROOT.parent

from mcp_server.bulk_manifest import choose_latest_snapshot, required_bulk_keys
from mcp_server.dispatcher import (
    JetsonTarget,
    build_worker_command,
    db_tunnel_endpoint,
    jetson_hosts_from_env,
    jetson_target_specs_from_env,
    jetson_user_from_env,
    tunneled_db_url,
)
from mcp_server.embedding_scheduler import SchedulerConfig, run_scheduler_once
from mcp_server.loader import (
    DEFAULT_MVP_STATES,
    best_opinion_text,
    bz2_decompress_command,
    court_matches_mvp,
    iter_bulk_csv_rows,
    parse_mvp_states,
    resolved_table_limit,
    should_keep_cluster,
)
from mcp_server.query_embeddings import QueryEmbeddingClient, format_vector_literal
from mcp_server.repository import CourtListenerRepository
from mcp_server.schema import SCHEMA_SQL
from mcp_server.tools import TOOL_NAMES, build_tool_manifest
from mcp_server.worker_config import WorkerConfig, partition_sql


class RecordingCursor:
    def __init__(self):
        self.sql = ""
        self.params = []
        self.description = []
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params or []
        self.executions.append((sql, self.params))

    def fetchall(self):
        return []


class RecordingConnection:
    def __init__(self):
        self.cursor_obj = RecordingCursor()

    def cursor(self):
        return self.cursor_obj


class SchedulerCursor:
    def __init__(self, lock_acquired=True, unembedded=0):
        self.lock_acquired = lock_acquired
        self.unembedded = unembedded
        self.executions = []
        self._last = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.executions.append((sql, params or []))
        if "pg_try_advisory_lock" in sql:
            self._last = "lock"
        elif "COUNT(*)" in sql and "embedding IS NULL" in sql:
            self._last = "count"
        elif "pg_advisory_unlock" in sql:
            self._last = "unlock"

    def fetchone(self):
        if self._last == "lock":
            return [self.lock_acquired]
        if self._last == "count":
            return [self.unembedded]
        return [None]


class SchedulerConnection:
    def __init__(self, lock_acquired=True, unembedded=0):
        self.cursor_obj = SchedulerCursor(
            lock_acquired=lock_acquired, unembedded=unembedded
        )
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_schema_defines_mcp_owned_opinion_chunks_with_mxbai_vectors():
    assert "CREATE TABLE IF NOT EXISTS opinion_chunks" in SCHEMA_SQL
    assert "embedding vector(1024)" in SCHEMA_SQL
    assert (
        "embedding_model text NOT NULL DEFAULT 'mixedbread-ai/mxbai-embed-large-v1'"
        in SCHEMA_SQL
    )
    assert "CREATE TABLE IF NOT EXISTS embedding_jobs" in SCHEMA_SQL
    assert "CREATE EXTENSION IF NOT EXISTS vector" in SCHEMA_SQL
    assert "USING hnsw (embedding vector_cosine_ops)" in SCHEMA_SQL


def test_courtlistener_compose_requires_password_and_local_bind_by_default():
    compose = (REPO_ROOT / "docker-compose.courtlistener-mcp.yml").read_text()

    assert "POSTGRES_PASSWORD: ${COURTLISTENER_DB_PASSWORD:?" in compose
    assert "${COURTLISTENER_DB_BIND:-127.0.0.1}" in compose
    assert "${COURTLISTENER_MCP_BIND:-127.0.0.1}" in compose
    assert (
        "POSTGRES_PASSWORD: ${COURTLISTENER_DB_PASSWORD:-courtlistener}" not in compose
    )
    assert "${COURTLISTENER_DB_BIND:-0.0.0.0}" not in compose


def test_courtlistener_compose_defines_embedding_scheduler_profile():
    compose = (REPO_ROOT / "docker-compose.courtlistener-mcp.yml").read_text()
    scheduler_section = compose.split("  embedding-scheduler:", 1)[1]

    assert "embedding-scheduler:" in compose
    assert 'profiles: ["embedding-scheduler"]' in compose
    assert "network_mode: host" in scheduler_section
    assert "SCHEDULER_DB_URL:" in scheduler_section
    assert "python" in scheduler_section
    assert "mcp_server.embedding_scheduler" in scheduler_section


def test_embedding_scheduler_dispatches_when_unembedded_chunks_exist():
    conn = SchedulerConnection(lock_acquired=True, unembedded=42)
    dispatches = []

    result = run_scheduler_once(
        conn,
        SchedulerConfig(
            db_url="postgresql://courtlistener:secret@db:5432/courtlistener",
            worker_db_url="postgresql://courtlistener:secret@192.168.1.10:5434/courtlistener",
            hosts="192.168.1.203",
            user="varta",
            script_dir="/data/legalapp-embeddings/scripts",
            batch_size=32,
            minimum_unembedded=1,
        ),
        dispatch=lambda targets,
        script_dir,
        db_url,
        batch_size,
        reverse_tunnel,
        tunnel_remote_port_base: dispatches.append(
            (
                targets,
                script_dir,
                db_url,
                batch_size,
                reverse_tunnel,
                tunnel_remote_port_base,
            )
        ),
    )

    assert result.dispatched is True
    assert result.unembedded_count == 42
    assert len(dispatches) == 1
    assert dispatches[0][0][0].host == "192.168.1.203"
    assert dispatches[0][1] == "/data/legalapp-embeddings/scripts"
    assert (
        dispatches[0][2]
        == "postgresql://courtlistener:secret@192.168.1.10:5434/courtlistener"
    )
    assert dispatches[0][3] == 32
    assert any("pg_advisory_unlock" in sql for sql, _ in conn.cursor_obj.executions)


def test_embedding_scheduler_skips_when_lock_is_held_or_queue_is_empty():
    held = SchedulerConnection(lock_acquired=False, unembedded=42)
    empty = SchedulerConnection(lock_acquired=True, unembedded=0)
    dispatches = []
    config = SchedulerConfig(
        db_url="postgresql://courtlistener:secret@db:5432/courtlistener",
        hosts="192.168.1.203",
        user="varta",
        script_dir="/data/legalapp-embeddings/scripts",
        minimum_unembedded=1,
    )

    held_result = run_scheduler_once(
        held, config, dispatch=lambda *args, **kwargs: dispatches.append(args)
    )
    empty_result = run_scheduler_once(
        empty, config, dispatch=lambda *args, **kwargs: dispatches.append(args)
    )

    assert held_result.dispatched is False
    assert held_result.reason == "lock_held"
    assert empty_result.dispatched is False
    assert empty_result.reason == "below_threshold"
    assert dispatches == []


def test_manifest_exposes_domain_scoped_legal_tools():
    manifest = build_tool_manifest()
    names = [tool["name"] for tool in manifest["tools"]]

    assert manifest["catalogVersion"] == "1"
    assert "protocolVersion" not in manifest
    assert names == TOOL_NAMES
    assert names == [
        "search_caselaw",
        "search_legal_authorities",
        "get_case_details",
        "get_full_opinion",
        "find_similar_cases",
        "search_by_citation",
        "validate_citation",
        "normalize_citation",
        "get_citation_network",
        "get_authority_treatment",
        "search_by_jurisdiction",
        "search_recent_authority",
        "get_court_info",
        "get_court_coverage",
        "search_dockets",
        "export_research_bundle",
        "sync_status",
        "corpus_status",
    ]


def test_case_details_contract_requires_exactly_one_identifier():
    manifest = build_tool_manifest()
    details_tool = next(
        tool for tool in manifest["tools"] if tool["name"] == "get_case_details"
    )
    schema = details_tool["inputSchema"]

    assert schema["oneOf"] == [
        {"required": ["opinion_id"], "not": {"required": ["cluster_id"]}},
        {"required": ["cluster_id"], "not": {"required": ["opinion_id"]}},
    ]
    assert schema["required"] == []


def test_full_opinion_contract_requires_exactly_one_identifier():
    manifest = build_tool_manifest()
    full_tool = next(
        tool for tool in manifest["tools"] if tool["name"] == "get_full_opinion"
    )
    schema = full_tool["inputSchema"]

    assert schema["oneOf"] == [
        {"required": ["opinion_id"], "not": {"required": ["cluster_id"]}},
        {"required": ["cluster_id"], "not": {"required": ["opinion_id"]}},
    ]


def test_repository_hybrid_search_uses_vector_and_fts_when_embedding_available():
    conn = RecordingConnection()
    embedding = [0.001] * 1024

    CourtListenerRepository(conn).search_caselaw(
        query="constructive possession",
        top_k=5,
        jurisdiction="nd",
        query_embedding=embedding,
    )

    sql = conn.cursor_obj.sql
    params = conn.cursor_obj.params

    assert "embedding <=>" in sql
    assert "websearch_to_tsquery" in sql
    assert "dense_rank" in sql
    assert "fts_rank" in sql
    assert "source_url" in sql
    assert "{{0,cite}}" not in sql
    assert "{0,cite}" in sql
    assert any(
        isinstance(param, str) and param.startswith("[0.001") for param in params
    )


def test_repository_search_falls_back_to_fts_when_query_embedding_unavailable():
    conn = RecordingConnection()

    CourtListenerRepository(conn).search_caselaw(
        query="constructive possession",
        top_k=5,
        query_embedding=None,
    )

    sql = conn.cursor_obj.sql

    assert "embedding <=>" not in sql
    assert "d.document_status = 'current'" in sql
    assert "s.enabled = TRUE" in sql
    assert "websearch_to_tsquery" in sql
    assert "source_url" in sql
    assert "{0,cite}" in sql


def test_repository_searches_general_authority_with_effective_date_filters():
    conn = RecordingConnection()

    CourtListenerRepository(conn).search_legal_authorities(
        "estate recovery",
        top_k=6,
        jurisdiction="US",
        source_keys=["cms:medicaid-estate-recovery"],
        authority_tiers=["agency_guidance"],
        effective_on="2026-07-31",
        query_embedding=None,
    )

    sql = conn.cursor_obj.sql
    assert "legal_document_chunks" in sql
    assert "legal_documents" in sql
    assert "websearch_to_tsquery" in sql
    assert "termination_date" in sql
    assert "embedding <=>" not in sql


def test_repository_uses_hybrid_search_for_general_authority():
    conn = RecordingConnection()

    CourtListenerRepository(conn).search_legal_authorities(
        "estate tax portability",
        top_k=4,
        jurisdiction="US",
        query_embedding=[0.001] * 1024,
    )

    sql = conn.cursor_obj.sql
    assert "legal_document_chunks" in sql
    assert "embedding <=>" in sql
    assert "dense_rank" in sql
    assert "fts_rank" in sql


def test_repository_normalizes_messy_citation_before_lookup():
    conn = RecordingConnection()

    result = CourtListenerRepository(conn).normalize_citation("  410   N.W. 2d   123  ")

    assert result["valid"] is True
    assert result["canonical"] == "410 N.W.2d 123"
    assert result["volume"] == "410"
    assert result["reporter"] == "N.W.2d"
    assert result["page"] == "123"
    assert conn.cursor_obj.params == ["410", "N.W.2d", "123"]


def test_repository_search_dockets_targets_docket_metadata():
    conn = RecordingConnection()

    CourtListenerRepository(conn).search_dockets(
        query="chapter 13 farm",
        court_id="ndb",
        jurisdiction="F",
        date_from="2020-01-01",
        top_k=10,
    )

    sql = conn.cursor_obj.sql
    assert "FROM dockets d" in sql
    assert "opinion_clusters" in sql
    assert "d.docket_number ILIKE" in sql
    assert "c.jurisdiction = %s" in sql


def test_repository_court_coverage_reports_loaded_ranges():
    conn = RecordingConnection()

    CourtListenerRepository(conn).court_coverage(jurisdiction="S")

    sql = conn.cursor_obj.sql
    assert "COUNT(DISTINCT d.docket_id)" in sql
    assert "COUNT(DISTINCT oc.cluster_id)" in sql
    assert "MIN(oc.date_filed)" in sql
    assert "GROUP BY c.court_id" in sql


def test_repository_status_tools_read_ingest_and_embedding_progress():
    conn = RecordingConnection()

    CourtListenerRepository(conn).sync_status()

    statements = "\n".join(sql for sql, _ in conn.cursor_obj.executions)
    assert "FROM ingest_runs" in statements
    assert "embedding IS NULL" in statements
    assert "embedding IS NOT NULL" in statements


def test_query_embedding_client_posts_to_configured_provider(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"embeddings": [[0.1, 0.2, 0.3]]}

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return FakeResponse()

    monkeypatch.setattr("mcp_server.query_embeddings.httpx.post", fake_post)

    client = QueryEmbeddingClient(
        url="http://jetson-query-embed:8031/embed",
        model="mixedbread-ai/mxbai-embed-large-v1",
        timeout_seconds=2.5,
    )

    assert client.embed_query("tax deficiency") == [0.1, 0.2, 0.3]
    assert calls == [
        (
            "http://jetson-query-embed:8031/embed",
            {
                "texts": [
                    "Represent this sentence for searching relevant passages: tax deficiency"
                ],
                "model": "mixedbread-ai/mxbai-embed-large-v1",
            },
            2.5,
        )
    ]
    assert format_vector_literal([0.1, 0.2]) == "[0.10000000,0.20000000]"


def test_bulk_manifest_selects_complete_latest_quarterly_snapshot():
    keys = [
        "bulk-data/courts-2026-03-31.csv.bz2",
        "bulk-data/dockets-2026-03-31.csv.bz2",
        "bulk-data/opinion-clusters-2026-03-31.csv.bz2",
        "bulk-data/opinions-2026-03-31.csv.bz2",
        "bulk-data/citations-2026-03-31.csv.bz2",
        "bulk-data/citation-map-2026-03-31.csv.bz2",
        "bulk-data/schema-2026-03-31.sql",
        "bulk-data/courts-2026-06-30.csv.bz2",
    ]

    snapshot = choose_latest_snapshot(keys)

    assert snapshot.date == "2026-03-31"
    assert set(snapshot.keys) == set(required_bulk_keys("2026-03-31"))


def test_loader_allows_large_courtlistener_csv_fields():
    assert csv.field_size_limit() >= 10 * 1024 * 1024


def test_loader_prefers_parallel_bz2_decompressor(monkeypatch, tmp_path):
    archive = tmp_path / "opinions.csv.bz2"
    archive.write_bytes(b"")
    monkeypatch.setattr(
        "mcp_server.loader.shutil.which",
        lambda name: "/usr/bin/lbzip2" if name == "lbzip2" else None,
    )

    assert bz2_decompress_command(archive) == ["/usr/bin/lbzip2", "-dc", str(archive)]


def test_loader_supports_table_specific_smoke_limits():
    assert resolved_table_limit(1000, None) == 1000
    assert resolved_table_limit(1000, 20) == 20
    assert resolved_table_limit(None, 20) == 20


def test_loader_parses_backslash_escaped_multiline_csv_fields(tmp_path):
    sample = tmp_path / "opinions.csv"
    sample.write_text(
        "id,xml_harvard,cluster_id\n"
        '"1","<opinion type=\\"majority\\">\n<p>text</p>","4249781"\n',
        encoding="utf-8",
    )

    rows = list(iter_bulk_csv_rows(sample))

    assert rows == [
        {
            "id": "1",
            "xml_harvard": '<opinion type="majority">\n<p>text</p>',
            "cluster_id": "4249781",
        }
    ]


def test_loader_uses_harvard_xml_as_opinion_text_fallback():
    row = {
        "plain_text": "",
        "html_with_citations": "",
        "html": "",
        "xml_harvard": "<opinion>text</opinion>",
    }

    assert best_opinion_text(row) == "<opinion>text</opinion>"


def test_worker_config_locks_mxbai_1024_and_partition_query():
    config = WorkerConfig(worker_id=1, total_workers=3, batch_size=32)

    assert config.model == "mixedbread-ai/mxbai-embed-large-v1"
    assert config.dim == 1024
    assert config.batch_size == 32
    assert "opinion_chunks" in partition_sql()
    assert "embedding IS NULL" in partition_sql()
    assert "ABS(HASHTEXT(id::text)) %% %s = %s" in partition_sql()
    authority_sql = partition_sql("legal_document_chunks")
    assert "legal_document_chunks" in authority_sql
    assert "legal_documents" in authority_sql
    assert "authority_tier" in authority_sql


def test_dispatcher_supports_indexed_jetson_env(monkeypatch):
    monkeypatch.delenv("JETSON_HOSTS", raising=False)
    monkeypatch.setenv("JETSON_0_HOST", "jetson-a.local")
    monkeypatch.setenv("JETSON1_HOST", "jetson-b.local")
    monkeypatch.setenv("JETSON3_HOST", "jetson-c.local")

    assert jetson_hosts_from_env() == [
        "jetson-a.local",
        "jetson-b.local",
        "jetson-c.local",
    ]


def test_dispatcher_supports_indexed_jetson_user(monkeypatch):
    monkeypatch.setenv("JETSON_3_USER", "nvidia")

    assert jetson_user_from_env(3, "jetson") == "nvidia"
    assert jetson_user_from_env(2, "jetson") == "jetson"


def test_dispatcher_preserves_sparse_indexed_jetson_user(monkeypatch):
    monkeypatch.delenv("JETSON_HOSTS", raising=False)
    monkeypatch.setenv("JETSON_3_HOST", "172.16.40.100")
    monkeypatch.setenv("JETSON_3_USER", "varta")

    assert jetson_target_specs_from_env("", "jetson") == [
        JetsonTarget(env_index=3, worker_id=0, host="172.16.40.100", user="varta")
    ]


def test_dispatcher_uses_user_writable_log_dir():
    command = build_worker_command(
        script_dir="/home/varta/legalapp/scripts",
        db_url="postgresql://example/db",
        worker_id=0,
        total_workers=1,
        batch_size=32,
    )

    assert "mkdir -p ~/clarity-legal-logs" in command
    assert ">> ~/clarity-legal-logs/courtlistener_worker_0.log" in command


def test_dispatcher_foreground_command_supports_reverse_tunnel_session():
    command = build_worker_command(
        script_dir="/data/legalapp-embeddings/scripts",
        db_url="postgresql://courtlistener:secret@127.0.0.1:15434/courtlistener",
        worker_id=0,
        total_workers=1,
        batch_size=8,
        background=False,
    )

    assert "nohup" not in command
    assert command.startswith("mkdir -p ~/clarity-legal-logs")
    assert "PYTHONUNBUFFERED=1 python3 jetson_embed_worker.py" in command
    assert not command.endswith(" &")


def test_dispatcher_rewrites_db_url_for_jetson_reverse_tunnel():
    direct = "postgresql://courtlistener:secret@172.16.16.202:5434/courtlistener"

    assert db_tunnel_endpoint(direct) == ("172.16.16.202", 5434)
    assert (
        tunneled_db_url(direct, 15434)
        == "postgresql://courtlistener:secret@127.0.0.1:15434/courtlistener"
    )


def test_mvp_state_filter_matches_upper_midwest_courts():
    assert DEFAULT_MVP_STATES == ("ND", "MT", "MN", "SD")
    assert court_matches_mvp(
        {
            "id": "nd",
            "short_name": "N.D.",
            "full_name": "North Dakota Supreme Court",
            "jurisdiction": "S",
        },
        states=("ND",),
        include_specialty=False,
    )
    assert court_matches_mvp(
        {
            "id": "minn",
            "short_name": "Minn.",
            "full_name": "Minnesota Court of Appeals",
            "jurisdiction": "S",
        },
        states=("MN",),
        include_specialty=False,
    )
    assert not court_matches_mvp(
        {
            "id": "cafc",
            "short_name": "Fed. Cir.",
            "full_name": "United States Court of Appeals for the Federal Circuit",
            "jurisdiction": "F",
        },
        states=("ND", "MT", "MN", "SD"),
        include_specialty=False,
    )


def test_mvp_specialty_filter_keeps_tax_immigration_and_regional_bankruptcy():
    states = parse_mvp_states("ND, mt, Minnesota, south dakota")
    assert states == ("ND", "MT", "MN", "SD")
    assert court_matches_mvp(
        {"id": "tax", "short_name": "T.C.", "full_name": "United States Tax Court"},
        states=states,
        include_specialty=True,
    )
    assert court_matches_mvp(
        {
            "id": "bia",
            "short_name": "BIA",
            "full_name": "Board of Immigration Appeals",
        },
        states=states,
        include_specialty=True,
    )
    assert court_matches_mvp(
        {
            "id": "ndb",
            "short_name": "Bankr. D.N.D.",
            "full_name": "United States Bankruptcy Court, District of North Dakota",
        },
        states=states,
        include_specialty=True,
    )


def test_mvp_cluster_filter_prefers_precedential_authority():
    assert should_keep_cluster(
        {"precedential_status": "Published"}, precedential_only=True
    )
    assert should_keep_cluster(
        {"precedential_status": "Precedential"}, precedential_only=True
    )
    assert not should_keep_cluster(
        {"precedential_status": "Unpublished"}, precedential_only=True
    )
    assert should_keep_cluster(
        {"precedential_status": "Unpublished"}, precedential_only=False
    )
