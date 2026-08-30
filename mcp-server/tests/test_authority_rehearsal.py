"""Opt-in database rehearsal for the authority release lifecycle.

CI environments with a disposable PostgreSQL/pgvector database can run this
with AUTHORITY_REHEARSAL_DATABASE_URL.  It is deliberately skipped by normal
unit runs so local tests never touch a developer or production database.
"""

import os
import uuid

import pytest

from mcp_server.control_plane import (
    promote_corpus_version,
    record_audit,
    rollback_corpus_version,
    sampled_audit,
    stage_corpus_version,
)
from mcp_server.database import connect
from mcp_server.loader import create_snapshot_chunks, init_schema
from mcp_server.repository import CourtListenerRepository


def test_authority_release_rehearsal():
    db_url = os.getenv("AUTHORITY_REHEARSAL_DATABASE_URL")
    if not db_url:
        pytest.skip("set AUTHORITY_REHEARSAL_DATABASE_URL for the disposable DB rehearsal")

    init_schema(db_url)
    version = "rehearsal-authority-" + uuid.uuid4().hex
    with connect(db_url) as conn:
        source_key = "rehearsal:source:" + version
        fixture_cluster_id = 97000001
        second_cluster_id = 97000002
        def add_fixture(version_name, suffix):
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO legal_sources
                    (source_key, publisher, source_type, canonical_url, enabled,
                     storage_policy, rights_decision, reviewed_at, reviewed_by,
                     expected_cadence, claim_safe_wording)
                    VALUES (%s, 'Rehearsal', 'statute', 'https://example.test', TRUE,
                            'normalized_text', 'official', now(), 'rehearsal-admin',
                            'daily', 'Fixture source only') ON CONFLICT DO NOTHING""", [source_key])
                cur.execute("""INSERT INTO legal_documents
                    (source_key, external_id, document_type, title, authority_tier,
                     canonical_url, corpus_version, text_content)
                    VALUES (%s, 'same-document', 'statute', %s, 'binding_primary',
                            'https://example.test/doc', %s, %s) RETURNING id""",
                            [source_key, 'Fixture ' + suffix, version_name, suffix + ' authority'])
                document_id = cur.fetchone()[0]
                cur.execute("""INSERT INTO legal_document_chunks
                    (document_id, chunk_index, content, content_hash, corpus_version)
                    VALUES (%s, 0, %s, md5(%s), %s)""",
                            [document_id, suffix + ' authority', suffix + ' authority', version_name])
                cur.execute("""INSERT INTO authority_case_clusters
                    (corpus_version, cluster_id, case_name, date_filed)
                    VALUES (%s, %s, %s, '2026-01-01')
                    ON CONFLICT (corpus_version, cluster_id) DO UPDATE
                    SET case_name=EXCLUDED.case_name""", [version_name, fixture_cluster_id, 'Fixture ' + suffix])
                cluster_id = fixture_cluster_id
                cur.execute("""INSERT INTO authority_case_opinions
                    (corpus_version, opinion_id, cluster_id, source_url, plain_text)
                    VALUES (%s, %s, %s, 'https://example.test/case', %s)
                    ON CONFLICT (corpus_version, opinion_id) DO UPDATE
                    SET cluster_id=EXCLUDED.cluster_id, plain_text=EXCLUDED.plain_text""", [version_name, cluster_id, cluster_id, suffix + ' case'])
                cur.execute("""INSERT INTO authority_case_chunks
                    (corpus_version, opinion_id, cluster_id, chunk_index, content)
                    VALUES (%s, %s, %s, 0, %s)
                    ON CONFLICT (corpus_version, opinion_id, chunk_index) DO UPDATE
                    SET content=EXCLUDED.content""", [version_name, cluster_id, cluster_id, suffix + ' case authority'])
                cur.execute("""INSERT INTO authority_case_clusters
                    (corpus_version, cluster_id, case_name, date_filed)
                    VALUES (%s, %s, %s, '2026-01-01')
                    ON CONFLICT (corpus_version, cluster_id) DO UPDATE
                    SET case_name=EXCLUDED.case_name""", [version_name, second_cluster_id, 'Fixture second ' + suffix])
                cur.execute("""INSERT INTO authority_case_opinions
                    (corpus_version, opinion_id, cluster_id, source_url, plain_text)
                    VALUES (%s, %s, %s, 'https://example.test/second-case', %s)
                    ON CONFLICT (corpus_version, opinion_id) DO UPDATE
                    SET cluster_id=EXCLUDED.cluster_id, plain_text=EXCLUDED.plain_text""", [version_name, second_cluster_id, second_cluster_id, suffix + ' second case'])
                cur.execute("""INSERT INTO authority_case_chunks
                    (corpus_version, opinion_id, cluster_id, chunk_index, content)
                    VALUES (%s, %s, %s, 0, %s)
                    ON CONFLICT (corpus_version, opinion_id, chunk_index) DO UPDATE
                    SET cluster_id=EXCLUDED.cluster_id, content=EXCLUDED.content""", [version_name, second_cluster_id, second_cluster_id, suffix + ' second case authority'])
        stage_corpus_version(
            conn, version=version, manifest_hash="fixture-manifest-hash",
            as_of="2026-08-30T00:00:00Z", actor="rehearsal-admin",
            reason="disposable production-shaped rehearsal",
            embedding_model="mixedbread-ai/mxbai-embed-large-v1",
            embedding_version="1", embedding_dimension=1024,
        )
        add_fixture(version, 'old')
        create_snapshot_chunks(conn, version)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(DISTINCT cluster_id) FROM authority_case_chunks WHERE corpus_version=%s", [version])
            assert cur.fetchone()[0] == 2
        for kind, records in (
            ("release", [{"ready": True}]),
            ("completeness", [{"expected": True, "observed": True}]),
            ("freshness", [{"lag_seconds": 1}]),
            ("isolation", [{"namespace": "public-authority", "private": False}]),
        ):
            result = sampled_audit(records, audit_kind=kind)
            assert result["passed"]
            record_audit(
                conn, corpus_version=version, audit_kind=kind,
                methodology="fixture sample with computed threshold result",
                thresholds={"minimum_completeness": 0.95, "maximum_lag_seconds": 172800},
                result=result, passed=True, auditor="rehearsal-admin",
            )
        with pytest.raises(ValueError):
            record_audit(conn, corpus_version=version, audit_kind="release",
                         methodology="negative mismatch", thresholds={},
                         result={"passed": False}, passed=True, auditor="rehearsal-admin")
        promote_corpus_version(conn, version=version, actor="rehearsal-admin", reason="all fixture audits passed")
        authority_results = CourtListenerRepository(conn).search_legal_authorities('old authority')
        case_results = CourtListenerRepository(conn).search_caselaw('old case')
        # Execute the operator coverage projection against the versioned
        # snapshot schema; this guards against legacy ``id`` assumptions.
        assert isinstance(CourtListenerRepository(conn).court_coverage(), list)
        assert authority_results and authority_results[0]['title'] == 'Fixture old'
        assert case_results and case_results[0]['case_name'] == 'Fixture old'
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM authority_corpus_versions WHERE version=%s", [version])
            assert cur.fetchone()[0] == "promoted"
        # A staged follow-up captures usable rollback lineage and restores the
        # prior good version without changing tenant/private tables.
        follow_up = version + "-next"
        stage_corpus_version(
            conn, version=follow_up, manifest_hash="fixture-manifest-hash-next",
            as_of="2026-08-30T00:00:00Z", actor="rehearsal-admin",
            reason="rollback fixture", embedding_model="mixedbread-ai/mxbai-embed-large-v1",
            embedding_version="1", embedding_dimension=1024,
        )
        add_fixture(follow_up, 'new')
        for kind in ("release", "completeness", "freshness", "isolation"):
            result = sampled_audit([{"ready": True}] if kind == "release" else
                                   ([{"expected": True, "observed": True}] if kind == "completeness" else
                                    ([{"lag_seconds": 1}] if kind == "freshness" else
                                     [{"namespace": "public-authority", "private": False}])),
                                   audit_kind=kind)
            record_audit(conn, corpus_version=follow_up, audit_kind=kind,
                         methodology="fixture rollback sample", thresholds={}, result=result,
                         passed=True, auditor="rehearsal-admin")
        promote_corpus_version(conn, version=follow_up, actor="rehearsal-admin", reason="cutover fixture")
        assert CourtListenerRepository(conn).search_legal_authorities('new authority')[0]['title'] == 'Fixture new'
        assert CourtListenerRepository(conn).search_caselaw('new case')[0]['case_name'] == 'Fixture new'
        rollback_corpus_version(conn, version=follow_up, actor="rehearsal-admin", reason="bad release fixture")
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM authority_corpus_versions WHERE status='promoted'")
            assert cur.fetchone()[0] == version
        assert CourtListenerRepository(conn).search_legal_authorities('old authority')[0]['title'] == 'Fixture old'
        assert CourtListenerRepository(conn).search_caselaw('old case')[0]['case_name'] == 'Fixture old'
