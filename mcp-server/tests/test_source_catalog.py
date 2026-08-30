import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.schema import SCHEMA_SQL  # noqa: E402
from mcp_server.source_catalog import (  # noqa: E402
    CatalogValidationError,
    catalog_summary,
    load_catalog,
    seed_catalog,
    validate_catalog,
)


class RecordingCursor:
    def __init__(self):
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.executions.append((sql, params or []))


class RecordingConnection:
    def __init__(self):
        self.cursor_obj = RecordingCursor()
        self.commits = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1


def test_bundled_source_catalog_is_valid_and_covers_priority_domains():
    catalog = load_catalog()
    keys = {source["source_key"] for source in catalog["sources"]}

    assert {
        "ohio:laws",
        "federal:us-code",
        "irs:internal-revenue-bulletin",
        "cms:medicare-coverage-api",
        "cms:medicaid-estate-recovery",
        "sec:edgar",
        "nd:century-code",
    }.issubset(keys)
    assert len(keys) == len(catalog["sources"])


def test_bundled_fragments_cover_requested_federal_and_state_source_families():
    catalog = load_catalog()
    keys = {source["source_key"] for source in catalog["sources"]}

    assert {
        "irs:tax-law-official-guidance",
        "ecfr:current-api",
        "uscourts:federal-rules",
        "crs:constitution-annotated",
        "medicare:consumer-program-guidance",
        "freelawproject:github-organization",
        "minnesota:statutes",
        "south-dakota:legislature-statutes",
        "california:legislative-information",
        "texas:constitution-statutes",
        "florida:statutes",
        "municode:code-library",
        "findlaw:legal-research",
    }.issubset(keys)
    assert catalog["metadata"]["fragment_files"] == [
        "federal_freelaw.json",
        "federal_rules_research.json",
        "nd_mn_sd.json",
        "oh_ca_tx_fl_local_secondary.json",
    ]


def test_federal_rules_and_research_sources_have_auditable_bounded_policy():
    sources = {source["source_key"]: source for source in load_catalog()["sources"]}

    rules = sources["uscourts:federal-rules"]
    conan = sources["crs:constitution-annotated"]
    tax_court = sources["ustaxcourt:opinions"]

    assert rules["ingestion_mode"] == "manifest"
    assert rules["coverage_kind"] == "bounded"
    assert rules["enabled"] is True
    assert rules["implementation_status"] == "implemented"
    assert "reviewed allowlist" in rules["acquisition_basis"].lower()
    assert conan["authority_tier"] == "secondary_metadata"
    assert conan["official_status"] == "official_authenticated"
    assert conan["enabled"] is True
    assert conan["implementation_status"] == "implemented"
    assert "not binding primary law" in conan["coverage_notes"]
    assert tax_court["enabled"] is True
    assert tax_court["implementation_status"] == "implemented"
    assert "DAWSON" in tax_court["coverage_notes"]


def test_oh_ca_tx_fl_secondary_fragment_is_conservative_and_complete():
    catalog = load_catalog()
    sources = {source["source_key"]: source for source in catalog["sources"]}
    expected = {
        "california:legislative-information",
        "california:judicial-branch",
        "texas:constitution-statutes",
        "texas:rules-forms",
        "florida:statutes",
        "florida:administrative-code",
        "florida:bar-ethics",
        "municode:code-library",
        "amlegal:code-library",
        "cornell-lii:ohio",
        "justia:texas-law",
        "findlaw:legal-research",
        "nolo:legal-guidance",
    }

    assert expected.issubset(sources)
    fragment_sources = [
        source
        for source in catalog["sources"]
        if source["source_key"] in expected
    ]
    assert all(source["enabled"] is False for source in fragment_sources)
    assert sources["california:legislative-information"]["access_type"] == "blocked_robots"
    assert sources["california:legislative-information"]["ingestion_mode"] == "prohibited"
    assert sources["municode:code-library"]["storage_policy"] == "prohibited"
    assert sources["nolo:legal-guidance"]["license_status"] == "restricted"
    assert sources["cornell-lii:ohio"]["ingestion_mode"] == "query_time"


def test_catalog_can_merge_valid_source_fragments(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog = load_catalog()
    catalog["sources"] = catalog["sources"][:1]
    catalog.pop("metadata", None)
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    fragments = tmp_path / "fragments"
    fragments.mkdir()
    source = copy.deepcopy(load_catalog()["sources"][1])
    (fragments / "state.json").write_text(
        json.dumps({"schema_version": 1, "sources": [source]}), encoding="utf-8"
    )

    merged = load_catalog(catalog_path, fragments_dir=fragments)

    assert [item["source_key"] for item in merged["sources"]] == [
        catalog["sources"][0]["source_key"],
        source["source_key"],
    ]
    assert merged["metadata"]["fragment_files"] == ["state.json"]


def test_catalog_fragment_duplicate_source_keys_are_rejected(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog = load_catalog()
    catalog["sources"] = catalog["sources"][:1]
    catalog.pop("metadata", None)
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    fragments = tmp_path / "fragments"
    fragments.mkdir()
    (fragments / "duplicate.json").write_text(
        json.dumps({"schema_version": 1, "sources": catalog["sources"]}),
        encoding="utf-8",
    )

    with pytest.raises(CatalogValidationError, match="duplicate source_key"):
        load_catalog(catalog_path, fragments_dir=fragments)


def test_catalog_marks_ohio_laws_robots_block_as_non_ingestible():
    catalog = load_catalog()
    ohio = next(source for source in catalog["sources"] if source["source_key"] == "ohio:laws")

    assert ohio["access_type"] == "blocked_robots"
    assert ohio["ingestion_mode"] == "manual"
    assert ohio["storage_policy"] == "metadata_only"
    assert ohio["enabled"] is False


def test_validation_rejects_enabling_a_robots_blocked_source():
    catalog = load_catalog()
    invalid = copy.deepcopy(catalog)
    ohio = next(source for source in invalid["sources"] if source["source_key"] == "ohio:laws")
    ohio["enabled"] = True

    with pytest.raises(CatalogValidationError, match="robots-blocked"):
        validate_catalog(invalid)


def test_supreme_court_of_ohio_sources_record_operator_reported_permission():
    catalog = load_catalog()
    permitted_keys = {
        "ohio:supreme-court-rules",
        "ohio:probate-forms",
        "ohio:mediation-rules-forms",
    }
    sources = {
        source["source_key"]: source
        for source in catalog["sources"]
        if source["source_key"] in permitted_keys
    }

    assert set(sources) == permitted_keys
    assert all(source["license_status"] == "permission_granted" for source in sources.values())
    assert all(source["authorization_basis"] for source in sources.values())


def test_validation_rejects_permission_without_an_authorization_record():
    catalog = load_catalog()
    invalid = copy.deepcopy(catalog)
    source = next(
        item
        for item in invalid["sources"]
        if item["source_key"] == "ohio:supreme-court-rules"
    )
    source.pop("authorization_basis")

    with pytest.raises(CatalogValidationError, match="authorization_basis"):
        validate_catalog(invalid)


def test_catalog_summary_surfaces_policy_and_implementation_state():
    summary = catalog_summary(load_catalog())

    assert summary["source_count"] >= 15
    assert summary["enabled_count"] >= 1
    assert "ohio:laws" in summary["policy_hold_source_keys"]
    assert summary["by_ingestion_mode"]["bulk"] >= 3


def test_schema_defines_general_authority_documents_and_vector_chunks():
    assert "CREATE TABLE IF NOT EXISTS legal_documents" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS legal_document_chunks" in SCHEMA_SQL
    assert "UNIQUE (source_key, external_id)" in SCHEMA_SQL
    assert "ix_legal_document_chunks_embedding_hnsw" in SCHEMA_SQL


def test_seed_catalog_upserts_catalog_fields_without_operational_counters():
    catalog = load_catalog()
    catalog["sources"][0]["retry_action"] = "Recheck the approved API terms."
    conn = RecordingConnection()

    count = seed_catalog(conn, catalog)

    assert count == len(catalog["sources"])
    assert len(conn.cursor_obj.executions) == count
    assert conn.commits == 1
    sql, params = conn.cursor_obj.executions[0]
    assert "ON CONFLICT (source_key) DO UPDATE" in sql
    assert "item_count" not in sql
    assert params[0] == catalog["sources"][0]["source_key"]
    metadata = json.loads(params[-1])
    assert metadata["retry_action"] == "Recheck the approved API terms."
