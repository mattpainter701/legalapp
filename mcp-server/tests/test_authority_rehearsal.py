"""Opt-in database rehearsal for the authority release lifecycle.

CI environments with a disposable PostgreSQL/pgvector database can run this
with AUTHORITY_REHEARSAL_DATABASE_URL.  It is deliberately skipped by normal
unit runs so local tests never touch a developer or production database.
"""

import os
import uuid
import bz2
import csv
import threading
import base64
import hashlib
import hmac
import json
import time
from urllib.parse import quote, urlsplit, urlunsplit
from pathlib import Path
from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from psycopg2 import DatabaseError

from mcp_server.control_plane import (
    authorize_citator_reviewer,
    claim_embedding_shard,
    enqueue_citator_alert,
    finish_embedding_shard,
    heartbeat_embedding_shard,
    promote_corpus_version,
    record_audit,
    record_citator_alert_delivery,
    record_treatment_assessment,
    revoke_citator_watch,
    review_treatment_assessment,
    rollback_corpus_version,
    run_and_record_control_audit,
    save_citator_watch,
    sampled_audit,
    stage_corpus_version,
)
from mcp_server.database import connect
from mcp_server.loader import (
    backfill_promoted_caselaw_snapshot,
    create_chunks,
    create_snapshot_chunks,
    init_schema,
    load_staged_core,
    materialize_citator_facts,
    refresh_courtlistener_coverage_ledger,
)
from mcp_server.authority_ingest import FetchedDocument, ingest_document
import mcp_server.authority_ingest as authority_ingest_module
from mcp_server.authority_adapter_store import AdapterDocument, upsert_adapter_document
from mcp_server.repository import CourtListenerRepository
from mcp_server.server import ControlPlaneRequest, run_control_audit
from mcp_server.source_catalog import admit_public_source
from mcp_server.sync import PostgresSyncStore
import mcp_server.server as authority_server
from mcp_server.jetson_worker import process_once
from mcp_server.worker_config import WorkerConfig


def _declare_complete_partition(
    conn,
    *,
    version: str,
    source_key: str,
    partition_key: str,
    expected: int,
    observed: int,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO corpus_coverage_ledger
                 (source_key, partition_key, expected_item_count,
                  acquisition_state, source_release, rows_loaded,
                  chunks_loaded, vectors_loaded, last_checked_at)
               VALUES (%s, %s, %s, 'complete', %s, %s, %s, %s, now())
               ON CONFLICT (source_key, partition_key, source_release) DO UPDATE SET
                 expected_item_count=EXCLUDED.expected_item_count,
                 acquisition_state='complete', rows_loaded=EXCLUDED.rows_loaded,
                 chunks_loaded=EXCLUDED.chunks_loaded,
                 vectors_loaded=EXCLUDED.vectors_loaded, last_checked_at=now()""",
            [
                source_key,
                partition_key,
                expected,
                version,
                observed,
                observed,
                observed,
            ],
        )
    conn.commit()


def _run_production_audits(conn, *, version: str, auditor: str) -> None:
    for audit_kind in ("release", "completeness", "freshness", "isolation"):
        result, immutable_hash = run_and_record_control_audit(
            conn,
            version=version,
            audit_kind=audit_kind,
            auditor=auditor,
        )
        assert result["passed"] is True, (audit_kind, result)
        assert result["state_digest"]
        assert immutable_hash


def test_authority_release_rehearsal(monkeypatch, tmp_path: Path):
    db_url = os.getenv("AUTHORITY_REHEARSAL_DATABASE_URL")
    if not db_url:
        pytest.skip(
            "set AUTHORITY_REHEARSAL_DATABASE_URL for the disposable DB rehearsal"
        )

    init_schema(db_url)
    version = "rehearsal-authority-" + uuid.uuid4().hex
    with connect(db_url) as conn:

        def db_reject(sql, params=()):
            """Assert a database constraint rejects one mutation without poisoning the test transaction."""
            with conn.cursor() as cur:
                cur.execute("SAVEPOINT rehearsal_negative")
                try:
                    cur.execute(sql, params)
                except DatabaseError:
                    cur.execute("ROLLBACK TO SAVEPOINT rehearsal_negative")
                    cur.execute("RELEASE SAVEPOINT rehearsal_negative")
                    return
                cur.execute("ROLLBACK TO SAVEPOINT rehearsal_negative")
                cur.execute("RELEASE SAVEPOINT rehearsal_negative")
            raise AssertionError("expected PostgreSQL constraint rejection")

        source_key = "rehearsal:source:" + version
        fixture_cluster_id = 97000001
        second_cluster_id = 97000002

        def add_fixture(version_name, suffix):
            catalog_schema = "rehearsal-v2" if suffix == "new" else "rehearsal"
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO courts (court_id, full_name)
                       VALUES ('ohio', 'Ohio rehearsal court')
                       ON CONFLICT (court_id) DO NOTHING"""
                )
                cur.execute(
                    """INSERT INTO legal_sources
                    (source_key, publisher, source_type, canonical_url, enabled,
                     storage_policy, rights_decision, reviewed_at, reviewed_by,
                     expected_cadence, claim_safe_wording, metadata)
                    VALUES (%s, 'Rehearsal', 'statute', 'https://example.test', TRUE,
                            'normalized_text', 'official', now(), 'rehearsal-admin',
                            'daily', 'Fixture source only',
                            '{"catalog_schema_version":"rehearsal","implementation_status":"fixture","manifest_reference":"fixture-manifest"}')
                    ON CONFLICT DO NOTHING""",
                    [source_key],
                )
                cur.execute(
                    """INSERT INTO legal_sources
                    (source_key, publisher, source_type, canonical_url, enabled,
                     storage_policy, rights_decision, reviewed_at, reviewed_by,
                     expected_cadence, claim_safe_wording, metadata)
                    VALUES ('courtlistener:ohio-caselaw', 'Rehearsal', 'case_law',
                            'https://example.test/caselaw', TRUE, 'normalized_text',
                            'official', now(), 'rehearsal-admin', 'daily',
                            'Fixture caselaw source only',
                            '{"catalog_schema_version":"rehearsal","implementation_status":"fixture","manifest_reference":"fixture-manifest"}')
                    ON CONFLICT DO NOTHING"""
                )
                cur.execute(
                    """UPDATE legal_sources
                       SET public_namespace='public-authority'
                     WHERE source_key IN (%s, 'courtlistener:ohio-caselaw')""",
                    [source_key],
                )
                cur.execute(
                    """INSERT INTO citator_public_source_admissions
                         (source_key, catalog_schema_version, manifest_reference,
                          manifest_sha256, reviewed_at, reviewed_by)
                       SELECT %s, %s, %s, manifest_hash,
                                now(), 'rehearsal-admin'
                         FROM authority_corpus_versions WHERE version=%s
                       ON CONFLICT (source_key, manifest_sha256) DO UPDATE SET active=TRUE,
                         manifest_sha256=EXCLUDED.manifest_sha256""",
                    [
                        source_key,
                        catalog_schema,
                        f"fixture-manifest:{version_name}",
                        version_name,
                    ],
                )
                cur.execute(
                    """INSERT INTO citator_public_source_admissions
                         (source_key, catalog_schema_version, manifest_reference,
                          manifest_sha256, reviewed_at, reviewed_by)
                       SELECT 'courtlistener:ohio-caselaw', %s,
                              %s, manifest_hash, now(),
                              'rehearsal-admin'
                         FROM authority_corpus_versions WHERE version=%s
                       ON CONFLICT (source_key, manifest_sha256) DO UPDATE SET
                         active=TRUE,
                         catalog_schema_version=EXCLUDED.catalog_schema_version,
                         manifest_reference=EXCLUDED.manifest_reference,
                         manifest_sha256=EXCLUDED.manifest_sha256""",
                    [
                        catalog_schema,
                        f"fixture-manifest:{version_name}",
                        version_name,
                    ],
                )
                cur.execute(
                    """UPDATE legal_documents
                          SET public_namespace='public-authority'
                        WHERE corpus_version=%s
                          AND source_key IN (%s, 'courtlistener:ohio-caselaw')""",
                    [version_name, source_key],
                )
                cur.execute(
                    """UPDATE authority_case_clusters
                          SET public_namespace='public-authority'
                        WHERE corpus_version=%s
                          AND source_key IN (%s, 'courtlistener:ohio-caselaw')""",
                    [version_name, source_key],
                )
                cur.execute(
                    """INSERT INTO legal_documents
                    (source_key, external_id, document_type, title, authority_tier,
                     canonical_url, corpus_version, text_content, metadata)
                    VALUES (%s, 'same-document', 'statute', %s, 'binding_primary',
                            'https://example.test/doc', %s, %s,
                            '{"namespace":"public-authority"}') RETURNING id""",
                    [
                        source_key,
                        "Fixture " + suffix,
                        version_name,
                        suffix + " authority",
                    ],
                )
                document_id = cur.fetchone()[0]
                cur.execute(
                    """INSERT INTO legal_document_chunks
                    (document_id, chunk_index, content, content_hash, corpus_version)
                    VALUES (%s, 0, %s, md5(%s), %s)""",
                    [
                        document_id,
                        suffix + " authority",
                        suffix + " authority",
                        version_name,
                    ],
                )
                cur.execute(
                    """INSERT INTO authority_case_clusters
                    (corpus_version, cluster_id, case_name, date_filed)
                    VALUES (%s, %s, %s, '2026-01-01')
                    ON CONFLICT (corpus_version, cluster_id) DO UPDATE
                    SET case_name=EXCLUDED.case_name""",
                    [version_name, fixture_cluster_id, "Fixture " + suffix],
                )
                cluster_id = fixture_cluster_id
                cur.execute(
                    """INSERT INTO authority_case_opinions
                    (corpus_version, opinion_id, cluster_id, source_url, plain_text)
                    VALUES (%s, %s, %s, 'https://example.test/case', %s)
                    ON CONFLICT (corpus_version, opinion_id) DO UPDATE
                    SET cluster_id=EXCLUDED.cluster_id, plain_text=EXCLUDED.plain_text""",
                    [version_name, cluster_id, cluster_id, suffix + " case"],
                )
                cur.execute(
                    """INSERT INTO authority_case_chunks
                    (corpus_version, opinion_id, cluster_id, court_id, chunk_index, content)
                    VALUES (%s, %s, %s, 'ohio', 0, %s)
                    ON CONFLICT (corpus_version, opinion_id, chunk_index) DO UPDATE
                    SET content=EXCLUDED.content""",
                    [version_name, cluster_id, cluster_id, suffix + " case authority"],
                )
                cur.execute(
                    """INSERT INTO authority_case_clusters
                    (corpus_version, cluster_id, case_name, date_filed)
                    VALUES (%s, %s, %s, '2026-01-01')
                    ON CONFLICT (corpus_version, cluster_id) DO UPDATE
                    SET case_name=EXCLUDED.case_name""",
                    [version_name, second_cluster_id, "Fixture second " + suffix],
                )
                cur.execute(
                    """INSERT INTO authority_case_opinions
                    (corpus_version, opinion_id, cluster_id, source_url, plain_text)
                    VALUES (%s, %s, %s, 'https://example.test/second-case', %s)
                    ON CONFLICT (corpus_version, opinion_id) DO UPDATE
                    SET cluster_id=EXCLUDED.cluster_id, plain_text=EXCLUDED.plain_text""",
                    [
                        version_name,
                        second_cluster_id,
                        second_cluster_id,
                        suffix + " second case",
                    ],
                )
                cur.execute(
                    """INSERT INTO authority_case_chunks
                    (corpus_version, opinion_id, cluster_id, court_id, chunk_index, content)
                    VALUES (%s, %s, %s, 'ohio', 0, %s)
                    ON CONFLICT (corpus_version, opinion_id, chunk_index) DO UPDATE
                    SET cluster_id=EXCLUDED.cluster_id, content=EXCLUDED.content""",
                    [
                        version_name,
                        second_cluster_id,
                        second_cluster_id,
                        suffix + " second case authority",
                    ],
                )
                cur.execute(
                    """INSERT INTO authority_case_citations
                    (corpus_version, citing_opinion_id, cited_opinion_id,
                     cited_cluster_id, cited_reporter, cited_volume, cited_page)
                    VALUES (%s, %s, %s, %s, 'Rehearsal', '1', '1')
                    ON CONFLICT DO NOTHING""",
                    [
                        version_name,
                        fixture_cluster_id,
                        second_cluster_id,
                        second_cluster_id,
                    ],
                )
            _declare_complete_partition(
                conn,
                version=version_name,
                source_key=source_key,
                partition_key=f"manifest:{source_key}",
                expected=1,
                observed=1,
            )
            _declare_complete_partition(
                conn,
                version=version_name,
                source_key="courtlistener:ohio-caselaw",
                partition_key="ohio",
                expected=2,
                observed=2,
            )

        stage_corpus_version(
            conn,
            version=version,
            manifest_hash="fixture-manifest-hash",
            as_of="2026-08-30T00:00:00Z",
            actor="rehearsal-admin",
            reason="disposable production-shaped rehearsal",
            embedding_model="mixedbread-ai/mxbai-embed-large-v1",
            embedding_version="1",
            embedding_dimension=1024,
            inherit_current=False,
        )
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM authority_citation_facts WHERE corpus_version=%s",
                [version],
            )
            cur.execute(
                "DELETE FROM authority_history_facts WHERE corpus_version=%s", [version]
            )
            cur.execute(
                "DELETE FROM authority_records WHERE corpus_version=%s", [version]
            )
            cur.execute(
                "DELETE FROM authority_case_citations WHERE corpus_version=%s",
                [version],
            )
            cur.execute(
                "DELETE FROM authority_case_chunks WHERE corpus_version=%s", [version]
            )
            cur.execute(
                "DELETE FROM authority_case_opinions WHERE corpus_version=%s", [version]
            )
            cur.execute(
                "DELETE FROM authority_case_clusters WHERE corpus_version=%s", [version]
            )
        conn.commit()
        # Production bulk loading now fails before touching even global court
        # metadata unless the selected candidate and current reviewed source
        # resolve through the exact public-lineage view.
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO legal_sources
                (source_key, publisher, source_type, canonical_url, enabled,
                 storage_policy, rights_decision, reviewed_at, reviewed_by,
                 expected_cadence, claim_safe_wording, public_namespace, metadata)
                VALUES ('courtlistener:ohio-caselaw', 'Rehearsal', 'case_law',
                        'https://example.test/caselaw', TRUE, 'normalized_text',
                        'official', now(), 'rehearsal-admin', 'daily',
                        'Fixture caselaw source only', 'public-authority',
                        '{"catalog_schema_version":"rehearsal","implementation_status":"fixture","manifest_reference":"fixture-manifest"}')
                ON CONFLICT (source_key) DO UPDATE SET enabled=TRUE,
                  storage_policy='normalized_text', rights_decision='official',
                  reviewed_at=now(), reviewed_by='rehearsal-admin',
                  public_namespace='public-authority',
                  metadata=EXCLUDED.metadata"""
            )
            cur.execute(
                """INSERT INTO citator_public_source_admissions
                     (source_key, catalog_schema_version, manifest_reference,
                      manifest_sha256, reviewed_at, reviewed_by)
                   SELECT 'courtlistener:ohio-caselaw', 'rehearsal',
                          'fixture-manifest', manifest_hash, now(),
                          'rehearsal-admin'
                     FROM authority_corpus_versions WHERE version=%s
                   ON CONFLICT (source_key, manifest_sha256) DO UPDATE SET active=TRUE,
                     catalog_schema_version=EXCLUDED.catalog_schema_version,
                     manifest_reference=EXCLUDED.manifest_reference,
                     manifest_sha256=EXCLUDED.manifest_sha256""",
                [version],
            )
            cur.execute(
                """UPDATE authority_case_clusters
                      SET public_namespace='public-authority'
                    WHERE corpus_version=%s
                      AND source_key='courtlistener:ohio-caselaw'""",
                [version],
            )
        conn.commit()
        bulk_dir = tmp_path / "bulk"
        bulk_dir.mkdir()

        def write_bulk(name, headers, rows):
            with bz2.open(
                bulk_dir / name, "wt", encoding="utf-8", newline=""
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=headers)
                writer.writeheader()
                writer.writerows(rows)

        write_bulk(
            "courts-rehearsal.csv.bz2",
            ["id", "short_name", "full_name", "jurisdiction"],
            [
                {
                    "id": "rehearsal-ohio",
                    "short_name": "RO",
                    "full_name": "Rehearsal Ohio",
                    "jurisdiction": "US-OH",
                }
            ],
        )
        write_bulk(
            "dockets-rehearsal.csv.bz2",
            [
                "id",
                "court_id",
                "docket_number",
                "case_name",
                "date_filed",
                "date_terminated",
            ],
            [
                {
                    "id": "97100001",
                    "court_id": "rehearsal-ohio",
                    "docket_number": "R-1",
                    "case_name": "Bulk fixture",
                    "date_filed": "2026-01-01",
                    "date_terminated": "",
                }
            ],
        )
        write_bulk(
            "opinion-clusters-rehearsal.csv.bz2",
            [
                "id",
                "docket_id",
                "case_name",
                "date_filed",
                "precedential_status",
                "citations",
            ],
            [
                {
                    "id": "97100001",
                    "docket_id": "97100001",
                    "case_name": "Bulk fixture",
                    "date_filed": "2026-01-01",
                    "precedential_status": "Published",
                    "citations": "[]",
                }
            ],
        )
        write_bulk(
            "opinions-rehearsal.csv.bz2",
            [
                "id",
                "cluster_id",
                "type",
                "author_id",
                "html_with_citations",
                "plain_text",
                "sha1",
                "download_url",
            ],
            [
                {
                    "id": "97100001",
                    "cluster_id": "97100001",
                    "type": "010combined",
                    "author_id": "97100001",
                    "html_with_citations": "",
                    "plain_text": "Bulk fixture opinion",
                    "sha1": "bulk-sha",
                    "download_url": "https://example.test/bulk",
                }
            ],
        )
        write_bulk(
            "citations-rehearsal.csv.bz2",
            ["cluster_id", "reporter", "volume", "page"],
            [
                {
                    "cluster_id": "97100001",
                    "reporter": "Bulk Reporter",
                    "volume": "1",
                    "page": "1",
                }
            ],
        )
        write_bulk(
            "citation-map-rehearsal.csv.bz2",
            ["citing_opinion_id", "cited_opinion_id", "depth"],
            [
                {
                    "citing_opinion_id": "97100001",
                    "cited_opinion_id": "97100001",
                    "depth": "0",
                }
            ],
        )
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE citator_public_source_admissions
                      SET manifest_sha256=repeat('f', 64)
                    WHERE source_key='courtlistener:ohio-caselaw'"""
            )
        conn.commit()
        with pytest.raises(
            PermissionError, match="current reviewed public-source lineage"
        ):
            load_staged_core(bulk_dir, db_url)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM courts WHERE court_id='rehearsal-ohio'")
            assert cur.fetchone()[0] == 0
            cur.execute(
                """UPDATE citator_public_source_admissions p
                      SET manifest_sha256=v.manifest_hash
                     FROM authority_corpus_versions v
                    WHERE p.source_key='courtlistener:ohio-caselaw'
                      AND v.version=%s""",
                [version],
            )
        conn.commit()
        # A malformed opinion row must fail before a candidate can be promoted.
        write_bulk(
            "opinions-rehearsal.csv.bz2",
            [
                "id",
                "cluster_id",
                "type",
                "author_id",
                "html_with_citations",
                "plain_text",
                "sha1",
                "download_url",
            ],
            [
                {
                    "id": "",
                    "cluster_id": "97100001",
                    "type": "010combined",
                    "author_id": "97100001",
                    "html_with_citations": "",
                    "plain_text": "broken",
                    "sha1": "bad",
                    "download_url": "",
                }
            ],
        )
        with pytest.raises(Exception):
            load_staged_core(bulk_dir, db_url)
        with pytest.raises((PermissionError, ValueError)):
            promote_corpus_version(
                conn,
                version=version,
                actor="rehearsal-admin",
                reason="partial bulk must not promote",
            )
        # The rejected promotion deliberately takes the release advisory lock.
        # End that failed transaction before the operator-style loader resumes
        # on its own connection, exactly as the API transaction boundary does.
        conn.rollback()
        write_bulk(
            "opinions-rehearsal.csv.bz2",
            [
                "id",
                "cluster_id",
                "type",
                "author_id",
                "html_with_citations",
                "plain_text",
                "sha1",
                "download_url",
            ],
            [
                {
                    "id": "97100001",
                    "cluster_id": "97100001",
                    "type": "010combined",
                    "author_id": "97100001",
                    "html_with_citations": "",
                    "plain_text": "Bulk fixture opinion",
                    "sha1": "bulk-sha",
                    "download_url": "https://example.test/bulk",
                }
            ],
        )
        bulk_counts = load_staged_core(bulk_dir, db_url)
        assert bulk_counts["courts"] == 1
        assert bulk_counts["dockets"] == 1
        assert bulk_counts["opinion_clusters"] == 1
        assert bulk_counts["opinions"] == 1
        assert bulk_counts["citations"] == 1
        assert bulk_counts["citation_map"] == 1
        created_bulk_chunks = create_chunks(db_url)
        assert created_bulk_chunks > 0
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM authority_case_chunks WHERE corpus_version=%s AND opinion_id=97100001",
                [version],
            )
            assert cur.fetchone()[0] > 0
        replay_counts = load_staged_core(bulk_dir, db_url)
        assert replay_counts == bulk_counts
        assert create_chunks(db_url) == 0
        # A release contract change invalidates an otherwise unchanged
        # candidate chunk so it cannot be promoted with an old vector.
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE authority_case_chunks
                   SET embedding=('[' || array_to_string(array_fill(0, ARRAY[1024]), ',') || ']')::vector,
                       embedding_model='fixture-model', embedding_version='1'
                 WHERE corpus_version=%s AND opinion_id=97100001 AND chunk_index=0""",
                [version],
            )
            cur.execute(
                """UPDATE authority_corpus_versions
                      SET embedding_model='fixture-model-v2', embedding_version='2'
                    WHERE version=%s""",
                [version],
            )
        conn.commit()
        assert create_chunks(db_url) > 0
        with conn.cursor() as cur:
            cur.execute(
                """SELECT embedding, embedding_model, embedding_version
                     FROM authority_case_chunks
                    WHERE corpus_version=%s AND opinion_id=97100001 AND chunk_index=0""",
                [version],
            )
            assert cur.fetchone() == (None, None, None)
        # A changed replay must invalidate the old vector contract while an
        # unchanged replay remains idempotent.  The production CSV path is
        # used here rather than updating snapshot rows directly.
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE authority_case_chunks
                   SET embedding=('[' || array_to_string(array_fill(0, ARRAY[1024]), ',') || ']')::vector,
                       embedding_model='fixture-model', embedding_version='1'
                 WHERE corpus_version=%s AND opinion_id=97100001 AND chunk_index=0""",
                [version],
            )
        conn.commit()
        write_bulk(
            "opinions-rehearsal.csv.bz2",
            [
                "id",
                "cluster_id",
                "type",
                "author_id",
                "html_with_citations",
                "plain_text",
                "sha1",
                "download_url",
            ],
            [
                {
                    "id": "97100001",
                    "cluster_id": "97100001",
                    "type": "010combined",
                    "author_id": "97100001",
                    "html_with_citations": "",
                    "plain_text": "Bulk fixture opinion changed",
                    "sha1": "bulk-sha-2",
                    "download_url": "https://example.test/bulk",
                }
            ],
        )
        assert load_staged_core(bulk_dir, db_url)["opinions"] == 1
        assert create_chunks(db_url) > 0
        with conn.cursor() as cur:
            cur.execute(
                """SELECT content, embedding, embedding_model, embedding_version
                   FROM authority_case_chunks
                  WHERE corpus_version=%s AND opinion_id=97100001 AND chunk_index=0""",
                [version],
            )
            changed_chunk = cur.fetchone()
        assert changed_chunk[0] == "Bulk fixture opinion changed"
        assert changed_chunk[1:] == (None, None, None)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT embedding_model, embedding_version, embedding_dimension
                     FROM authority_corpus_versions WHERE version=%s""",
                [version],
            )
            target_model, target_version, target_dimension = cur.fetchone()
        add_fixture(version, "old")
        custom_private_source = "custom-private:" + version
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO legal_sources
                   (source_key, publisher, source_type, canonical_url, enabled,
                    storage_policy, rights_decision, reviewed_at, reviewed_by,
                    public_namespace, claim_safe_wording, metadata)
                   VALUES (%s, 'Fixture', 'case_law', 'https://example.test/private', TRUE,
                           'normalized_text', 'official', now(), 'rehearsal-admin',
                           'public-authority', 'Spoofed public source claim',
                           '{"catalog_schema_version":"rehearsal",
                             "implementation_status":"fixture",
                             "manifest_reference":"fixture-manifest"}')""",
                [custom_private_source],
            )
        with pytest.raises(
            PermissionError,
            match="current reviewed public-source lineage",
        ):
            upsert_adapter_document(
                conn,
                AdapterDocument(
                    source_key=custom_private_source,
                    external_id="adapter-private",
                    document_type="statute",
                    title="Private adapter payload",
                    citation=None,
                    jurisdiction="US",
                    authority_tier="binding_primary",
                    canonical_url="https://example.test/private-adapter",
                    text="private adapter content must never be written",
                    metadata={"namespace": "public-authority"},
                ),
            )
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO legal_documents
                   (source_key, external_id, document_type, title, authority_tier,
                    canonical_url, corpus_version, public_namespace, text_content)
                   VALUES (%s, 'private-doc', 'statute', 'Private', 'binding_primary',
                           'https://example.test/private-doc', %s,
                           'public-authority', 'private')
                   RETURNING public_namespace""",
                [custom_private_source, version],
            )
            assert cur.fetchone()[0] == "unknown"
            cur.execute(
                """INSERT INTO authority_case_clusters
                   (corpus_version, source_key, public_namespace, cluster_id, case_name)
                   VALUES (%s, %s, 'public-authority', 97999997, 'Private case')
                   RETURNING public_namespace""",
                [version, custom_private_source],
            )
            assert cur.fetchone()[0] == "unknown"
            cur.execute(
                "DELETE FROM authority_case_clusters WHERE corpus_version=%s AND cluster_id=97999997",
                [version],
            )
            cur.execute(
                """INSERT INTO legal_documents
                   (source_key, external_id, document_type, title, authority_tier,
                    canonical_url, corpus_version, text_content, metadata)
                   VALUES (%s, 'spoof-doc', 'statute', 'Spoof', 'binding_primary',
                           'https://example.test/spoof', %s, 'spoof',
                           '{"namespace":"public-authority"}')
                RETURNING public_namespace""",
                [custom_private_source, version],
            )
            assert cur.fetchone()[0] == "unknown"
            cur.execute(
                """INSERT INTO authority_case_clusters
                     (corpus_version, source_key, cluster_id, case_name)
                   VALUES (%s, %s, 97999998, 'Unknown namespace case')
                   RETURNING public_namespace""",
                [version, custom_private_source],
            )
            assert cur.fetchone()[0] == "unknown"
            cur.execute(
                """DELETE FROM authority_case_clusters
                    WHERE corpus_version=%s AND cluster_id=97999998""",
                [version],
            )
            cur.execute(
                "DELETE FROM legal_documents WHERE source_key=%s AND corpus_version=%s",
                [custom_private_source, version],
            )
            cur.execute(
                "DELETE FROM legal_sources WHERE source_key=%s", [custom_private_source]
            )
        monkeypatch.setenv("AUTHORITY_INGEST_CORPUS_VERSION", version)
        ingest_input = {
            "source_key": source_key,
            "external_id": "production-ingest-document",
            "document_type": "statute",
            "title": "Fixture production ingest",
            "canonical_url": "https://example.test/ingest",
            "jurisdiction": "US",
            "authority_tier": "binding_primary",
            "parser_version": "fixture-parser",
            "practice_areas": ["rehearsal"],
            "parser": "fixture-parser",
            "official_status": "official",
            "acquisition_basis": "fixture-only",
            "coverage_notes": "bounded rehearsal input",
            "metadata": {
                "namespace": "custom-private:spoof",
                "public_namespace": "private",
                "manifest_reference": "attacker-controlled",
            },
        }
        lineage_probe = FetchedDocument(
            text="lineage probe",
            content_hash="lineage-probe-hash",
            media_type="text/html",
            retrieved_at=datetime.now(timezone.utc),
            source_modified_at=None,
            etag=None,
        )
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE citator_public_source_admissions
                      SET manifest_sha256=repeat('e', 64)
                    WHERE source_key=%s""",
                [source_key],
            )
            cur.execute(
                "UPDATE legal_sources SET public_namespace='public-authority' WHERE source_key=%s",
                [source_key],
            )
        conn.commit()
        with pytest.raises(
            PermissionError, match="reviewed public-authority admission"
        ):
            ingest_document(
                conn,
                {**ingest_input, "external_id": "lineage-rejected"},
                lineage_probe,
            )
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE citator_public_source_admissions p
                      SET manifest_sha256=v.manifest_hash
                     FROM authority_corpus_versions v
                    WHERE p.source_key=%s AND v.version=%s""",
                [source_key, version],
            )
            cur.execute(
                """SELECT COUNT(*) FROM legal_documents
                    WHERE source_key=%s AND external_id='lineage-rejected'""",
                [source_key],
            )
            assert cur.fetchone()[0] == 0
        conn.commit()
        malformed = FetchedDocument(
            text="/i255 " * 200,
            content_hash="bad",
            media_type="text/html",
            retrieved_at=datetime.now(timezone.utc),
            source_modified_at=None,
            etag=None,
        )
        with pytest.raises(RuntimeError, match="blocked_font_map"):
            ingest_document(
                conn, {**ingest_input, "external_id": "malformed"}, malformed
            )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM legal_documents WHERE external_id='malformed' AND corpus_version=%s",
                [version],
            )
            assert cur.fetchone()[0] == 0
        fetched = FetchedDocument(
            text="production fixture authority text",
            content_hash="production-hash",
            media_type="text/html",
            retrieved_at=datetime.now(timezone.utc),
            source_modified_at=datetime.now(timezone.utc),
            etag="fixture-etag",
            resolved_url="https://example.test/ingest",
        )
        ingest_result = ingest_document(conn, ingest_input, fetched)
        assert ingest_result["chunks_created"] > 0
        adapter_result = upsert_adapter_document(
            conn,
            AdapterDocument(
                source_key=source_key,
                external_id="production-adapter-document",
                document_type="regulation",
                title="Fixture production adapter",
                citation="Fixture Reg. 1",
                jurisdiction="US",
                authority_tier="binding_primary",
                canonical_url="https://example.test/adapter",
                text="production adapter authority text",
                metadata={
                    "namespace": "custom-private:spoof",
                    "manifest_reference": "attacker-controlled",
                },
            ),
        )
        assert adapter_result == {
            "external_id": "production-adapter-document",
            "corpus_version": version,
            "changed": True,
            "chunks_created": 1,
        }
        with conn.cursor() as cur:
            cur.execute(
                """SELECT source_key, corpus_version, content_hash, parser_version,
                                  metadata->>'namespace',
                                  metadata->>'manifest_reference', text_content
                     FROM legal_documents
                    WHERE external_id='production-ingest-document'"""
            )
            document_row = cur.fetchone()
            assert document_row == (
                source_key,
                version,
                "production-hash",
                "fixture-parser",
                "public-authority",
                None,
                "production fixture authority text",
            )
            cur.execute(
                """SELECT COUNT(*), COUNT(*) FILTER (WHERE embedding IS NULL),
                                  MIN(content)
                     FROM legal_document_chunks c
                     JOIN legal_documents d ON d.id=c.document_id
                    WHERE d.external_id='production-ingest-document'
                      AND c.corpus_version=%s""",
                [version],
            )
            chunk_row = cur.fetchone()
            assert chunk_row[0] == ingest_result["chunks_created"]
            assert chunk_row[1] == chunk_row[0]
            assert chunk_row[2] == "production fixture authority text"
            cur.execute(
                """SELECT public_namespace, metadata->>'namespace',
                          metadata->>'manifest_reference'
                     FROM legal_documents
                    WHERE source_key=%s
                      AND external_id='production-adapter-document'
                      AND corpus_version=%s""",
                [source_key, version],
            )
            assert cur.fetchone() == ("public-authority", "public-authority", None)
            cur.execute(
                """SELECT corpus_version, status
                     FROM authority_harvest_checkpoints
                    WHERE source_key=%s AND partition_key=%s""",
                [source_key, f"manifest:{source_key}"],
            )
            assert cur.fetchone() == (version, "complete")
            cur.execute(
                """UPDATE legal_documents
                      SET metadata=metadata ||
                        '{"namespace":"custom-private:stale",
                          "manifest_reference":"stale-private-manifest",
                          "private_query":"must-not-survive-refresh"}'::jsonb
                    WHERE source_key=%s
                      AND external_id='production-ingest-document'
                      AND corpus_version=%s""",
                [source_key, version],
            )
        # An idempotent refresh replaces, rather than merges, old protected or
        # private metadata.  Unchanged content therefore cannot retain a
        # pre-boundary spoofed namespace/query payload.
        refreshed = ingest_document(conn, ingest_input, fetched)
        assert refreshed["changed"] is False
        with conn.cursor() as cur:
            cur.execute(
                """SELECT metadata->>'namespace',
                          metadata->>'manifest_reference',
                          metadata->>'private_query'
                     FROM legal_documents
                    WHERE source_key=%s
                      AND external_id='production-ingest-document'
                      AND corpus_version=%s""",
                [source_key, version],
            )
            assert cur.fetchone() == ("public-authority", None, None)
        create_snapshot_chunks(conn, version)
        refresh_courtlistener_coverage_ledger(conn, source_release=version)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO corpus_coverage_ledger
                (source_key, partition_key, expected_item_count, acquisition_state,
                 source_release, rows_loaded, last_checked_at)
                VALUES (%s, %s, 3, 'complete', %s, 3, now())
                ON CONFLICT (source_key, partition_key, source_release) DO UPDATE
                SET expected_item_count=EXCLUDED.expected_item_count,
                    acquisition_state=EXCLUDED.acquisition_state,
                    rows_loaded=EXCLUDED.rows_loaded,
                    last_checked_at=EXCLUDED.last_checked_at""",
                [source_key, f"manifest:{source_key}", version],
            )
            cur.execute(
                """SELECT DISTINCT cluster_id, court_id
                     FROM authority_case_chunks
                    WHERE corpus_version=%s
                    ORDER BY cluster_id""",
                [version],
            )
            assert set(cur.fetchall()) == {
                (97000001, "ohio"),
                (97000002, "ohio"),
                (97100001, "rehearsal-ohio"),
            }
            cur.execute(
                """SELECT citing_opinion_id, cited_opinion_id, cited_cluster_id
                     FROM authority_case_citations
                    WHERE corpus_version=%s
                    ORDER BY citing_opinion_id, cited_opinion_id NULLS LAST""",
                [version],
            )
            assert cur.fetchall() == [
                (97000001, 97000002, 97000002),
                (97100001, 97100001, None),
            ]
        conn.commit()
        import mcp_server.server as control_server

        monkeypatch.setattr(control_server, "connect", lambda _db=None: connect(db_url))
        unknown_audit = run_control_audit(
            ControlPlaneRequest(
                version=version,
                reason="production-path unknown expectation negative",
                audit_kind="completeness",
            ),
            actor="rehearsal-admin",
        )
        assert unknown_audit["audit"]["passed"] is False
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE corpus_coverage_ledger
                   SET expected_item_count=2
                 WHERE source_key='courtlistener:ohio-caselaw'
                   AND partition_key='ohio' AND source_release=%s""",
                [version],
            )
            cur.execute(
                """UPDATE corpus_coverage_ledger
                   SET expected_item_count=1
                 WHERE source_key='courtlistener:ohio-caselaw'
                   AND partition_key='rehearsal-ohio' AND source_release=%s""",
                [version],
            )
        conn.commit()
        for kind in ("release", "completeness", "freshness", "isolation"):
            audit_response = run_control_audit(
                ControlPlaneRequest(
                    version=version,
                    reason="production-path rehearsal",
                    audit_kind=kind,
                ),
                actor="rehearsal-admin",
            )
            assert audit_response["audit"]["passed"] is True
        with pytest.raises(ValueError):
            record_audit(
                conn,
                corpus_version=version,
                audit_kind="release",
                methodology="negative mismatch",
                thresholds={},
                result={"passed": False},
                passed=True,
                auditor="rehearsal-admin",
            )
        with pytest.raises(PermissionError, match="complete matching embeddings"):
            promote_corpus_version(
                conn,
                version=version,
                actor="rehearsal-admin",
                reason="must reject unembedded candidate",
            )
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE legal_document_chunks c
                   SET embedding=('[' || array_to_string(array_fill(0, ARRAY[1024]), ',') || ']')::vector,
                       embedding_model=%s, embedding_version=%s
                 WHERE c.corpus_version=%s""",
                [target_model, target_version, version],
            )
            cur.execute(
                """UPDATE authority_case_chunks c
                   SET embedding=('[' || array_to_string(array_fill(0, ARRAY[1024]), ',') || ']')::vector,
                       embedding_model=%s, embedding_version=%s
                 WHERE c.corpus_version=%s""",
                [target_model, target_version, version],
            )
            cur.execute(
                """SELECT COUNT(*) FILTER (WHERE c.corpus_version IS DISTINCT FROM %s),
                           COUNT(*) FILTER (WHERE c.embedding IS NULL),
                           COUNT(*) FILTER (WHERE c.embedding_model IS DISTINCT FROM %s),
                           COUNT(*) FILTER (WHERE c.embedding_version::text IS DISTINCT FROM %s),
                           COUNT(*) FILTER (WHERE vector_dims(c.embedding) IS DISTINCT FROM %s)
                      FROM legal_document_chunks c
                      JOIN legal_documents d ON d.id=c.document_id
                     WHERE d.corpus_version=%s""",
                [version, target_model, target_version, target_dimension, version],
            )
            legal_contract_counts = cur.fetchone()
            cur.execute(
                """SELECT COUNT(*) FILTER (WHERE c.embedding IS NULL),
                           COUNT(*) FILTER (WHERE c.embedding_model IS DISTINCT FROM %s),
                           COUNT(*) FILTER (WHERE c.embedding_version IS DISTINCT FROM %s),
                           COUNT(*) FILTER (WHERE vector_dims(c.embedding) IS DISTINCT FROM %s)
                      FROM authority_case_chunks c
                     WHERE c.corpus_version=%s""",
                [target_model, target_version, target_dimension, version],
            )
            authority_contract_counts = cur.fetchone()
        assert legal_contract_counts == (0, 0, 0, 0, 0)
        assert authority_contract_counts == (0, 0, 0, 0)
        conn.commit()
        assert materialize_citator_facts(conn, version) > 0
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM authority_records WHERE corpus_version=%s",
                [version],
            )
            assert cur.fetchone()[0] >= 3
        conn.commit()
        _run_production_audits(conn, version=version, auditor="rehearsal-admin")
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE legal_document_chunks
                      SET metadata=metadata || '{"post_audit_mutation":true}'::jsonb
                    WHERE corpus_version=%s""",
                [version],
            )
        conn.commit()
        with pytest.raises(PermissionError, match="stale, fabricated, or invalid"):
            promote_corpus_version(
                conn,
                version=version,
                actor="rehearsal-admin",
                reason="post-audit staged mutation must invalidate evidence",
            )
        _run_production_audits(conn, version=version, auditor="rehearsal-admin")
        with conn.cursor() as cur:
            cur.execute(
                """SELECT embedding_model, embedding_version, embedding_dimension
                     FROM authority_corpus_versions WHERE version=%s""",
                [version],
            )
            target_model, target_version, target_dimension = cur.fetchone()
            cur.execute(
                """SELECT COUNT(*) FROM legal_document_chunks c
                     JOIN legal_documents d ON d.id=c.document_id
                    WHERE d.corpus_version=%s
                      AND (c.corpus_version IS DISTINCT FROM %s
                           OR c.embedding IS NULL
                           OR c.embedding_model IS DISTINCT FROM %s
                           OR c.embedding_version::text IS DISTINCT FROM %s
                           OR vector_dims(c.embedding) IS DISTINCT FROM %s)""",
                [version, version, target_model, target_version, target_dimension],
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                """SELECT COUNT(*) FROM authority_case_chunks
                    WHERE corpus_version=%s
                      AND (embedding IS NULL
                           OR embedding_model IS DISTINCT FROM %s
                           OR embedding_version IS DISTINCT FROM %s
                           OR vector_dims(embedding) IS DISTINCT FROM %s)""",
                [version, target_model, target_version, target_dimension],
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                """INSERT INTO legal_sources
                   (source_key, publisher, source_type, canonical_url, enabled,
                    storage_policy, rights_decision, reviewed_at, reviewed_by,
                    public_namespace, claim_safe_wording, metadata)
                   VALUES (%s, 'Private fixture', 'case_law',
                           'https://example.test/custom-private', TRUE,
                           'normalized_text', 'official', now(), 'rehearsal-admin',
                           'public-authority', 'Spoofed public source claim',
                           '{"catalog_schema_version":"rehearsal","implementation_status":"fixture","manifest_reference":"spoofed"}')""",
                [custom_private_source],
            )
            cur.execute(
                """INSERT INTO authority_case_clusters
                     (corpus_version, source_key, cluster_id, case_name, date_filed)
                   VALUES (%s, %s, 97999999, 'Custom private promoted case', '2026-01-01')
                   RETURNING public_namespace""",
                [version, custom_private_source],
            )
            assert cur.fetchone()[0] == "unknown"
            cur.execute(
                """INSERT INTO authority_case_opinions
                     (corpus_version, opinion_id, cluster_id, source_url, plain_text)
                   VALUES (%s, 97999999, 97999999,
                           'https://example.test/custom-private-case',
                           'custom private authority text')""",
                [version],
            )
            cur.execute(
                """INSERT INTO authority_case_chunks
                     (corpus_version, opinion_id, cluster_id, court_id, chunk_index,
                      content, embedding, embedding_model, embedding_version)
                   VALUES (%s, 97999999, 97999999, 'ohio', 0,
                           'custom private authority text',
                           ('[' || array_to_string(array_fill(0, ARRAY[1024]), ',') || ']')::vector,
                           %s, %s)""",
                [version, target_model, target_version],
            )
        conn.commit()
        with pytest.raises(PermissionError, match="non-public authority lineage"):
            promote_corpus_version(
                conn,
                version=version,
                actor="rehearsal-admin",
                reason="unknown caselaw lineage must not promote",
            )
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM authority_case_chunks WHERE corpus_version=%s AND opinion_id=97999999",
                [version],
            )
            cur.execute(
                "DELETE FROM authority_case_opinions WHERE corpus_version=%s AND opinion_id=97999999",
                [version],
            )
            cur.execute(
                "DELETE FROM authority_case_clusters WHERE corpus_version=%s AND cluster_id=97999999",
                [version],
            )
            cur.execute(
                "DELETE FROM legal_sources WHERE source_key=%s", [custom_private_source]
            )
        conn.commit()
        _run_production_audits(conn, version=version, auditor="rehearsal-admin")
        promote_corpus_version(
            conn,
            version=version,
            actor="rehearsal-admin",
            reason="all fixture audits and embeddings passed",
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM authority_records WHERE corpus_version=%s",
                [version],
            )
            promoted_fact_count = cur.fetchone()[0]
        with pytest.raises(PermissionError, match="staged/canary"):
            materialize_citator_facts(conn, version)
        conn.rollback()
        init_schema(db_url)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM authority_records WHERE corpus_version=%s",
                [version],
            )
            assert cur.fetchone()[0] == promoted_fact_count
        authority_results = CourtListenerRepository(conn).search_legal_authorities(
            "old authority"
        )
        case_results = CourtListenerRepository(conn).search_caselaw("old case")
        # Execute the operator coverage projection against the versioned
        # snapshot schema; this guards against legacy ``id`` assumptions.
        assert isinstance(CourtListenerRepository(conn).court_coverage(), list)
        assert authority_results and authority_results[0]["title"] == "Fixture old"
        assert case_results and case_results[0]["case_name"] == "Fixture old"

        # Required public-lineage mutation matrix.  Each mutation is made
        # after promotion and independently restored; public projections must
        # fail closed rather than continue serving stale admitted metadata.
        lineage_repo = CourtListenerRepository(conn)
        fixture_chunk_id = case_results[0]["chunk_id"]
        assert lineage_repo.case_details(opinion_id=97000001)
        assert lineage_repo.get_full_opinion(opinion_id=97000001)["full_text"]
        assert lineage_repo.find_similar_cases(chunk_id=fixture_chunk_id)
        assert lineage_repo.search_by_citation("1 Rehearsal 1")
        assert lineage_repo.normalize_citation("1 Rehearsal 1")["matches"]
        assert lineage_repo.validate_citation("1 Rehearsal 1")["is_known_locally"]
        assert lineage_repo.citation_network(97000001)["cited"]
        assert lineage_repo.citation_network(97000002)["citing"]
        assert lineage_repo.court_info("ohio")
        assert lineage_repo.court_coverage()
        assert lineage_repo.search_dockets("Bulk fixture")
        assert (
            lineage_repo.export_research_bundle(opinion_ids=[97000001])["case_count"]
            == 1
        )
        baseline_corpus_status = lineage_repo.corpus_status()
        assert baseline_corpus_status["chunks"] > 0
        assert baseline_corpus_status["legal_documents"] > 0
        assert {source_key, "courtlistener:ohio-caselaw"}.issubset(
            {row["source_key"] for row in lineage_repo.sync_status()["sources"]}
        )
        baseline_coverage = lineage_repo.authority_coverage()
        assert {source_key, "courtlistener:ohio-caselaw"}.issubset(
            {row["source_key"] for row in baseline_coverage["sources"]}
        )
        assert {
            (
                row["source_key"],
                row["catalog_schema_version"],
                row["manifest_reference"],
                row["manifest_sha256"],
            )
            for row in baseline_coverage["sources"]
        }.issuperset(
            {
                (
                    source_key,
                    "rehearsal",
                    f"fixture-manifest:{version}",
                    "fixture-manifest-hash",
                ),
                (
                    "courtlistener:ohio-caselaw",
                    "rehearsal",
                    f"fixture-manifest:{version}",
                    "fixture-manifest-hash",
                ),
            }
        )
        assert {source_key, "courtlistener:ohio-caselaw"}.issubset(
            {row["source_key"] for row in baseline_coverage["partitions"]}
        )
        private_telemetry_source = "custom-private-telemetry:" + version
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO legal_sources
                   (source_key, publisher, source_type, canonical_url, enabled,
                    storage_policy, rights_decision, reviewed_at, reviewed_by,
                    public_namespace, claim_safe_wording, metadata)
                   VALUES (%s, 'Private telemetry', 'case_law',
                           'https://private.example.test/secret', TRUE,
                           'normalized_text', 'official', now(), 'private-operator',
                           'public-authority', 'Spoofed public telemetry claim',
                           '{"catalog_schema_version":"private","implementation_status":"private","secret":"must-not-leak"}')""",
                [private_telemetry_source],
            )
            cur.execute(
                """INSERT INTO source_sync_states
                     (source_key, partition_key, status, last_error, metadata)
                   VALUES (%s, 'private-partition', 'failed', 'private failure text',
                           '{"private_query":"must-not-leak"}')""",
                [private_telemetry_source],
            )
            cur.execute(
                """INSERT INTO corpus_coverage_ledger
                     (source_key, partition_key, expected_item_count,
                      acquisition_state, source_release, rows_loaded, metadata)
                   VALUES (%s, 'private-partition', 1, 'complete', %s, 1,
                           '{"private_document":"must-not-leak"}')""",
                [private_telemetry_source, version],
            )
            cur.execute(
                """INSERT INTO ingest_runs (source, status, errors)
                   VALUES (%s, 'failed', '[{"private":"must-not-leak"}]'::jsonb)""",
                [private_telemetry_source],
            )
            cur.execute(
                """INSERT INTO ingest_runs
                     (source, source_key, corpus_version, status)
                   VALUES (%s, %s, %s, 'completed')""",
                [source_key, source_key, version],
            )
        conn.commit()
        baseline_sync = lineage_repo.sync_status()
        assert any(row["source"] == source_key for row in baseline_sync["ingest_runs"])
        assert all(
            row["errors"].get("redacted") is True
            for row in baseline_sync["ingest_runs"]
        )
        assert private_telemetry_source not in str(baseline_sync)
        assert private_telemetry_source not in str(lineage_repo.corpus_status())
        assert private_telemetry_source not in str(lineage_repo.authority_coverage())
        private_isolation = run_control_audit(
            ControlPlaneRequest(
                version=version,
                reason="private telemetry contamination must fail isolation",
                audit_kind="isolation",
            ),
            actor="rehearsal-admin",
        )
        assert private_isolation["audit"]["passed"] is False
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM corpus_coverage_ledger WHERE source_key=%s",
                [private_telemetry_source],
            )
            cur.execute(
                "DELETE FROM source_sync_states WHERE source_key=%s",
                [private_telemetry_source],
            )
            cur.execute(
                "DELETE FROM ingest_runs WHERE source=%s",
                [private_telemetry_source],
            )
            cur.execute(
                "DELETE FROM legal_sources WHERE source_key=%s",
                [private_telemetry_source],
            )
        conn.commit()
        recovered_isolation = run_control_audit(
            ControlPlaneRequest(
                version=version,
                reason="private telemetry cleanup restores isolation",
                audit_kind="isolation",
            ),
            actor="rehearsal-admin",
        )
        assert recovered_isolation["audit"]["passed"] is True

        def assert_public_surfaces_suppressed():
            assert lineage_repo.search_legal_authorities("old authority") == []
            assert lineage_repo.search_caselaw("old case") == []
            assert lineage_repo.case_details(opinion_id=97000001) is None
            assert lineage_repo.get_full_opinion(opinion_id=97000001) is None
            with pytest.raises(ValueError):
                lineage_repo.find_similar_cases(chunk_id=fixture_chunk_id)
            assert lineage_repo.search_by_citation("1 Rehearsal 1") == []
            assert lineage_repo.normalize_citation("1 Rehearsal 1")["matches"] == []
            assert (
                lineage_repo.validate_citation("1 Rehearsal 1")["is_known_locally"]
                is False
            )
            assert lineage_repo.citation_network(97000001) == {
                "cited": [],
                "citing": [],
            }
            assert lineage_repo.citation_network(97000002) == {
                "cited": [],
                "citing": [],
            }
            assert lineage_repo.court_info("ohio") is None
            assert lineage_repo.court_coverage() == []
            assert lineage_repo.search_dockets("Bulk fixture") == []
            suppressed_export = lineage_repo.export_research_bundle(
                opinion_ids=[97000001]
            )
            assert suppressed_export["case_count"] == 0
            assert suppressed_export["cases"] == []
            assert suppressed_export["citations"] == []
            coverage = lineage_repo.authority_coverage()
            assert coverage["available"] is False
            assert coverage["corpus_version"] is None
            assert coverage["claim_state"] == "suppressed"
            assert coverage["audits"] == []
            assert coverage["partitions"] == []
            assert all(
                row["source_key"] not in {source_key, "courtlistener:ohio-caselaw"}
                for row in coverage["sources"]
            )
            sync = lineage_repo.sync_status()
            assert sync["ingest_runs"] == []
            assert sync["embedded_chunks"] == 0
            assert sync["pending_chunks"] == 0
            assert sync["source_partitions"] == []
            assert sync["authority_coverage"]["available"] is False
            assert all(
                row["source_key"] not in {source_key, "courtlistener:ohio-caselaw"}
                for row in sync["sources"]
            )
            assert private_telemetry_source not in str(sync)
            suppressed_corpus_status = lineage_repo.corpus_status()
            for count_key in (
                "courts",
                "dockets",
                "clusters",
                "opinions",
                "chunks",
                "embedded_chunks",
                "legal_sources",
                "legal_documents",
                "legal_document_chunks",
                "embedded_legal_document_chunks",
            ):
                assert suppressed_corpus_status[count_key] == 0
            assert suppressed_corpus_status["first_date"] is None
            assert suppressed_corpus_status["last_date"] is None
            assert suppressed_corpus_status["coverage"] == []
            assert suppressed_corpus_status["coverage_ledger"] == []
            assert private_telemetry_source not in str(suppressed_corpus_status)
            for audit_kind in ("release", "completeness", "freshness", "isolation"):
                result = run_control_audit(
                    ControlPlaneRequest(
                        version=version,
                        reason=f"lineage mutation suppresses {audit_kind}",
                        audit_kind=audit_kind,
                    ),
                    actor="rehearsal-admin",
                )
                assert result["audit"]["passed"] is False

        def assert_public_surfaces_recovered():
            """Prove every mutation is independent, not masked by prior failures."""
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT source_key FROM public_authority_source_lineage
                        WHERE corpus_version=%s ORDER BY source_key""",
                    [version],
                )
                assert set(row[0] for row in cur.fetchall()) == {
                    source_key,
                    "courtlistener:ohio-caselaw",
                }
            assert lineage_repo.search_legal_authorities("old authority")
            assert lineage_repo.search_caselaw("old case")
            for audit_kind in ("release", "completeness", "freshness", "isolation"):
                result = run_control_audit(
                    ControlPlaneRequest(
                        version=version,
                        reason=f"lineage recovery passes {audit_kind}",
                        audit_kind=audit_kind,
                    ),
                    actor="rehearsal-admin",
                )
                assert result["audit"]["passed"] is True
            coverage = lineage_repo.authority_coverage()
            assert coverage["available"] is True
            assert coverage["claim_state"] == "supported"
            assert {source_key, "courtlistener:ohio-caselaw"}.issubset(
                {row["source_key"] for row in coverage["sources"]}
            )

        lineage_mutations = [
            ("enabled", "FALSE", "TRUE"),
            ("rights_decision", "'prohibited'", "'official'"),
            ("storage_policy", "'prohibited'", "'normalized_text'"),
            ("reviewed_at", "NULL", "now()"),
            ("reviewed_by", "NULL", "'fixture-reviewer'"),
            (
                "claim_safe_wording",
                "NULL",
                "'Fixture reviewed source only'",
            ),
            ("public_namespace", "'private'", "'public-authority'"),
        ]
        for column, bad_value, good_value in lineage_mutations:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE legal_sources SET {column}={bad_value} WHERE source_key IN (%s, 'courtlistener:ohio-caselaw')",
                    [source_key],
                )
            conn.commit()
            if column == "enabled":
                with pytest.raises(
                    PermissionError,
                    match="current reviewed public-authority admission",
                ):
                    PostgresSyncStore(conn).ensure_source(date(2025, 1, 1))
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT enabled FROM legal_sources
                            WHERE source_key='courtlistener:ohio-caselaw'"""
                    )
                    assert cur.fetchone() == (False,)
            assert_public_surfaces_suppressed()
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE legal_sources SET {column}={good_value} WHERE source_key IN (%s, 'courtlistener:ohio-caselaw')",
                    [source_key],
                )
            conn.commit()
            assert_public_surfaces_recovered()
        db_reject(
            "UPDATE citator_public_source_admissions SET namespace='private' WHERE source_key=%s",
            [source_key],
        )
        db_reject(
            "UPDATE citator_public_source_admissions SET manifest_reference='' WHERE source_key=%s",
            [source_key],
        )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE citator_public_source_admissions SET active=FALSE WHERE source_key IN (%s, 'courtlistener:ohio-caselaw')",
                [source_key],
            )
        conn.commit()
        assert_public_surfaces_suppressed()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE citator_public_source_admissions SET active=TRUE WHERE source_key IN (%s, 'courtlistener:ohio-caselaw')",
                [source_key],
            )
        conn.commit()
        assert_public_surfaces_recovered()
        db_reject(
            "UPDATE citator_public_source_admissions SET reviewed_at=NULL WHERE source_key=%s",
            [source_key],
        )
        db_reject(
            "UPDATE citator_public_source_admissions SET reviewed_by='' WHERE source_key=%s",
            [source_key],
        )
        db_reject(
            "UPDATE citator_public_source_admissions SET catalog_schema_version='changed' WHERE source_key IN (%s, 'courtlistener:ohio-caselaw')",
            [source_key],
        )
        assert_public_surfaces_recovered()
        db_reject(
            "UPDATE citator_public_source_admissions SET manifest_reference='changed-manifest' WHERE source_key IN (%s, 'courtlistener:ohio-caselaw')",
            [source_key],
        )
        assert_public_surfaces_recovered()
        # One mutable source-level reference cannot represent side-by-side
        # releases.  The reviewed admission row is authoritative instead, so
        # payload/global source metadata cannot revoke or redirect it.
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE legal_sources
                      SET metadata=jsonb_set(metadata, '{manifest_reference}',
                                            '"changed-source-manifest"'::jsonb)
                    WHERE source_key IN (%s, 'courtlistener:ohio-caselaw')""",
                [source_key],
            )
        conn.commit()
        assert_public_surfaces_recovered()
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE legal_sources
                      SET metadata=jsonb_set(metadata, '{manifest_reference}',
                                            '"fixture-manifest"'::jsonb)
                    WHERE source_key IN (%s, 'courtlistener:ohio-caselaw')""",
                [source_key],
            )
        conn.commit()
        assert_public_surfaces_recovered()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE legal_sources SET metadata=metadata || '{\"catalog_schema_version\":\"spoofed\"}'::jsonb WHERE source_key IN (%s, 'courtlistener:ohio-caselaw')",
                [source_key],
            )
        conn.commit()
        assert_public_surfaces_recovered()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE legal_sources SET metadata=metadata || '{\"catalog_schema_version\":\"rehearsal\"}'::jsonb WHERE source_key IN (%s, 'courtlistener:ohio-caselaw')",
                [source_key],
            )
        conn.commit()
        assert_public_surfaces_recovered()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE legal_sources SET metadata=metadata - 'implementation_status' WHERE source_key IN (%s, 'courtlistener:ohio-caselaw')",
                [source_key],
            )
        conn.commit()
        assert_public_surfaces_suppressed()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE legal_sources SET metadata=metadata || '{\"implementation_status\":\"fixture\"}'::jsonb WHERE source_key IN (%s, 'courtlistener:ohio-caselaw')",
                [source_key],
            )
        conn.commit()
        assert_public_surfaces_recovered()
        db_reject(
            "UPDATE citator_public_source_admissions SET manifest_sha256=repeat('c', 64) WHERE source_key IN (%s, 'courtlistener:ohio-caselaw')",
            [source_key],
        )
        assert_public_surfaces_recovered()

        # Scope/cadence/caveat fields remain live operator metadata, but every
        # customer claim is state-digested.  A change suppresses claims until
        # the production audits are recomputed for that exact state.
        audited_claim_mutations = [
            ("coverage_kind", "'complete'", "'bounded'"),
            ("coverage_start", "DATE '2025-01-01'", "NULL"),
            ("coverage_end", "DATE '2026-12-31'", "NULL"),
            ("jurisdiction", "'US-OH'", "NULL"),
            (
                "canonical_url",
                "'https://example.test/changed-source'",
                "'https://example.test'",
            ),
            ("display_name", "'Changed rehearsal source'", "NULL"),
            ("publisher", "'Changed publisher'", "'Rehearsal'"),
            (
                "geographic_scope",
                '\'["US-OH","US-FED"]\'::jsonb',
                "'[]'::jsonb",
            ),
            (
                "completeness_caveats",
                "'Reviewed fixture caveat changed after promotion'",
                "NULL",
            ),
            ("expected_cadence", "'weekly'", "'daily'"),
        ]
        for column, changed_value, original_value in audited_claim_mutations:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE legal_sources SET {column}={changed_value} WHERE source_key=%s",
                    [source_key],
                )
            conn.commit()
            assert lineage_repo.search_legal_authorities("old authority")
            assert lineage_repo.authority_coverage()["claim_state"] == "suppressed"
            _run_production_audits(
                conn, version=version, auditor="rehearsal-claim-metadata-reviewer"
            )
            assert lineage_repo.authority_coverage()["claim_state"] == "supported"
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE legal_sources SET {column}={original_value} WHERE source_key=%s",
                    [source_key],
                )
            conn.commit()
            assert lineage_repo.authority_coverage()["claim_state"] == "suppressed"
            _run_production_audits(
                conn, version=version, auditor="rehearsal-claim-metadata-reviewer"
            )
            assert lineage_repo.authority_coverage()["claim_state"] == "supported"

        # Startup migration is one-time. Singleton rows written after a normal
        # feature release exists can never be adopted into that promoted
        # snapshot by a later init/backfill call.
        legacy_cluster = 97000003
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO opinion_clusters
                (cluster_id, case_name, date_filed, corpus_version)
                VALUES (%s, 'Legacy upgrade case', '2025-01-01', %s)
                ON CONFLICT (cluster_id) DO UPDATE SET corpus_version=EXCLUDED.corpus_version""",
                [legacy_cluster, version],
            )
            cur.execute(
                """INSERT INTO opinions
                (opinion_id, cluster_id, plain_text, source_url)
                VALUES (%s, %s, 'Legacy upgrade text', 'https://example.test/legacy')
                ON CONFLICT (opinion_id) DO UPDATE SET cluster_id=EXCLUDED.cluster_id,
                    plain_text=EXCLUDED.plain_text""",
                [legacy_cluster, legacy_cluster],
            )
            cur.execute(
                """INSERT INTO opinion_chunks
                (opinion_id, cluster_id, chunk_index, content)
                VALUES (%s, %s, 0, 'Legacy upgrade searchable text')
                ON CONFLICT (opinion_id, chunk_index) DO UPDATE SET content=EXCLUDED.content,
                    cluster_id=EXCLUDED.cluster_id""",
                [legacy_cluster, legacy_cluster],
            )
            cur.execute(
                """INSERT INTO opinion_citations
                (citing_opinion_id, cited_opinion_id, cited_cluster_id,
                 cited_reporter, cited_volume, cited_page)
                VALUES (%s, %s, %s, 'Upgrade', '1', '1')""",
                [legacy_cluster, legacy_cluster, legacy_cluster],
            )
        before_backfill = backfill_promoted_caselaw_snapshot(conn)
        after_backfill = backfill_promoted_caselaw_snapshot(conn)
        assert before_backfill == 0 and after_backfill == 0
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM authority_case_clusters WHERE corpus_version IS NULL"
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT COUNT(*) FROM authority_case_opinions WHERE corpus_version IS NULL OR cluster_id IS NULL"
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT COUNT(*) FROM authority_case_chunks WHERE corpus_version IS NULL"
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT COUNT(*) FROM authority_case_citations WHERE corpus_version IS NULL"
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                """SELECT
                  (SELECT COUNT(*) FROM authority_case_clusters
                    WHERE corpus_version=%s AND cluster_id=%s),
                  (SELECT COUNT(*) FROM authority_case_opinions
                    WHERE corpus_version=%s AND opinion_id=%s),
                  (SELECT COUNT(*) FROM authority_case_chunks
                    WHERE corpus_version=%s AND opinion_id=%s),
                  (SELECT COUNT(*) FROM authority_case_citations
                    WHERE corpus_version=%s
                      AND (citing_opinion_id=%s OR cited_opinion_id=%s))""",
                [
                    version,
                    legacy_cluster,
                    version,
                    legacy_cluster,
                    version,
                    legacy_cluster,
                    version,
                    legacy_cluster,
                    legacy_cluster,
                ],
            )
            assert cur.fetchone() == (0, 0, 0, 0)

        # Composite snapshot relationships reject cross-version and missing
        # parents; this is stronger than an application orphan count.
        db_reject(
            """INSERT INTO authority_case_opinions
            (corpus_version, opinion_id, cluster_id, plain_text)
            VALUES (%s, 99000001, 99000099, 'orphan')""",
            [version],
        )
        db_reject(
            """INSERT INTO authority_case_chunks
            (corpus_version, opinion_id, cluster_id, chunk_index, content)
            VALUES (%s, 99000001, 99000099, 0, 'orphan')""",
            [version],
        )

        # Release rows are append-only and snapshots are immutable after
        # promotion.  Test all three mutation classes in the real database.
        db_reject(
            """INSERT INTO authority_case_clusters
            (corpus_version, cluster_id, case_name) VALUES (%s, 99000004, 'late')""",
            [version],
        )
        db_reject(
            """UPDATE authority_case_clusters SET case_name='tampered'
            WHERE corpus_version=%s AND cluster_id=%s""",
            [version, fixture_cluster_id],
        )
        db_reject(
            """DELETE FROM authority_case_clusters
            WHERE corpus_version=%s AND cluster_id=%s""",
            [version, fixture_cluster_id],
        )
        db_reject(
            """INSERT INTO legal_documents
                 (source_key, external_id, document_type, title, authority_tier,
                  canonical_url, corpus_version, text_content)
               VALUES (%s, 'post-promote-insert', 'statute', 'tampered',
                       'binding_primary', 'https://example.test/tampered', %s,
                       'tampered')""",
            [source_key, version],
        )
        db_reject(
            """UPDATE legal_documents SET title='tampered'
                 WHERE corpus_version=%s AND source_key=%s""",
            [version, source_key],
        )
        db_reject(
            """DELETE FROM legal_documents
                 WHERE corpus_version=%s AND source_key=%s""",
            [version, source_key],
        )
        db_reject(
            """UPDATE legal_document_chunks SET content='tampered'
                 WHERE corpus_version=%s""",
            [version],
        )
        db_reject(
            "DELETE FROM legal_document_chunks WHERE corpus_version=%s",
            [version],
        )
        db_reject("DELETE FROM authority_audits WHERE corpus_version=%s", [version])

        # An older pass cannot keep a release eligible after the latest result
        # fails.  Use a new staged candidate so the failure is not hidden by
        # the promoted snapshot guard.
        failed_version = version + "-failed"
        stage_corpus_version(
            conn,
            version=failed_version,
            manifest_hash="f" * 64,
            as_of="2026-08-30T00:00:00Z",
            actor="rehearsal-admin",
            reason="latest-failure gate",
            embedding_model="mixedbread-ai/mxbai-embed-large-v1",
            embedding_version="1",
            embedding_dimension=1024,
        )
        add_fixture(failed_version, "failed")
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE legal_document_chunks
                      SET embedding=('[' || array_to_string(array_fill(0, ARRAY[1024]), ',') || ']')::vector,
                          embedding_model='mixedbread-ai/mxbai-embed-large-v1',
                          embedding_version=1
                    WHERE corpus_version=%s""",
                [failed_version],
            )
            cur.execute(
                """UPDATE authority_case_chunks
                      SET embedding=('[' || array_to_string(array_fill(0, ARRAY[1024]), ',') || ']')::vector,
                          embedding_model='mixedbread-ai/mxbai-embed-large-v1',
                          embedding_version='1'
                    WHERE corpus_version=%s""",
                [failed_version],
            )
            cur.execute(
                "SELECT COUNT(*) FROM authority_case_chunks WHERE corpus_version=%s",
                [failed_version],
            )
            assert cur.fetchone()[0] >= 2
        conn.commit()
        for kind in ("release", "completeness", "freshness", "isolation"):
            result = sampled_audit(
                [
                    {
                        "ready": True,
                        "expected": 1,
                        "observed": 1,
                        "lag_seconds": 1,
                        "namespace": "public-authority",
                        "private": False,
                    }
                ],
                audit_kind=kind,
            )
            record_audit(
                conn,
                corpus_version=failed_version,
                audit_kind=kind,
                methodology="latest-result precedence",
                thresholds={},
                result=result,
                passed=True,
                auditor="rehearsal-admin",
            )
        failed = sampled_audit(
            [
                {
                    "ready": False,
                    "expected": 1,
                    "observed": 0,
                    "lag_seconds": 999999,
                    "namespace": "tenant:private",
                    "private": True,
                }
            ],
            audit_kind="isolation",
        )
        record_audit(
            conn,
            corpus_version=failed_version,
            audit_kind="isolation",
            methodology="latest-result precedence negative",
            thresholds={},
            result=failed,
            passed=False,
            auditor="rehearsal-admin",
        )
        with pytest.raises(PermissionError, match="stale, fabricated, or invalid"):
            promote_corpus_version(
                conn,
                version=failed_version,
                actor="rehearsal-admin",
                reason="must reject latest failed audit",
            )

        # Durable harvest state: a retryable checkpoint is due, while a
        # terminal dead letter has no retry timestamp and is counted once.
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO authority_harvest_checkpoints
                (source_key, partition_key, corpus_version, cursor_url, status,
                 retry_count, next_retry_at)
                VALUES (%s, 'rehearsal-retry', %s, 'cursor-1', 'retryable_failure', 2,
                        now() - interval '1 minute')
                ON CONFLICT (source_key, partition_key, corpus_version) DO UPDATE SET
                  cursor_url=EXCLUDED.cursor_url, status=EXCLUDED.status,
                  retry_count=EXCLUDED.retry_count, next_retry_at=EXCLUDED.next_retry_at""",
                [source_key, failed_version],
            )
            cur.execute(
                """INSERT INTO authority_harvest_checkpoints
                (source_key, partition_key, corpus_version, cursor_url, status,
                 retry_count, next_retry_at, dead_letter_at)
                VALUES (%s, 'rehearsal-dead', %s, 'cursor-dead', 'dead_letter', 3,
                        NULL, now())
                ON CONFLICT (source_key, partition_key, corpus_version) DO UPDATE SET
                  status='dead_letter', retry_count=3, next_retry_at=NULL,
                  dead_letter_at=now()""",
                [source_key, failed_version],
            )
            cur.execute(
                """SELECT status, next_retry_at FROM authority_harvest_checkpoints
                WHERE source_key=%s AND partition_key='rehearsal-dead' AND corpus_version=%s""",
                [source_key, failed_version],
            )
            dead_status, dead_retry = cur.fetchone()
            assert dead_status == "dead_letter" and dead_retry is None

        # Lease exclusion, heartbeat renewal, expiry reclaim, and terminal
        # failure are all exercised against the shard state machine.
        shard = "rehearsal-shard-" + uuid.uuid4().hex
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO authority_embedding_shards
                (shard_key, corpus_version, corpus_table, model, model_version, dimension)
                VALUES (%s, %s, 'authority_case_chunks',
                        'mixedbread-ai/mxbai-embed-large-v1', '1', 1024)""",
                [shard, failed_version],
            )
        assert claim_embedding_shard(
            conn, shard_key=shard, worker_id="worker-a", lease_seconds=30
        )
        assert not claim_embedding_shard(
            conn, shard_key=shard, worker_id="worker-b", lease_seconds=30
        )
        assert heartbeat_embedding_shard(
            conn, shard_key=shard, worker_id="worker-a", lease_seconds=30
        )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE authority_embedding_shards SET lease_expires_at=now() - interval '1 second' WHERE shard_key=%s",
                [shard],
            )
        assert not heartbeat_embedding_shard(
            conn, shard_key=shard, worker_id="worker-a", lease_seconds=30
        )
        with pytest.raises(PermissionError):
            finish_embedding_shard(
                conn,
                shard_key=shard,
                worker_id="worker-a",
                success=True,
            )
        assert claim_embedding_shard(
            conn, shard_key=shard, worker_id="worker-b", lease_seconds=30
        )
        assert not heartbeat_embedding_shard(
            conn, shard_key=shard, worker_id="worker-a", lease_seconds=30
        )
        with pytest.raises(PermissionError):
            finish_embedding_shard(
                conn,
                shard_key=shard,
                worker_id="worker-a",
                success=True,
            )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE authority_embedding_shards SET attempts=3 WHERE shard_key=%s",
                [shard],
            )
        finish_embedding_shard(
            conn,
            shard_key=shard,
            worker_id="worker-b",
            success=False,
            error="rehearsal failure",
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, dead_letter_reason FROM authority_embedding_shards WHERE shard_key=%s",
                [shard],
            )
            shard_status, shard_error = cur.fetchone()
            assert shard_status == "dead_letter" and shard_error
        assert not heartbeat_embedding_shard(
            conn, shard_key=shard, worker_id="worker-a", lease_seconds=30
        )
        with pytest.raises(PermissionError):
            finish_embedding_shard(
                conn,
                shard_key=shard,
                worker_id="worker-a",
                success=True,
            )

        # Private material is rejected at the public namespace boundary and
        # cannot pass the isolation audit as public authority evidence.
        from mcp_server.control_plane import public_namespace

        with pytest.raises(ValueError):
            public_namespace("tenant:private-matter", admitted=True)
        private_audit = sampled_audit(
            [{"namespace": "tenant:private-matter", "private": True}],
            audit_kind="isolation",
        )
        assert private_audit["passed"] is False
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM authority_corpus_versions WHERE version=%s",
                [version],
            )
            assert cur.fetchone()[0] == "promoted"
        # A staged follow-up captures usable rollback lineage and restores the
        # prior good version without changing tenant/private tables.
        follow_up = version + "-next"
        with conn.cursor() as cur:
            cur.execute(
                """SELECT embedding_model, embedding_version, embedding_dimension
                     FROM authority_corpus_versions WHERE version=%s""",
                [version],
            )
            follow_model, follow_model_version, follow_dimension = cur.fetchone()
        stage_corpus_version(
            conn,
            version=follow_up,
            manifest_hash="fixture-manifest-hash-next",
            as_of="2026-08-30T00:00:00Z",
            actor="rehearsal-admin",
            reason="rollback fixture",
            embedding_model=follow_model,
            embedding_version=str(follow_model_version),
            embedding_dimension=follow_dimension,
        )
        add_fixture(follow_up, "new")
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                """SELECT corpus_version, catalog_schema_version,
                          manifest_reference
                     FROM public_authority_source_lineage
                    WHERE source_key=%s AND corpus_version=ANY(%s)
                    ORDER BY corpus_version""",
                [source_key, [version, follow_up]],
            )
            assert set(cur.fetchall()) == {
                (version, "rehearsal", f"fixture-manifest:{version}"),
                (
                    follow_up,
                    "rehearsal-v2",
                    f"fixture-manifest:{follow_up}",
                ),
            }
        follow_config = WorkerConfig(
            worker_id=0,
            total_workers=1,
            batch_size=8,
            model=follow_model,
            model_version=str(follow_model_version),
            dim=follow_dimension,
            db_url=db_url,
        )

        class FollowupModel:
            def encode(self, texts, **_kwargs):
                return [[1.0] + [0.0] * (follow_dimension - 1) for _ in texts]

        monkeypatch.setenv("AUTHORITY_EMBEDDING_CORPUS_VERSION", follow_up)
        try:
            assert process_once(follow_config, FollowupModel()) > 0
        finally:
            monkeypatch.delenv("AUTHORITY_EMBEDDING_CORPUS_VERSION", raising=False)
        assert materialize_citator_facts(conn, follow_up) > 0
        conn.commit()
        for table in (
            "legal_documents",
            "legal_document_chunks",
            "authority_case_clusters",
            "authority_records",
        ):
            db_reject(
                f"UPDATE {table} SET corpus_version=%s WHERE corpus_version=%s",
                [version, follow_up],
            )
            db_reject(
                f"UPDATE {table} SET corpus_version=%s WHERE corpus_version=%s",
                [follow_up, version],
            )
        with conn.cursor() as cur:
            for table in (
                "legal_documents",
                "legal_document_chunks",
                "authority_case_clusters",
                "authority_records",
            ):
                cur.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE corpus_version=%s",
                    [follow_up],
                )
                assert cur.fetchone()[0] > 0
        _run_production_audits(conn, version=follow_up, auditor="rehearsal-admin")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT coverage_kind FROM legal_sources WHERE source_key=%s",
                [source_key],
            )
            original_coverage_kind = cur.fetchone()[0]
            changed_coverage_kind = (
                "bounded" if original_coverage_kind == "complete" else "complete"
            )
            cur.execute(
                "UPDATE legal_sources SET coverage_kind=%s WHERE source_key=%s",
                [changed_coverage_kind, source_key],
            )
        conn.commit()
        with pytest.raises(PermissionError, match="stale, fabricated, or invalid"):
            promote_corpus_version(
                conn,
                version=follow_up,
                actor="rehearsal-admin",
                reason="claim metadata changed after audit",
            )
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE legal_sources SET coverage_kind=%s WHERE source_key=%s",
                [original_coverage_kind, source_key],
            )
        conn.commit()
        _run_production_audits(conn, version=follow_up, auditor="rehearsal-admin")
        promote_corpus_version(
            conn, version=follow_up, actor="rehearsal-admin", reason="cutover fixture"
        )
        assert (
            CourtListenerRepository(conn).search_legal_authorities("new authority")[0][
                "title"
            ]
            == "Fixture new"
        )
        assert (
            CourtListenerRepository(conn).search_caselaw("new case")[0]["case_name"]
            == "Fixture new"
        )
        rollback_corpus_version(
            conn,
            version=follow_up,
            actor="rehearsal-admin",
            reason="bad release fixture",
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT version FROM authority_corpus_versions WHERE status='promoted'"
            )
            assert cur.fetchone()[0] == version
        assert (
            CourtListenerRepository(conn).search_legal_authorities("old authority")[0][
                "title"
            ]
            == "Fixture old"
        )
        assert (
            CourtListenerRepository(conn).search_caselaw("old case")[0]["case_name"]
            == "Fixture old"
        )


def test_courtlistener_sync_store_is_exact_candidate_version(monkeypatch):
    """The normal REST sync writes snapshots/checkpoints, never legacy singletons."""
    db_url = os.getenv("AUTHORITY_REHEARSAL_DATABASE_URL")
    if not db_url:
        pytest.skip(
            "set AUTHORITY_REHEARSAL_DATABASE_URL for the disposable DB rehearsal"
        )
    init_schema(db_url)
    suffix = uuid.uuid4().hex[:10]
    old_version = f"sync-old-{suffix}"
    new_version = f"sync-new-{suffix}"
    opinion_id = 98500000 + int(uuid.uuid4().int % 100000)
    cluster_id = opinion_id
    docket_id = opinion_id

    def bundle(text: str) -> dict[str, object]:
        return {
            "court": {
                "id": "ohio-sync-rehearsal",
                "short_name": "Sync rehearsal",
                "full_name": "Ohio Sync Rehearsal Court",
            },
            "docket": {
                "id": docket_id,
                "docket_number": f"SYNC-{suffix}",
                "case_name": "Version-bound sync fixture",
                "date_filed": "2026-08-31",
            },
            "cluster": {
                "id": cluster_id,
                "docket_id": docket_id,
                "case_name": "Version-bound sync fixture",
                "date_filed": "2026-08-31",
                "citations": [],
                "absolute_url": f"/opinion/{opinion_id}/",
            },
            "opinion": {
                "id": opinion_id,
                "cluster_id": cluster_id,
                "plain_text": text,
                "absolute_url": f"/opinion/{opinion_id}/",
            },
        }

    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO legal_sources
                     (source_key, publisher, source_type, canonical_url, enabled,
                      storage_policy, rights_decision, reviewed_at, reviewed_by,
                      expected_cadence, claim_safe_wording, public_namespace,
                      metadata)
                   VALUES ('courtlistener:ohio-caselaw', 'Rehearsal', 'case_law',
                           'https://www.courtlistener.com/', TRUE,
                           'normalized_text', 'open', now(), 'sync-reviewer',
                           'daily', 'Bounded sync rehearsal source',
                           'public-authority',
                           '{"catalog_schema_version":"rehearsal","implementation_status":"fixture"}')
                   ON CONFLICT (source_key) DO UPDATE SET
                     enabled=TRUE, storage_policy='normalized_text',
                     rights_decision='open', reviewed_at=now(),
                     reviewed_by='sync-reviewer', expected_cadence='daily',
                     claim_safe_wording='Bounded sync rehearsal source',
                     public_namespace='public-authority',
                     metadata=EXCLUDED.metadata"""
            )
        conn.commit()
        for version_name in (old_version, new_version):
            stage_corpus_version(
                conn,
                version=version_name,
                manifest_hash=f"sync-manifest-{version_name}",
                as_of="2026-08-31T00:00:00Z",
                actor="sync-reviewer",
                reason="version-bound CourtListener sync rehearsal",
                embedding_model="mixedbread-ai/mxbai-embed-large-v1",
                embedding_version="1",
                embedding_dimension=1024,
                inherit_current=False,
            )
            admit_public_source(
                conn,
                source_key="courtlistener:ohio-caselaw",
                catalog_schema_version="rehearsal",
                manifest_reference=f"synthetic://sync/{version_name}",
                manifest_sha256=f"sync-manifest-{version_name}",
                reviewed_by="sync-reviewer",
            )

        for version_name, text in (
            (old_version, "Old version-bound CourtListener opinion"),
            (new_version, "New version-bound CourtListener opinion"),
        ):
            monkeypatch.setenv("AUTHORITY_INGEST_CORPUS_VERSION", version_name)
            store = PostgresSyncStore(conn)
            store.ensure_source(date(2025, 1, 1))
            assert store.corpus_version == version_name
            assert store.start_partition("ohio-sync-rehearsal") is not None
            run_id = store.begin_run("ohio-sync-rehearsal", date(2025, 1, 1))
            rows, chunks = store.ingest_bundle(bundle(text))
            assert rows == 1 and chunks >= 1
            store.finish_partition(
                "ohio-sync-rehearsal",
                run_id,
                datetime.now(timezone.utc),
                rows,
                chunks,
            )
            store.unlock_partition("ohio-sync-rehearsal")

        with conn.cursor() as cur:
            cur.execute(
                """SELECT corpus_version, plain_text
                     FROM authority_case_opinions
                    WHERE opinion_id=%s AND corpus_version=ANY(%s)
                    ORDER BY corpus_version""",
                [opinion_id, [old_version, new_version]],
            )
            assert set(cur.fetchall()) == {
                (old_version, "Old version-bound CourtListener opinion"),
                (new_version, "New version-bound CourtListener opinion"),
            }
            cur.execute(
                """SELECT corpus_version, source_key, status
                     FROM authority_harvest_checkpoints
                    WHERE partition_key='ohio-sync-rehearsal'
                      AND corpus_version=ANY(%s)
                    ORDER BY corpus_version""",
                [[old_version, new_version]],
            )
            assert set(cur.fetchall()) == {
                (old_version, "courtlistener:ohio-caselaw", "complete"),
                (new_version, "courtlistener:ohio-caselaw", "complete"),
            }
            cur.execute(
                """SELECT corpus_version, source_key, status
                     FROM ingest_runs
                    WHERE id IN (
                      SELECT id FROM ingest_runs
                       WHERE corpus_version=ANY(%s)
                         AND source_key='courtlistener:ohio-caselaw')""",
                [[old_version, new_version]],
            )
            assert set(cur.fetchall()) == {
                (old_version, "courtlistener:ohio-caselaw", "completed"),
                (new_version, "courtlistener:ohio-caselaw", "completed"),
            }
            cur.execute(
                """SELECT corpus_version, authority_key, source_key
                     FROM authority_records
                    WHERE corpus_version=ANY(%s) AND authority_key=%s
                    ORDER BY corpus_version""",
                [[old_version, new_version], f"case:{opinion_id}"],
            )
            assert set(cur.fetchall()) == {
                (
                    old_version,
                    f"case:{opinion_id}",
                    "courtlistener:ohio-caselaw",
                ),
                (
                    new_version,
                    f"case:{opinion_id}",
                    "courtlistener:ohio-caselaw",
                ),
            }
            cur.execute(
                "SELECT COUNT(*) FROM opinions WHERE opinion_id=%s", [opinion_id]
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT COUNT(*) FROM opinion_clusters WHERE cluster_id=%s",
                [cluster_id],
            )
            assert cur.fetchone()[0] == 0


def test_due_retry_runner_preserves_exact_candidate_version(monkeypatch):
    db_url = os.getenv("AUTHORITY_REHEARSAL_DATABASE_URL")
    if not db_url:
        pytest.skip(
            "set AUTHORITY_REHEARSAL_DATABASE_URL for the disposable DB rehearsal"
        )
    init_schema(db_url)
    suffix = uuid.uuid4().hex[:10]
    source_key = f"retry:source:{suffix}"
    versions = [f"retry-old-{suffix}", f"retry-new-{suffix}"]
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO legal_sources
                     (source_key, publisher, source_type, canonical_url, enabled,
                      storage_policy, rights_decision, reviewed_at, reviewed_by,
                      expected_cadence, claim_safe_wording, public_namespace,
                      metadata)
                   VALUES (%s, 'Retry rehearsal', 'statute', 'https://example.test/retry',
                           TRUE, 'normalized_text', 'official', now(), 'retry-reviewer',
                           'daily', 'Bounded retry rehearsal', 'public-authority',
                           '{"catalog_schema_version":"rehearsal","implementation_status":"fixture"}')""",
                [source_key],
            )
        conn.commit()
        for version_name in versions:
            stage_corpus_version(
                conn,
                version=version_name,
                manifest_hash=f"retry-manifest-{version_name}",
                as_of="2026-08-31T00:00:00Z",
                actor="retry-reviewer",
                reason="version-bound due retry rehearsal",
                embedding_model="mixedbread-ai/mxbai-embed-large-v1",
                embedding_version="1",
                embedding_dimension=1024,
                inherit_current=False,
            )
            admit_public_source(
                conn,
                source_key=source_key,
                catalog_schema_version="rehearsal",
                manifest_reference=f"synthetic://retry/{version_name}",
                manifest_sha256=f"retry-manifest-{version_name}",
                reviewed_by="retry-reviewer",
            )
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO authority_harvest_checkpoints
                         (source_key, partition_key, corpus_version, status,
                          retry_count, next_retry_at)
                       VALUES (%s, %s, %s, 'retryable_failure', 1,
                               now() - interval '1 minute')""",
                    [source_key, f"manifest:{source_key}", version_name],
                )
            conn.commit()

    calls: list[str] = []

    def fake_sync(documents, _catalog, _db_url, *, corpus_versions=None):
        assert len(documents) == 1
        assert corpus_versions and corpus_versions[source_key] in versions
        version_name = corpus_versions[source_key]
        calls.append(version_name)
        return [{"source_key": source_key, "corpus_version": version_name}]

    monkeypatch.setattr(authority_ingest_module, "sync_documents", fake_sync)
    results = authority_ingest_module.retry_due_documents(
        [{"source_key": source_key, "external_id": "retry-doc"}],
        {source_key: {}},
        db_url,
        limit=10,
    )
    assert set(calls) == set(versions)
    assert {row["corpus_version"] for row in results} == set(versions)


def _signed_citator_command(secret: str, claims: dict[str, object]) -> str:
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).digest()

    def encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    return f"{encode(payload)}.{encode(signature)}"


def _citator_scope_assertion(
    *,
    tenant_id: str,
    matter_id: str,
    principal: str,
    authority_key: str,
    delivery_channels: list[str],
    quiet_hours: dict[str, object] | None = None,
    issued: int | None = None,
    expires: int | None = None,
) -> str:
    """Build the same short-lived backend contract used by the private service."""
    issued = int(time.time()) if issued is None else issued
    body = {
        "action": "save",
        "tenant_id": tenant_id,
        "matter_id": matter_id,
        "principal": principal,
        "authority_key": authority_key,
        "delivery_channels": delivery_channels,
        "quiet_hours": quiet_hours or {},
    }
    return _signed_citator_command(
        os.environ["MCP_CITATOR_SCOPE_ASSERTION_SECRET"],
        {
            "tenant_id": tenant_id,
            "matter_id": matter_id,
            "actor": principal,
            "purpose": "citator:watch:save",
            "issued": issued,
            "expires": issued + 300 if expires is None else expires,
            "nonce": "rehearsal-" + uuid.uuid4().hex,
            "body_sha256": hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        },
    )


def _reviewer_authorization_assertion(*, actor: str, principal: str, basis: str) -> str:
    issued = int(time.time())
    body = {
        "action": "authorize_reviewer",
        "principal": principal,
        "authorization_basis": basis,
    }
    return _signed_citator_command(
        os.environ["MCP_OPERATOR_ASSERTION_SECRET"],
        {
            "actor": actor,
            "credential": "rehearsal-platform-jti",
            "purpose": "citator:reviewer:authorize",
            "issued": issued,
            "expires": issued + 60,
            "nonce": "reviewer-" + uuid.uuid4().hex,
            "body_sha256": hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        },
    )


def test_citator_review_watch_and_tenant_isolation_rehearsal(monkeypatch):
    """Exercise source facts, review, watch dedupe/revocation, and RLS in CI.

    This is intentionally part of the mandatory pgvector/PostgreSQL rehearsal,
    rather than an opt-in unit fixture. It uses only synthetic authority and
    UUIDs and never contacts an alert provider.
    """
    db_url = os.getenv("AUTHORITY_REHEARSAL_DATABASE_URL")
    if not db_url:
        pytest.skip(
            "set AUTHORITY_REHEARSAL_DATABASE_URL for the disposable DB rehearsal"
        )
    init_schema(db_url)
    version = "citator-rehearsal-" + uuid.uuid4().hex
    source_key = "citator:rehearsal:" + version
    citing_source_key = "citator:citing:" + version
    citing_authority_key = "case:citing-" + version
    custom_source_key = "custom:citator:" + version
    private_shaped_source_key = "privatefoo:citator:" + version
    opinion_id = 97900000 + int(uuid.uuid4().int % 100000)
    authority_key = f"case:{opinion_id}"
    stale_opinion_id = opinion_id + 1000000
    stale_authority_key = f"case:{stale_opinion_id}"
    custom_authority_key = "case:custom-" + version
    private_shaped_authority_key = "case:private-shaped-" + version
    tenant_id, other_tenant_id, matter_id = (str(uuid.uuid4()) for _ in range(3))
    monkeypatch.setenv("MCP_CITATOR_SCOPE_ASSERTION_SECRET", "c" * 48)
    monkeypatch.setenv("MCP_OPERATOR_ASSERTION_SECRET", "o" * 48)

    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT convalidated FROM pg_constraint
                     WHERE conrelid='citator_alert_events'::regclass
                       AND conname='citator_alert_events_id_tenant_key'"""
            )
            assert cur.fetchone() == (True,)
            cur.execute(
                """SELECT convalidated FROM pg_constraint
                     WHERE conrelid='citator_alert_events'::regclass
                       AND conname='citator_alert_events_evidence_check'"""
            )
            assert cur.fetchone() == (True,)
            cur.execute(
                """SELECT convalidated FROM pg_constraint
                     WHERE conrelid='citator_alert_deliveries'::regclass
                       AND conname='citator_alert_deliveries_event_tenant_fk'"""
            )
            assert cur.fetchone() == (True,)
        stage_corpus_version(
            conn,
            version=version,
            manifest_hash="citator-rehearsal-manifest",
            as_of="2026-08-30T00:00:00Z",
            actor="rehearsal-admin",
            reason="citator/watch/review disposable rehearsal",
            embedding_model="mixedbread-ai/mxbai-embed-large-v1",
            embedding_version="1",
            embedding_dimension=1024,
            inherit_current=False,
        )
        with conn.cursor() as cur:
            # This is an explicit full-rebuild candidate. It does not inherit
            # immutable facts or vectors from the prior promoted release.
            cur.execute(
                "DELETE FROM authority_citation_facts WHERE corpus_version=%s",
                [version],
            )
            cur.execute(
                "DELETE FROM authority_history_facts WHERE corpus_version=%s", [version]
            )
            cur.execute(
                "DELETE FROM authority_records WHERE corpus_version=%s", [version]
            )
            cur.execute(
                "DELETE FROM authority_case_citations WHERE corpus_version=%s",
                [version],
            )
            cur.execute(
                "DELETE FROM authority_case_chunks WHERE corpus_version=%s", [version]
            )
            cur.execute(
                "DELETE FROM authority_case_opinions WHERE corpus_version=%s", [version]
            )
            cur.execute(
                "DELETE FROM authority_case_clusters WHERE corpus_version=%s", [version]
            )
            cur.execute(
                """INSERT INTO legal_sources
                     (source_key, publisher, source_type, canonical_url, enabled,
                      storage_policy, rights_decision, reviewed_at, reviewed_by,
                      expected_cadence, claim_safe_wording, public_namespace,
                      metadata)
                   VALUES (%s, 'Citator rehearsal', 'case_law', 'https://example.test/citator',
                     TRUE, 'normalized_text', 'official', now(), 'rehearsal-admin',
                     'daily', 'Synthetic rehearsal source only', 'public-authority',
                     '{"catalog_schema_version":"rehearsal","implementation_status":"fixture","manifest_reference":"synthetic://citator/rehearsal"}')""",
                [source_key],
            )
            cur.execute(
                """INSERT INTO citator_public_source_admissions
                     (source_key, catalog_schema_version, manifest_reference,
                      manifest_sha256, reviewed_at, reviewed_by)
                   VALUES (%s, 'rehearsal', 'synthetic://citator/rehearsal',
                     'citator-rehearsal-manifest', now(), 'rehearsal-admin')""",
                [source_key],
            )
            cur.execute(
                """INSERT INTO legal_sources
                     (source_key, publisher, source_type, canonical_url, enabled,
                      storage_policy, rights_decision, reviewed_at, reviewed_by,
                      expected_cadence, claim_safe_wording, public_namespace,
                      metadata)
                   VALUES (%s, 'Citing-source rehearsal', 'case_law',
                     'https://example.test/citing', TRUE, 'normalized_text',
                     'official', now(), 'rehearsal-admin', 'daily',
                     'Synthetic citing source only', 'public-authority',
                     '{"catalog_schema_version":"rehearsal","implementation_status":"fixture"}')""",
                [citing_source_key],
            )
            cur.execute(
                """INSERT INTO citator_public_source_admissions
                     (source_key, catalog_schema_version, manifest_reference,
                      manifest_sha256, reviewed_at, reviewed_by)
                   VALUES (%s, 'rehearsal', 'synthetic://citator/citing',
                     'citator-rehearsal-manifest', now(), 'rehearsal-admin')""",
                [citing_source_key],
            )
            for unsafe_source_key, unsafe_authority_key in (
                (custom_source_key, custom_authority_key),
                (private_shaped_source_key, private_shaped_authority_key),
            ):
                cur.execute(
                    """INSERT INTO legal_sources
                         (source_key, publisher, source_type, canonical_url, enabled,
                          storage_policy, rights_decision, reviewed_at, reviewed_by,
                          expected_cadence, claim_safe_wording, metadata)
                       VALUES (%s, 'Unsafe synthetic source', 'case_law', 'https://example.test/unsafe',
                         TRUE, 'normalized_text', 'official', now(), 'rehearsal-admin',
                         'daily', 'Unsafe synthetic source only',
                         '{"catalog_schema_version":"rehearsal","implementation_status":"fixture"}')""",
                    [unsafe_source_key],
                )
                cur.execute(
                    """INSERT INTO authority_records
                         (corpus_version, authority_key, authority_kind, source_key, title,
                          source_url, source_as_of, source_version, currentness_state)
                       VALUES (%s, %s, 'case', %s, 'Unsafe synthetic authority',
                         'https://example.test/unsafe-authority', '2026-08-30T00:00:00Z',
                         %s, 'current')""",
                    [version, unsafe_authority_key, unsafe_source_key, version],
                )
            cur.execute(
                """INSERT INTO authority_records
                     (corpus_version, authority_key, authority_kind, source_key,
                      canonical_citation, title, court, decision_date, source_url,
                      source_as_of, source_version, currentness_state,
                      deterministic_metadata)
                   VALUES (%s, %s, 'case', %s, '1 R. 1', 'Synthetic citator case',
                     'rehearsal-court', '2026-01-01', 'https://example.test/case',
                     '2026-08-30T00:00:00Z', %s, 'current',
                     %s::jsonb)""",
                [
                    version,
                    authority_key,
                    source_key,
                    version,
                    '{"opinion_id":' + str(opinion_id) + "}",
                ],
            )
            cur.execute(
                """INSERT INTO authority_records
                     (corpus_version, authority_key, authority_kind, source_key,
                      title, source_url, source_as_of, source_version,
                      currentness_state, deterministic_metadata)
                   VALUES (%s, %s, 'case', %s, 'Stale synthetic authority',
                     'https://example.test/stale-case', '2026-08-30T00:00:00Z',
                     %s, 'stale', %s::jsonb)""",
                [
                    version,
                    stale_authority_key,
                    source_key,
                    version,
                    '{"opinion_id":' + str(stale_opinion_id) + "}",
                ],
            )
            cur.execute(
                """INSERT INTO authority_case_clusters
                     (corpus_version, source_key, cluster_id, case_name, date_filed)
                   VALUES (%s, %s, %s, 'Synthetic citator case', '2026-01-01')""",
                [version, source_key, opinion_id],
            )
            cur.execute(
                """INSERT INTO authority_case_opinions
                     (corpus_version, opinion_id, cluster_id, source_url, plain_text)
                   VALUES (%s, %s, %s, 'https://example.test/case',
                     'Synthetic citator source-bound authority text')""",
                [version, opinion_id, opinion_id],
            )
            cur.execute(
                """INSERT INTO authority_case_chunks
                     (corpus_version, opinion_id, cluster_id, court_id, chunk_index, content,
                      embedding, embedding_model, embedding_version)
                   VALUES (%s, %s, %s, 'rehearsal-court', 0,
                     'Synthetic citator source-bound authority text',
                     ('[' || array_to_string(array_fill(0, ARRAY[1024]), ',') || ']')::vector,
                     'mixedbread-ai/mxbai-embed-large-v1', '1')""",
                [version, opinion_id, opinion_id],
            )
            cur.execute(
                """INSERT INTO authority_history_facts
                     (corpus_version, authority_key, fact_kind, event_date, source_url,
                      evidence_span, evidence_locator, source_hash)
                   VALUES (%s, %s, 'later_history', '2026-02-01',
                     'https://example.test/history', 'Synthetic history span',
                     '{"paragraph":1}', 'history-sha256-fixture') RETURNING id::text""",
                [version, authority_key],
            )
            history_fact_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO authority_records
                     (corpus_version, authority_key, authority_kind, source_key,
                      title, source_url, source_as_of, source_version, currentness_state)
                   VALUES (%s, %s, 'case', %s, 'Other synthetic authority', 'https://example.test/other',
                     '2026-08-30T00:00:00Z', %s, 'current')""",
                [version, citing_authority_key, citing_source_key, version],
            )
            cur.execute(
                """INSERT INTO authority_history_facts
                     (corpus_version, authority_key, fact_kind, source_url,
                      evidence_span, evidence_locator, source_hash)
                   VALUES (%s, %s, 'direct_history', 'https://example.test/other-history',
                     'Other authority span', '{"paragraph":2}', 'other-sha') RETURNING id::text""",
                [version, citing_authority_key],
            )
            cross_authority_fact_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO authority_citation_facts
                     (corpus_version, citing_authority_key, cited_authority_key,
                      depth, source_url, evidence_span, evidence_locator, source_hash)
                   VALUES (%s, %s, %s, 0, 'https://example.test/citing-edge',
                     'Synthetic citing edge', '{"paragraph":4}',
                     'citing-edge-sha') RETURNING id::text""",
                [version, citing_authority_key, authority_key],
            )
            two_sided_citation_fact_id = cur.fetchone()[0]
            foreign_version = version + "-foreign"
            stage_corpus_version(
                conn,
                version=foreign_version,
                manifest_hash="foreign-citator-manifest",
                as_of="2026-08-30T00:00:00Z",
                actor="rehearsal-admin",
                reason="cross-version evidence negative",
                embedding_model="mixedbread-ai/mxbai-embed-large-v1",
                embedding_version="1",
                embedding_dimension=1024,
            )
            cur.execute(
                """INSERT INTO authority_records
                     (corpus_version, authority_key, authority_kind, source_key,
                      title, source_url, source_as_of, source_version, currentness_state)
                   VALUES (%s, %s, 'case', %s, 'Foreign synthetic authority', 'https://example.test/foreign',
                     '2026-08-30T00:00:00Z', %s, 'current')""",
                [foreign_version, authority_key, source_key, foreign_version],
            )
            cur.execute(
                """INSERT INTO authority_history_facts
                     (corpus_version, authority_key, fact_kind, source_url,
                      evidence_span, evidence_locator, source_hash)
                   VALUES (%s, %s, 'direct_history', 'https://example.test/foreign-history',
                     'Foreign version span', '{"paragraph":3}', 'foreign-sha') RETURNING id::text""",
                [foreign_version, authority_key],
            )
            cross_version_fact_id = cur.fetchone()[0]
        conn.commit()
        _declare_complete_partition(
            conn,
            version=version,
            source_key=source_key,
            partition_key="citator-rehearsal",
            expected=1,
            observed=1,
        )
        _declare_complete_partition(
            conn,
            version=version,
            source_key=citing_source_key,
            partition_key="citator-citing-rehearsal",
            expected=1,
            observed=1,
        )
        with conn.cursor() as cur:
            cur.execute(
                """SELECT embedding_model, embedding_version, vector_dims(embedding)
                     FROM authority_case_chunks
                     WHERE corpus_version=%s AND opinion_id=%s AND chunk_index=0""",
                [version, opinion_id],
            )
            assert cur.fetchone() == ("mixedbread-ai/mxbai-embed-large-v1", "1", 1024)
        with pytest.raises(PermissionError, match="citator records without public"):
            promote_corpus_version(
                conn,
                version=version,
                actor="rehearsal-admin",
                reason="unadmitted citator identities must not promote",
            )
        with conn.cursor() as cur:
            cur.execute(
                """DELETE FROM authority_records
                    WHERE corpus_version=%s AND authority_key=ANY(%s)""",
                [version, [custom_authority_key, private_shaped_authority_key]],
            )
        conn.commit()
        _run_production_audits(conn, version=version, auditor="rehearsal-admin")
        promote_corpus_version(
            conn,
            version=version,
            actor="rehearsal-admin",
            reason="citator rehearsal cutover",
        )
        mismatched_version = version + "-mismatched-citation"
        stage_corpus_version(
            conn,
            version=mismatched_version,
            manifest_hash="citator-rehearsal-manifest",
            as_of="2026-08-30T00:00:00Z",
            actor="rehearsal-admin",
            reason="citation authority/opinion identity negative",
            embedding_model="mixedbread-ai/mxbai-embed-large-v1",
            embedding_version="1",
            embedding_dimension=1024,
        )
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO authority_citation_facts
                     (corpus_version, citing_authority_key, cited_authority_key,
                      citing_opinion_id, depth, source_url, evidence_span,
                      evidence_locator, source_hash)
                   VALUES (%s, %s, %s, %s, 0,
                     'https://example.test/mismatched-citing-edge',
                     'Unrelated public opinion cannot launder a citing authority',
                     '{"paragraph":5}', 'mismatched-citing-edge-sha')
                   RETURNING id::text""",
                [
                    mismatched_version,
                    citing_authority_key,
                    authority_key,
                    opinion_id,
                ],
            )
            mismatched_citation_fact_id = cur.fetchone()[0]
            cur.execute(
                """SELECT COUNT(*) FROM public_authority_citation_facts
                    WHERE id=%s::uuid""",
                [mismatched_citation_fact_id],
            )
            assert cur.fetchone()[0] == 0
        conn.commit()
        mismatch_isolation, _ = run_and_record_control_audit(
            conn,
            version=mismatched_version,
            audit_kind="isolation",
            auditor="rehearsal-admin",
        )
        assert mismatch_isolation["passed"] is False
        assert mismatch_isolation["isolated"] < mismatch_isolation["sample_size"]
        for unsafe_authority_key in (
            custom_authority_key,
            private_shaped_authority_key,
        ):
            with pytest.raises(
                PermissionError, match="promoted, reviewed public authority evidence"
            ):
                record_treatment_assessment(
                    conn,
                    corpus_version=version,
                    authority_key=unsafe_authority_key,
                    treatment_label="unknown",
                    confidence=0.0,
                    policy_version="citator-policy-rehearsal-v1",
                    evidence_fact_ids=[],
                    abstained=True,
                    abstention_reason="unsafe source negative",
                    model_version="synthetic-model-v1",
                    actor="rehearsal-worker",
                )
            with pytest.raises(
                PermissionError,
                match="watches require promoted, reviewed public authority evidence",
            ):
                save_citator_watch(
                    conn,
                    tenant_id=tenant_id,
                    matter_id=matter_id,
                    authority_key=unsafe_authority_key,
                    created_by="rehearsal-attorney",
                    delivery_channels=["in_app"],
                    matter_scope_assertion=_citator_scope_assertion(
                        tenant_id=tenant_id,
                        matter_id=matter_id,
                        principal="rehearsal-attorney",
                        authority_key=unsafe_authority_key,
                        delivery_channels=["in_app"],
                        quiet_hours={},
                    ),
                )
        for fact_id in (
            str(uuid.uuid4()),
            cross_authority_fact_id,
            cross_version_fact_id,
        ):
            with pytest.raises(
                PermissionError, match="same authority and corpus version"
            ):
                record_treatment_assessment(
                    conn,
                    corpus_version=version,
                    authority_key=authority_key,
                    treatment_label="negative",
                    confidence=0.7,
                    policy_version="citator-policy-rehearsal-v1",
                    evidence_fact_ids=[fact_id],
                    model_version="synthetic-model-v1",
                    actor="rehearsal-worker",
                )
        assessment_id = record_treatment_assessment(
            conn,
            corpus_version=version,
            authority_key=authority_key,
            treatment_label="distinguished",
            confidence=0.77,
            policy_version="citator-policy-rehearsal-v1",
            evidence_fact_ids=[history_fact_id],
            model_version="synthetic-model-v1",
            actor="rehearsal-worker",
        )
        with pytest.raises(
            PermissionError, match="authorized citator review principal"
        ):
            review_treatment_assessment(
                conn,
                assessment_id=assessment_id,
                reviewer="unregistered-attorney",
                decision="accepted",
            )
        with pytest.raises(PermissionError, match="invalid"):
            authorize_citator_reviewer(
                conn,
                principal="rehearsal-attorney",
                authorization_basis="synthetic attorney review fixture",
                actor="rehearsal-admin",
                authorization_assertion="forged",
            )
        with pytest.raises(PermissionError, match="mismatched"):
            authorize_citator_reviewer(
                conn,
                principal="rehearsal-attorney",
                authorization_basis="synthetic attorney review fixture",
                actor="rehearsal-admin",
                authorization_assertion=_signed_citator_command(
                    os.environ["MCP_OPERATOR_ASSERTION_SECRET"],
                    {
                        "actor": "rehearsal-admin",
                        "credential": "rehearsal-platform-jti",
                        "purpose": "citator:watch:save",
                        "issued": int(time.time()),
                        "expires": int(time.time()) + 60,
                        "nonce": "cross-action-" + uuid.uuid4().hex,
                        "body_sha256": "0" * 64,
                    },
                ),
            )
        reviewer_assertion = _reviewer_authorization_assertion(
            actor="rehearsal-admin",
            principal="rehearsal-attorney",
            basis="synthetic attorney review fixture",
        )
        authorize_citator_reviewer(
            conn,
            principal="rehearsal-attorney",
            authorization_basis="synthetic attorney review fixture",
            actor="rehearsal-admin",
            authorization_assertion=reviewer_assertion,
        )
        with pytest.raises(PermissionError, match="replayed"):
            authorize_citator_reviewer(
                conn,
                principal="rehearsal-attorney",
                authorization_basis="synthetic attorney review fixture",
                actor="rehearsal-admin",
                authorization_assertion=reviewer_assertion,
            )
        review_treatment_assessment(
            conn,
            assessment_id=assessment_id,
            reviewer="rehearsal-attorney",
            decision="overridden",
            override_label="no_decision",
            note="Synthetic attorney override",
        )
        assert (
            CourtListenerRepository(conn).citator_status()["release"][
                "reviewed_assessment_count"
            ]
            == 1
        )
        watch_assertion = _citator_scope_assertion(
            tenant_id=tenant_id,
            matter_id=matter_id,
            principal="rehearsal-attorney",
            authority_key=authority_key,
            delivery_channels=["in_app"],
            quiet_hours={"start": "22:00", "end": "07:00", "timezone": "UTC"},
        )
        watch_id = save_citator_watch(
            conn,
            tenant_id=tenant_id,
            matter_id=matter_id,
            authority_key=authority_key,
            created_by="rehearsal-attorney",
            delivery_channels=["in_app"],
            quiet_hours={"start": "22:00", "end": "07:00", "timezone": "UTC"},
            matter_scope_assertion=watch_assertion,
        )
        with pytest.raises(PermissionError, match="replayed"):
            save_citator_watch(
                conn,
                tenant_id=tenant_id,
                matter_id=matter_id,
                authority_key=authority_key,
                created_by="rehearsal-attorney",
                delivery_channels=["in_app"],
                quiet_hours={"start": "22:00", "end": "07:00", "timezone": "UTC"},
                matter_scope_assertion=watch_assertion,
            )
        with pytest.raises(PermissionError, match="mismatched"):
            save_citator_watch(
                conn,
                tenant_id=tenant_id,
                matter_id=matter_id,
                authority_key="case:tampered",
                created_by="rehearsal-attorney",
                delivery_channels=["in_app"],
                matter_scope_assertion=_citator_scope_assertion(
                    tenant_id=tenant_id,
                    matter_id=matter_id,
                    principal="rehearsal-attorney",
                    authority_key=authority_key,
                    delivery_channels=["in_app"],
                    quiet_hours={},
                ),
            )
        with pytest.raises(PermissionError, match="expired"):
            save_citator_watch(
                conn,
                tenant_id=tenant_id,
                matter_id=matter_id,
                authority_key=authority_key,
                created_by="expired-watch",
                delivery_channels=["in_app"],
                matter_scope_assertion=_citator_scope_assertion(
                    tenant_id=tenant_id,
                    matter_id=matter_id,
                    principal="expired-watch",
                    authority_key=authority_key,
                    delivery_channels=["in_app"],
                    quiet_hours={},
                    issued=int(time.time()) - 400,
                    expires=int(time.time()) - 1,
                ),
            )
        alert_id = enqueue_citator_alert(
            conn,
            tenant_id=tenant_id,
            watch_id=watch_id,
            corpus_version=version,
            authority_key=authority_key,
            event_fingerprint="synthetic-history-v1",
            event_kind="history",
            evidence_fact_id=history_fact_id,
        )
        assert alert_id
        with pytest.raises(PermissionError, match="stored same-version"):
            enqueue_citator_alert(
                conn,
                tenant_id=tenant_id,
                watch_id=watch_id,
                corpus_version=version,
                authority_key=authority_key,
                event_fingerprint="forged-alert-evidence",
                event_kind="history",
                evidence_fact_id=str(uuid.uuid4()),
            )
        with pytest.raises(TypeError, match="source_url"):
            enqueue_citator_alert(
                conn,
                tenant_id=tenant_id,
                watch_id=watch_id,
                corpus_version=version,
                authority_key=authority_key,
                event_fingerprint="forged-alert-url",
                event_kind="history",
                evidence_fact_id=history_fact_id,
                source_url="https://forged.example",
            )
        with pytest.raises(TypeError, match="payload"):
            enqueue_citator_alert(
                conn,
                tenant_id=tenant_id,
                watch_id=watch_id,
                corpus_version=version,
                authority_key=authority_key,
                event_fingerprint="forged-alert-payload",
                event_kind="history",
                evidence_fact_id=history_fact_id,
                payload={"forged": True},
            )
        assert (
            enqueue_citator_alert(
                conn,
                tenant_id=tenant_id,
                watch_id=watch_id,
                corpus_version=version,
                authority_key=authority_key,
                event_fingerprint="synthetic-history-v1",
                event_kind="history",
                evidence_fact_id=history_fact_id,
            )
            is None
        )
        with pytest.raises(PermissionError, match="not consented"):
            record_citator_alert_delivery(
                conn,
                tenant_id=tenant_id,
                alert_event_id=alert_id,
                channel="email",
                delivery_key="synthetic-email-attempt",
                attempted_outcome="queued",
            )
        assert record_citator_alert_delivery(
            conn,
            tenant_id=tenant_id,
            alert_event_id=alert_id,
            channel="in_app",
            delivery_key="synthetic-attempt-1",
            attempted_outcome="queued",
            now=datetime(2026, 8, 30, 23, 0, tzinfo=timezone.utc),
        )
        # The disposable DB user is a superuser and would bypass RLS. Exercise
        # the policy through a temporary least-privilege role instead, while
        # the composite FK independently rejects a bypass-shaped event whose
        # tenant UUID does not match the referenced watch.
        rls_role = "citator_rls_" + uuid.uuid4().hex[:16]
        with conn.cursor() as cur:
            cur.execute(f'CREATE ROLE "{rls_role}" NOLOGIN')
            cur.execute(f'GRANT SELECT ON citator_watches TO "{rls_role}"')
            cur.execute(f'GRANT INSERT ON citator_alert_events TO "{rls_role}"')
            cur.execute("SET LOCAL app.current_tenant_id = %s", [other_tenant_id])
            cur.execute(f'SET LOCAL ROLE "{rls_role}"')
            cur.execute("SAVEPOINT cross_tenant_watch_event")
            with pytest.raises(DatabaseError):
                cur.execute(
                    """INSERT INTO citator_alert_events
                         (watch_id, tenant_id, corpus_version, authority_key,
                          event_fingerprint, event_kind, source_url, payload)
                       VALUES (%s::uuid, %s::uuid, %s, %s, 'cross-tenant-fk', 'history',
                         'https://example.test/history', '{}'::jsonb)""",
                    [watch_id, other_tenant_id, version, authority_key],
                )
            cur.execute("ROLLBACK TO SAVEPOINT cross_tenant_watch_event")
            cur.execute("RELEASE SAVEPOINT cross_tenant_watch_event")
            cur.execute(
                "SELECT count(*) FROM citator_watches WHERE id=%s::uuid", [watch_id]
            )
            assert cur.fetchone()[0] == 0
            cur.execute("RESET ROLE")
            cur.execute(f'REVOKE SELECT ON citator_watches FROM "{rls_role}"')
            cur.execute(f'REVOKE INSERT ON citator_alert_events FROM "{rls_role}"')
            cur.execute(f'DROP ROLE "{rls_role}"')
        conn.commit()

        def save_race_watch(principal: str) -> str:
            return save_citator_watch(
                conn,
                tenant_id=tenant_id,
                matter_id=matter_id,
                authority_key=authority_key,
                created_by=principal,
                delivery_channels=["in_app"],
                matter_scope_assertion=_citator_scope_assertion(
                    tenant_id=tenant_id,
                    matter_id=matter_id,
                    principal=principal,
                    authority_key=authority_key,
                    delivery_channels=["in_app"],
                    quiet_hours={},
                ),
            )

        def lock_watch_row(
            target_watch_id: str, locked: threading.Event, release: threading.Event
        ):
            with connect(db_url) as lock_conn:
                with lock_conn.cursor() as cur:
                    cur.execute("SET LOCAL app.current_tenant_id = %s", [tenant_id])
                    cur.execute(
                        "SELECT id FROM citator_watches WHERE id=%s::uuid FOR UPDATE",
                        [target_watch_id],
                    )
                    assert cur.fetchone()
                    locked.set()
                    assert release.wait(timeout=10)
                lock_conn.commit()

        # These two races use separate real PostgreSQL connections.  Revoke is
        # queued first behind the row lock, so enqueue/delivery must recheck
        # state only after revoke commits; neither may persist after revocation.
        race_watch = save_race_watch("rehearsal-race-enqueue")
        locked, release, revoke_done, enqueue_done = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        race_results: dict[str, object] = {}
        locker = threading.Thread(
            target=lock_watch_row, args=(race_watch, locked, release)
        )
        locker.start()
        assert locked.wait(timeout=5)

        def revoke_enqueue_race():
            try:
                with connect(db_url) as race_conn:
                    race_results["revoke"] = revoke_citator_watch(
                        race_conn, tenant_id=tenant_id, watch_id=race_watch
                    )
            finally:
                revoke_done.set()

        def enqueue_after_revoke():
            with connect(db_url) as race_conn:
                race_results["enqueue"] = enqueue_citator_alert(
                    race_conn,
                    tenant_id=tenant_id,
                    watch_id=race_watch,
                    corpus_version=version,
                    authority_key=authority_key,
                    event_fingerprint="race-enqueue-after-revoke",
                    event_kind="history",
                    evidence_fact_id=history_fact_id,
                )
            enqueue_done.set()

        revoker = threading.Thread(target=revoke_enqueue_race)
        enqueuer = threading.Thread(target=enqueue_after_revoke)
        revoker.start()
        time.sleep(0.05)
        enqueuer.start()
        assert not revoke_done.wait(timeout=0.15)
        assert not enqueue_done.wait(timeout=0.15)
        release.set()
        for worker in (locker, revoker, enqueuer):
            worker.join(timeout=10)
            assert not worker.is_alive()
        assert race_results == {"revoke": True, "enqueue": None}

        delivery_watch = save_race_watch("rehearsal-race-delivery")
        delivery_alert = enqueue_citator_alert(
            conn,
            tenant_id=tenant_id,
            watch_id=delivery_watch,
            corpus_version=version,
            authority_key=authority_key,
            event_fingerprint="race-delivery-before-revoke",
            event_kind="history",
            evidence_fact_id=history_fact_id,
        )
        assert delivery_alert
        locked, release, revoke_done, delivery_done = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        delivery_results: dict[str, object] = {}
        locker = threading.Thread(
            target=lock_watch_row, args=(delivery_watch, locked, release)
        )
        locker.start()
        assert locked.wait(timeout=5)

        def revoke_delivery_race():
            try:
                with connect(db_url) as race_conn:
                    delivery_results["revoke"] = revoke_citator_watch(
                        race_conn, tenant_id=tenant_id, watch_id=delivery_watch
                    )
            finally:
                revoke_done.set()

        def delivery_after_revoke():
            with connect(db_url) as race_conn:
                delivery_results["delivery"] = record_citator_alert_delivery(
                    race_conn,
                    tenant_id=tenant_id,
                    alert_event_id=delivery_alert,
                    channel="in_app",
                    delivery_key="race-delivery-after-revoke",
                    attempted_outcome="queued",
                )
            delivery_done.set()

        revoker = threading.Thread(target=revoke_delivery_race)
        deliverer = threading.Thread(target=delivery_after_revoke)
        revoker.start()
        time.sleep(0.05)
        deliverer.start()
        assert not revoke_done.wait(timeout=0.15)
        assert not delivery_done.wait(timeout=0.15)
        release.set()
        for worker in (locker, revoker, deliverer):
            worker.join(timeout=10)
            assert not worker.is_alive()
        assert delivery_results["revoke"] is True
        with conn.cursor() as cur:
            cur.execute("SET LOCAL app.current_tenant_id = %s", [tenant_id])
            cur.execute(
                "SELECT outcome FROM citator_alert_deliveries WHERE alert_event_id=%s::uuid AND delivery_key='race-delivery-after-revoke'",
                [delivery_alert],
            )
            assert cur.fetchone()[0] == "revoked"
        conn.commit()
        assert revoke_citator_watch(conn, tenant_id=tenant_id, watch_id=watch_id)
        assert (
            enqueue_citator_alert(
                conn,
                tenant_id=tenant_id,
                watch_id=watch_id,
                corpus_version=version,
                authority_key=authority_key,
                event_fingerprint="synthetic-history-v2",
                event_kind="history",
                evidence_fact_id=history_fact_id,
            )
            is None
        )
        result = CourtListenerRepository(conn).authority_treatment(opinion_id)
        assert result["status"] == "review_ready"
        assert result["machine_interpretation"]["attorney_reviewed"] is True
        assert result["machine_interpretation"]["effective_label"] == "no_decision"
        assert "does not determine" in result["claim"]
        assert "good law" in result["claim"].lower()

        rejected_assessment = record_treatment_assessment(
            conn,
            corpus_version=version,
            authority_key=authority_key,
            treatment_label="negative",
            confidence=0.8,
            policy_version="citator-policy-rehearsal-v1",
            evidence_fact_ids=[history_fact_id],
            model_version="synthetic-model-v1",
            actor="rehearsal-worker",
        )
        review_treatment_assessment(
            conn,
            assessment_id=rejected_assessment,
            reviewer="rehearsal-attorney",
            decision="rejected",
            note="Synthetic rejection",
        )
        rejected = CourtListenerRepository(conn).authority_treatment(opinion_id)
        assert rejected["machine_interpretation"]["review_state"] == "rejected"
        assert rejected["machine_interpretation"]["effective_label"] == "unknown"

        stale_assessment = record_treatment_assessment(
            conn,
            corpus_version=version,
            authority_key=authority_key,
            treatment_label="positive",
            confidence=0.8,
            policy_version="citator-policy-rehearsal-v1",
            evidence_fact_ids=[history_fact_id],
            model_version="synthetic-model-v1",
            actor="rehearsal-worker",
            stale_at=datetime.now(timezone.utc),
        )
        review_treatment_assessment(
            conn,
            assessment_id=stale_assessment,
            reviewer="rehearsal-attorney",
            decision="accepted",
            note="Synthetic stale acceptance",
        )
        stale = CourtListenerRepository(conn).authority_treatment(opinion_id)
        assert stale["machine_interpretation"]["review_state"] == "stale"
        assert stale["machine_interpretation"]["effective_label"] == "unknown"

        # Keep one active event through the lineage mutation matrix.  Delivery
        # must re-check the source at attempt time; an event that was valid
        # when queued cannot become deliverable after its authority lineage is
        # disabled, revoked, or changed.
        lineage_delivery_watch = save_race_watch("rehearsal-lineage-delivery")
        lineage_delivery_alert = enqueue_citator_alert(
            conn,
            tenant_id=tenant_id,
            watch_id=lineage_delivery_watch,
            corpus_version=version,
            authority_key=authority_key,
            event_fingerprint="lineage-delivery-baseline",
            event_kind="history",
            evidence_fact_id=history_fact_id,
        )
        assert lineage_delivery_alert

        def new_lineage_review_candidate(label: str) -> str:
            return record_treatment_assessment(
                conn,
                corpus_version=version,
                authority_key=authority_key,
                treatment_label="distinguished",
                confidence=0.6,
                policy_version="citator-policy-rehearsal-v1",
                evidence_fact_ids=[history_fact_id],
                model_version=f"lineage-{label}",
                actor="rehearsal-worker",
            )

        def assert_citator_lineage_suppressed(
            label: str,
            review_assessment_id: str,
            *,
            expect_release_unavailable: bool = True,
        ):
            treatment = CourtListenerRepository(conn).authority_treatment(opinion_id)
            assert treatment["status"] == "unavailable"
            assert treatment["deterministic_facts"] == {
                "history": [],
                "citing_references": [],
            }
            citator_status = CourtListenerRepository(conn).citator_status()
            if expect_release_unavailable:
                assert citator_status["available"] is False
                assert citator_status["release"] is None
            else:
                assert citator_status["available"] is True
                assert citator_status["release"]["reviewed_assessment_count"] == 0
            with pytest.raises(
                PermissionError, match="promoted, reviewed public authority evidence"
            ):
                record_treatment_assessment(
                    conn,
                    corpus_version=version,
                    authority_key=authority_key,
                    treatment_label="negative",
                    confidence=0.8,
                    policy_version="citator-policy-rehearsal-v1",
                    evidence_fact_ids=[history_fact_id],
                    actor="rehearsal-worker",
                )
            with pytest.raises(
                PermissionError,
                match="watches require promoted, reviewed public authority evidence",
            ):
                save_citator_watch(
                    conn,
                    tenant_id=tenant_id,
                    matter_id=matter_id,
                    authority_key=authority_key,
                    created_by=f"lineage-{label}",
                    delivery_channels=["in_app"],
                    matter_scope_assertion=_citator_scope_assertion(
                        tenant_id=tenant_id,
                        matter_id=matter_id,
                        principal=f"lineage-{label}",
                        authority_key=authority_key,
                        delivery_channels=["in_app"],
                        quiet_hours={},
                    ),
                )
            with pytest.raises(
                PermissionError,
                match="alerts require promoted, reviewed public authority evidence",
            ):
                enqueue_citator_alert(
                    conn,
                    tenant_id=tenant_id,
                    watch_id=watch_id,
                    corpus_version=version,
                    authority_key=authority_key,
                    event_fingerprint=f"lineage-{label}",
                    event_kind="history",
                    evidence_fact_id=history_fact_id,
                )
            with pytest.raises(
                PermissionError,
                match="current promoted public authority evidence",
            ):
                review_treatment_assessment(
                    conn,
                    assessment_id=review_assessment_id,
                    reviewer="rehearsal-attorney",
                    decision="accepted",
                    note=f"must fail closed for {label}",
                )
            delivery_id = record_citator_alert_delivery(
                conn,
                tenant_id=tenant_id,
                alert_event_id=lineage_delivery_alert,
                channel="in_app",
                delivery_key=f"lineage-{label}",
                attempted_outcome="queued",
            )
            with conn.cursor() as cur:
                cur.execute("SET LOCAL app.current_tenant_id = %s", [tenant_id])
                cur.execute(
                    "SELECT outcome FROM citator_alert_deliveries WHERE id=%s::uuid",
                    [delivery_id],
                )
                assert cur.fetchone() == ("revoked",)
            conn.commit()

        citator_source_mutations = [
            ("enabled", "FALSE", "TRUE"),
            ("rights_decision", "'prohibited'", "'official'"),
            ("storage_policy", "'prohibited'", "'normalized_text'"),
            ("reviewed_at", "NULL", "now()"),
            ("reviewed_by", "NULL", "'rehearsal-admin'"),
            (
                "claim_safe_wording",
                "NULL",
                "'Synthetic rehearsal source only'",
            ),
            ("public_namespace", "'private'", "'public-authority'"),
        ]
        for column, bad_value, good_value in citator_source_mutations:
            review_candidate = new_lineage_review_candidate(column)
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE legal_sources SET {column}={bad_value} WHERE source_key=%s",
                    [source_key],
                )
            conn.commit()
            assert_citator_lineage_suppressed(
                column, review_candidate, expect_release_unavailable=False
            )
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE legal_sources SET {column}={good_value} WHERE source_key=%s",
                    [source_key],
                )
            conn.commit()

        metadata_mutations = [
            (
                "implementation",
                "metadata=metadata-'implementation_status'",
                "metadata=jsonb_set(metadata, '{implementation_status}', '\"fixture\"'::jsonb)",
            ),
        ]
        for label, bad_set, good_set in metadata_mutations:
            review_candidate = new_lineage_review_candidate(label)
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE legal_sources SET {bad_set} WHERE source_key=%s",
                    [source_key],
                )
            conn.commit()
            assert_citator_lineage_suppressed(
                label, review_candidate, expect_release_unavailable=False
            )
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE legal_sources SET {good_set} WHERE source_key=%s",
                    [source_key],
                )
            conn.commit()

        # Source metadata is not the release manifest authority.  Mutating it
        # cannot redirect an already-reviewed, version-bound admission.
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE legal_sources
                      SET metadata=metadata ||
                          '{"manifest_reference":"attacker-controlled",
                            "catalog_schema_version":"attacker-controlled"}'::jsonb
                    WHERE source_key=%s""",
                [source_key],
            )
        conn.commit()
        assert (
            CourtListenerRepository(conn).authority_treatment(opinion_id)["status"]
            != "unavailable"
        )
        assert CourtListenerRepository(conn).citator_status()["available"] is True
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE legal_sources
                      SET metadata=metadata ||
                          '{"manifest_reference":"synthetic://citator/rehearsal",
                            "catalog_schema_version":"rehearsal"}'::jsonb
                    WHERE source_key=%s""",
                [source_key],
            )
        conn.commit()

        admission_mutations = [("admission-active", "active=FALSE", "active=TRUE")]
        for label, bad_set, good_set in admission_mutations:
            review_candidate = new_lineage_review_candidate(label)
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE citator_public_source_admissions SET {bad_set} WHERE source_key=%s",
                    [source_key],
                )
            conn.commit()
            assert_citator_lineage_suppressed(
                label, review_candidate, expect_release_unavailable=False
            )
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE citator_public_source_admissions SET {good_set} WHERE source_key=%s",
                    [source_key],
                )
            conn.commit()

        for bad_set in (
            "catalog_schema_version='changed'",
            "manifest_reference='changed'",
            "manifest_sha256=repeat('d', 64)",
            "namespace='private'",
        ):
            with pytest.raises(
                DatabaseError,
                match="promoted authority admission lineage is immutable",
            ):
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE citator_public_source_admissions SET {bad_set} WHERE source_key=%s",
                        [source_key],
                    )
            conn.rollback()
        assert (
            CourtListenerRepository(conn).authority_treatment(opinion_id)["status"]
            != "unavailable"
        )

        lineage_delivery_watch = save_race_watch("rehearsal-lineage-delivery")
        healthy_lineage_alert = enqueue_citator_alert(
            conn,
            tenant_id=tenant_id,
            watch_id=lineage_delivery_watch,
            corpus_version=version,
            authority_key=authority_key,
            event_fingerprint="two-sided-lineage-healthy",
            event_kind="treatment",
            evidence_fact_id=two_sided_citation_fact_id,
        )
        assert healthy_lineage_alert
        record_citator_alert_delivery(
            conn,
            tenant_id=tenant_id,
            alert_event_id=healthy_lineage_alert,
            channel="in_app",
            delivery_key="two-sided-lineage-healthy",
            attempted_outcome="sent",
            now=datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
        )
        revoked_lineage_alert = enqueue_citator_alert(
            conn,
            tenant_id=tenant_id,
            watch_id=lineage_delivery_watch,
            corpus_version=version,
            authority_key=authority_key,
            event_fingerprint="two-sided-lineage-revoked-before-delivery",
            event_kind="treatment",
            evidence_fact_id=two_sided_citation_fact_id,
        )
        assert revoked_lineage_alert
        with conn.cursor() as cur:
            cur.execute("SET LOCAL app.current_tenant_id = %s", [tenant_id])
            cur.execute(
                """SELECT e.evidence_fact_id::text, e.evidence_type, d.outcome
                     FROM citator_alert_events e
                     JOIN citator_alert_deliveries d ON d.alert_event_id=e.id
                    WHERE e.id=%s::uuid
                      AND d.delivery_key='two-sided-lineage-healthy'""",
                [healthy_lineage_alert],
            )
            assert cur.fetchone() == (
                two_sided_citation_fact_id,
                "citation",
                "sent",
            )
        conn.commit()

        citation_assessment_id = record_treatment_assessment(
            conn,
            corpus_version=version,
            authority_key=authority_key,
            treatment_label="distinguished",
            confidence=0.72,
            policy_version="citator-policy-rehearsal-v1",
            evidence_fact_ids=[two_sided_citation_fact_id],
            model_version="two-sided-lineage-rehearsal",
            actor="rehearsal-worker",
        )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE legal_sources SET enabled=FALSE WHERE source_key=%s",
                [citing_source_key],
            )
        conn.commit()
        record_citator_alert_delivery(
            conn,
            tenant_id=tenant_id,
            alert_event_id=revoked_lineage_alert,
            channel="in_app",
            delivery_key="two-sided-lineage-revoked-before-delivery",
            attempted_outcome="sent",
            now=datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
        )
        with conn.cursor() as cur:
            cur.execute("SET LOCAL app.current_tenant_id = %s", [tenant_id])
            cur.execute(
                """SELECT outcome, detail FROM citator_alert_deliveries
                    WHERE alert_event_id=%s::uuid
                      AND delivery_key='two-sided-lineage-revoked-before-delivery'""",
                [revoked_lineage_alert],
            )
            assert cur.fetchone() == (
                "revoked",
                "authority evidence lineage is no longer admitted",
            )
            cur.execute(
                """SELECT COUNT(*) FROM citator_alert_deliveries
                    WHERE alert_event_id=%s::uuid AND outcome='sent'""",
                [revoked_lineage_alert],
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT COUNT(*) FROM public_authority_citation_facts WHERE id=%s::uuid",
                [two_sided_citation_fact_id],
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT COUNT(*) FROM public_authority_citation_facts WHERE id=%s::uuid",
                [mismatched_citation_fact_id],
            )
            assert cur.fetchone()[0] == 0
        conn.commit()
        two_sided_suppressed = CourtListenerRepository(conn).authority_treatment(
            opinion_id
        )
        assert two_sided_suppressed["deterministic_facts"]["citing_references"] == []
        assert (
            two_sided_suppressed["machine_interpretation"].get("assessment_id")
            != citation_assessment_id
        )
        with pytest.raises(PermissionError):
            enqueue_citator_alert(
                conn,
                tenant_id=tenant_id,
                watch_id=watch_id,
                corpus_version=version,
                authority_key=authority_key,
                event_fingerprint="revoked-citing-source",
                event_kind="treatment",
                evidence_fact_id=two_sided_citation_fact_id,
            )
        with pytest.raises(PermissionError):
            record_treatment_assessment(
                conn,
                corpus_version=version,
                authority_key=authority_key,
                treatment_label="negative",
                confidence=0.8,
                policy_version="citator-policy-rehearsal-v1",
                evidence_fact_ids=[two_sided_citation_fact_id],
                model_version="revoked-citing-source",
                actor="rehearsal-worker",
            )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE legal_sources SET enabled=TRUE WHERE source_key=%s",
                [citing_source_key],
            )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM public_authority_citation_facts WHERE id=%s::uuid",
                [two_sided_citation_fact_id],
            )
            assert cur.fetchone()[0] == 1

        # Currentness is immutable release evidence.  A record staged as stale
        # must remain unavailable, while attempts to rewrite currentness after
        # promotion are rejected rather than silently changing served claims.
        stale_treatment = CourtListenerRepository(conn).authority_treatment(
            stale_opinion_id
        )
        assert stale_treatment["status"] == "unavailable"
        with pytest.raises(
            PermissionError, match="promoted, reviewed public authority evidence"
        ):
            record_treatment_assessment(
                conn,
                corpus_version=version,
                authority_key=stale_authority_key,
                treatment_label="negative",
                confidence=0.8,
                policy_version="citator-policy-rehearsal-v1",
                evidence_fact_ids=[],
                model_version="stale-authority-currentness",
                abstained=True,
                abstention_reason="staged currentness is stale",
                actor="rehearsal-worker",
            )
        with pytest.raises(DatabaseError):
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE authority_records SET currentness_state='current'
                        WHERE corpus_version=%s AND authority_key=%s""",
                    [version, stale_authority_key],
                )
        conn.rollback()

        with pytest.raises(DatabaseError):
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE citator_public_source_admissions SET namespace='private' WHERE source_key=%s",
                    [source_key],
                )
        conn.rollback()
        for invalid_review_set in ("reviewed_at=NULL", "reviewed_by=''"):
            with pytest.raises(DatabaseError):
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE citator_public_source_admissions SET {invalid_review_set} WHERE source_key=%s",
                        [source_key],
                    )
            conn.rollback()


def test_durable_operator_assertion_replay_rehearsal(monkeypatch):
    """Two consumers sharing PostgreSQL cannot consume one nonce twice."""
    db_url = os.getenv("AUTHORITY_REHEARSAL_DATABASE_URL")
    if not db_url:
        pytest.skip(
            "set AUTHORITY_REHEARSAL_DATABASE_URL for the disposable DB rehearsal"
        )
    init_schema(db_url)
    nonce = "rehearsal-nonce-" + uuid.uuid4().hex
    claims = {
        "nonce": nonce,
        "credential": "rehearsal-jti",
        "actor": "rehearsal-operator",
        "scope": "platform:write",
        "method": "POST",
        "path": "/api/mcp/control/stage",
        "body_sha256": "0" * 64,
        "issued": 1_700_000_000,
        "expires": 1_900_000_000,
    }
    monkeypatch.setattr(authority_server, "connect", lambda: connect(db_url))
    outcomes = []
    barrier = threading.Barrier(2)

    def consume():
        barrier.wait()
        try:
            authority_server.consume_operator_assertion(claims)
            outcomes.append("accepted")
        except Exception as exc:  # one concurrent consumer must lose the nonce
            outcomes.append(exc)

    workers = [threading.Thread(target=consume) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
    assert all(not worker.is_alive() for worker in workers)
    assert len(outcomes) == 2
    assert sum(outcome == "accepted" for outcome in outcomes) == 1
    rejected = [outcome for outcome in outcomes if isinstance(outcome, HTTPException)]
    assert len(rejected) == 1
    assert rejected[0].status_code == 403
    assert rejected[0].detail == "replayed signed operator context"
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*), MIN(credential_id), MIN(actor), MIN(scope),
                           MIN(method), MIN(path), MIN(body_sha256)
                     FROM authority_operator_assertions WHERE nonce=%s""",
                [nonce],
            )
            assert cur.fetchone() == (
                1,
                claims["credential"],
                claims["actor"],
                claims["scope"],
                claims["method"],
                claims["path"],
                claims["body_sha256"],
            )


def test_process_once_rehearsal_both_corpora(monkeypatch):
    """Exercise the production worker path with a deterministic model."""
    db_url = os.getenv("AUTHORITY_REHEARSAL_DATABASE_URL")
    if not db_url:
        pytest.skip(
            "set AUTHORITY_REHEARSAL_DATABASE_URL for the disposable DB rehearsal"
        )
    init_schema(db_url)
    version = "worker-rehearsal-" + uuid.uuid4().hex
    source_key = "worker:rehearsal:" + version
    model_name = "mixedbread-ai/mxbai-embed-large-v1"
    model_version = "1"
    dimension = 1024
    document_id = str(uuid.uuid4())
    with connect(db_url) as conn:
        stage_corpus_version(
            conn,
            version=version,
            manifest_hash="worker-rehearsal-manifest",
            as_of="2026-08-30T00:00:00+00:00",
            actor="rehearsal-admin",
            reason="production worker rehearsal",
            embedding_model=model_name,
            embedding_version=model_version,
            embedding_dimension=dimension,
        )
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO legal_sources
                    (source_key, publisher, source_type, canonical_url, enabled,
                     storage_policy, rights_decision, reviewed_at, reviewed_by,
                     expected_cadence, claim_safe_wording, public_namespace,
                     metadata)
                    VALUES (%s, 'Worker rehearsal', 'statute', 'https://worker.example',
                            TRUE, 'normalized_text', 'official', now(), 'rehearsal-admin',
                            'daily', 'Bounded worker rehearsal', 'public-authority',
                            '{\"catalog_schema_version\":\"rehearsal\",\"implementation_status\":\"fixture\",\"manifest_reference\":\"worker-rehearsal-manifest\"}')""",
                [source_key],
            )
            cur.execute(
                """INSERT INTO citator_public_source_admissions
                     (source_key, catalog_schema_version, manifest_reference,
                      manifest_sha256, reviewed_at, reviewed_by)
                   VALUES (%s, 'rehearsal', 'worker-rehearsal-manifest',
                           'worker-rehearsal-manifest', now(), 'rehearsal-admin')""",
                [source_key],
            )
            cur.execute(
                """INSERT INTO legal_documents
                    (id, source_key, external_id, document_type, title, jurisdiction,
                     authority_tier, canonical_url, corpus_version, text_content,
                     content_hash, metadata)
                    VALUES (%s, %s, 'worker-doc', 'statute', 'Worker document', 'US',
                            'binding_primary', 'https://worker.example/doc', %s,
                            'Worker legal text', 'worker-doc-hash',
                            '{\"namespace\":\"public-authority\"}')""",
                [document_id, source_key, version],
            )
            cur.execute(
                """INSERT INTO legal_document_chunks
                    (document_id, chunk_index, content, content_hash, corpus_version)
                    VALUES (%s, 0, 'Worker legal text', 'worker-chunk-hash', %s)""",
                [document_id, version],
            )
            cur.execute(
                """INSERT INTO authority_case_clusters
                    (corpus_version, source_key, cluster_id, case_name, date_filed)
                    VALUES (%s, %s, 98200001, 'Worker case', '2026-01-01')""",
                [version, source_key],
            )
            cur.execute(
                """INSERT INTO authority_case_opinions
                    (corpus_version, opinion_id, cluster_id, plain_text)
                    VALUES (%s, 98200001, 98200001, 'Worker case opinion')""",
                [version],
            )
            cur.execute(
                """INSERT INTO authority_case_chunks
                    (corpus_version, opinion_id, cluster_id, court_id, chunk_index, content)
                    VALUES (%s, 98200001, 98200001, 'worker-court', 0, 'Worker case opinion')""",
                [version],
            )
        conn.commit()

    class DeterministicModel:
        def encode(self, texts, **_kwargs):
            return [[1.0] + [0.0] * 1023 for _ in texts]

    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) FROM legal_document_chunks c
                     JOIN legal_documents d ON d.id=c.document_id
                     JOIN legal_sources s ON s.source_key=d.source_key
                     JOIN public_authority_source_lineage pas
                       ON pas.source_key=d.source_key
                      AND pas.corpus_version=d.corpus_version
                    WHERE d.corpus_version=%s
                      AND c.corpus_version IS NOT DISTINCT FROM %s
                      AND s.enabled IS TRUE
                      AND d.public_namespace='public-authority'
                      AND d.document_status='current'
                      AND s.storage_policy IN ('mirror', 'normalized_text')
                      AND (c.embedding IS NULL
                           OR c.embedding_model IS DISTINCT FROM %s
                           OR c.embedding_version::text IS DISTINCT FROM %s
                           OR vector_dims(c.embedding) IS DISTINCT FROM %s)""",
                [version, version, model_name, model_version, dimension],
            )
            legal_before = cur.fetchone()[0]
            cur.execute(
                """SELECT COUNT(*) FROM authority_case_chunks ch
                     JOIN authority_case_clusters cl
                       ON cl.corpus_version=ch.corpus_version
                      AND cl.cluster_id=ch.cluster_id
                     JOIN public_authority_source_lineage pas
                       ON pas.source_key=cl.source_key
                      AND pas.corpus_version=ch.corpus_version
                    WHERE ch.corpus_version=%s
                      AND cl.public_namespace='public-authority'
                      AND (ch.embedding IS NULL
                           OR ch.embedding_model IS DISTINCT FROM %s
                           OR ch.embedding_version IS DISTINCT FROM %s
                           OR vector_dims(ch.embedding) IS DISTINCT FROM %s)""",
                [version, model_name, model_version, dimension],
            )
            authority_before = cur.fetchone()[0]
        conn.commit()
    assert legal_before > 0 and authority_before > 0
    config = WorkerConfig(
        worker_id=0,
        total_workers=1,
        batch_size=8,
        model=model_name,
        model_version=str(model_version),
        dim=dimension,
        db_url=db_url,
    )
    monkeypatch.setenv("AUTHORITY_EMBEDDING_CORPUS_VERSION", version)
    monkeypatch.setenv("AUTHORITY_HEARTBEAT_INTERVAL_SECONDS", "0.01")
    mismatch_config = WorkerConfig(
        worker_id=0,
        total_workers=1,
        batch_size=8,
        model="wrong-model",
        model_version=model_version,
        dim=dimension,
        db_url=db_url,
    )
    with pytest.raises(RuntimeError, match="embedding contract mismatch"):
        process_once(mismatch_config, DeterministicModel())
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM authority_embedding_shards WHERE corpus_version=%s",
                [version],
            )
            assert cur.fetchone()[0] == 0
    embedded = process_once(config, DeterministicModel())
    replay_embedded = process_once(config, DeterministicModel())
    assert embedded == legal_before + authority_before
    assert replay_embedded == 0
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT corpus_table, status, model, model_version, dimension,
                           throughput_per_minute, capacity_evidence->>'observed_at'
                    FROM authority_embedding_shards
                   WHERE corpus_version=%s
                   ORDER BY corpus_table""",
                [version],
            )
            rows = cur.fetchall()
    assert [row[0] for row in rows] == [
        "authority_case_chunks",
        "legal_document_chunks",
    ]
    for (
        corpus_table,
        status,
        model,
        version_value,
        row_dimension,
        throughput,
        observed_at,
    ) in rows:
        assert status == "complete"
        assert model == model_name
        assert version_value == model_version
        assert row_dimension == dimension
        assert throughput is not None and throughput >= 0
        assert observed_at is not None

    # A newly-arriving chunk reopens the completed authority shard. A blocked
    # first worker holds the row lock while a duplicate worker must be unable
    # to claim the same shard or write the row.
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO authority_case_chunks
                    (corpus_version, opinion_id, cluster_id, court_id, chunk_index, content)
                    VALUES (%s, 98200001, 98200001, 'worker-court', 1, 'late worker case')""",
                [version],
            )
        conn.commit()
    started = threading.Event()
    release = threading.Event()
    worker_errors = []

    class BlockingModel(DeterministicModel):
        def encode(self, texts, **kwargs):
            started.set()
            assert release.wait(10)
            return super().encode(texts, **kwargs)

    def run_worker():
        try:
            process_once(config, BlockingModel())
        except Exception as exc:  # pragma: no cover - surfaced below
            worker_errors.append(exc)

    first = threading.Thread(target=run_worker)
    first.start()
    try:
        assert started.wait(10)
        duplicate = process_once(config, DeterministicModel())
        assert duplicate == 0
    finally:
        release.set()
        first.join(timeout=20)
    assert not first.is_alive()
    assert not worker_errors

    # A changed embedding contract is real work, even when the text is
    # unchanged. The completed shard must reopen and replace the old vector.
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO authority_case_chunks
                    (corpus_version, opinion_id, cluster_id, court_id, chunk_index,
                     content, embedding, embedding_model, embedding_version)
                    VALUES (%s, 98200001, 98200001, 'worker-court', 2,
                            'contract changed',
                            ('[' || array_to_string(array_fill(0, ARRAY[1024]), ',') || ']')::vector,
                            'old-model', '0')""",
                [version],
            )
        conn.commit()
    assert process_once(config, DeterministicModel()) == 1
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT embedding_model, embedding_version, vector_dims(embedding)
                     FROM authority_case_chunks
                    WHERE corpus_version=%s AND chunk_index=2""",
                [version],
            )
            assert cur.fetchone() == (model_name, model_version, dimension)

    # A heartbeat loss during inference aborts before vector writes. The
    # production heartbeat thread observes the loss on its own connection.
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO authority_case_chunks
                    (corpus_version, opinion_id, cluster_id, court_id, chunk_index, content)
                    VALUES (%s, 98200001, 98200001, 'worker-court', 3, 'lease loss')""",
                [version],
            )
        conn.commit()
    import mcp_server.jetson_worker as jetson_worker

    def lose_lease(*_args, **_kwargs):
        return not blocked.is_set()

    blocked = threading.Event()
    release_loss = threading.Event()

    class LeaseLossModel(DeterministicModel):
        def encode(self, texts, **kwargs):
            blocked.set()
            assert release_loss.wait(10)
            return super().encode(texts, **kwargs)

    monkeypatch.setattr(jetson_worker, "heartbeat_embedding_shard", lose_lease)
    loss_errors = []

    def run_loss_worker():
        try:
            process_once(config, LeaseLossModel())
        except Exception as exc:  # pragma: no cover - surfaced below
            loss_errors.append(exc)

    loss_thread = threading.Thread(target=run_loss_worker)
    loss_thread.start()
    try:
        assert blocked.wait(10)
    finally:
        release_loss.set()
        loss_thread.join(timeout=20)
        monkeypatch.setattr(
            jetson_worker, "heartbeat_embedding_shard", heartbeat_embedding_shard
        )
    assert not loss_thread.is_alive()
    assert not loss_errors
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.embedding, s.status FROM authority_case_chunks c
                     JOIN authority_embedding_shards s
                       ON s.shard_key=%s
                    WHERE c.corpus_version=%s AND c.chunk_index=3""",
                [f"authority_case_chunks:0:1:{version}", version],
            )
            embedding, status = cur.fetchone()
            assert embedding is None
            assert status == "retryable"

    # Repeated production failures consume bounded attempts and terminate in
    # dead-letter without ever writing a vector. This also proves the
    # telemetry health object is retained on the shard record.
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO authority_case_chunks
                    (corpus_version, opinion_id, cluster_id, court_id, chunk_index, content)
                    VALUES (%s, 98200001, 98200001, 'worker-court', 4, 'terminal failure')""",
                [version],
            )
        conn.commit()

    class FailingModel:
        def encode(self, _texts, **_kwargs):
            raise RuntimeError("deterministic inference failure")

    for _ in range(3):
        process_once(config, FailingModel())
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.embedding, s.status, s.attempts,
                           s.capacity_evidence->'health'->>'state',
                           s.capacity_evidence->'health'->>'sample_age_seconds'
                      FROM authority_case_chunks c
                      JOIN authority_embedding_shards s
                        ON s.corpus_version=c.corpus_version
                       AND s.corpus_table='authority_case_chunks'
                     WHERE c.corpus_version=%s AND c.chunk_index=4
                     ORDER BY s.updated_at DESC LIMIT 1""",
                [version],
            )
            failed_embedding, failed_status, attempts, health_state, sample_age = (
                cur.fetchone()
            )
            assert failed_embedding is None
            assert failed_status == "dead_letter"
            assert attempts >= 3
            assert health_state in {"healthy", "unavailable", "threshold_exceeded"}
            assert sample_age is not None


def test_citator_evidence_constraint_force_rls_owner_upgrade():
    """Legacy alert evidence upgrades safely for a FORCE-RLS table owner."""
    db_url = os.getenv("AUTHORITY_REHEARSAL_DATABASE_URL")
    if not db_url:
        pytest.skip(
            "set AUTHORITY_REHEARSAL_DATABASE_URL for the disposable DB rehearsal"
        )
    role = "citator_upgrade_" + uuid.uuid4().hex[:12]
    password = "Upgrade" + uuid.uuid4().hex
    schema = "citator_upgrade_" + uuid.uuid4().hex[:12]
    parts = urlsplit(db_url)
    host = parts.hostname or "127.0.0.1"
    port = f":{parts.port}" if parts.port else ""
    role_netloc = f"{quote(role, safe='')}:{quote(password, safe='')}@{host}{port}"
    options = "options=-csearch_path%3D" + quote(schema + ",public", safe="")
    role_query = "&".join(value for value in (parts.query, options) if value)
    role_url = urlunsplit(
        (parts.scheme, role_netloc, parts.path, role_query, parts.fragment)
    )
    tenant_id = str(uuid.uuid4())
    evidence_fact_id = str(uuid.uuid4())
    version = "legacy-alert-evidence-" + uuid.uuid4().hex
    role_created = False
    try:
        with connect(db_url) as admin:
            admin.autocommit = True
            with admin.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
                cur.execute(
                    f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}' "
                    "NOSUPERUSER NOBYPASSRLS"
                )
                role_created = True
                cur.execute(f'CREATE SCHEMA "{schema}" AUTHORIZATION "{role}"')
                cur.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
                cur.execute(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=%s",
                    [role],
                )
                assert cur.fetchone() == (False, False)

        # Create the full production schema as the same least-privilege owner
        # that will later run the additive migration.
        init_schema(role_url)
        with connect(role_url) as owner:
            with owner.cursor() as cur:
                cur.execute(
                    "ALTER TABLE citator_alert_events "
                    "DROP CONSTRAINT citator_alert_events_evidence_check"
                )
                cur.execute(
                    "ALTER TABLE citator_alert_events "
                    "ALTER COLUMN evidence_fact_id DROP NOT NULL, "
                    "ALTER COLUMN evidence_type DROP NOT NULL"
                )
                cur.execute(
                    """INSERT INTO authority_corpus_versions
                         (version, status, manifest_hash, as_of)
                       VALUES (%s, 'staged', %s, now())""",
                    [version, "legacy-alert-manifest-" + uuid.uuid4().hex],
                )
                cur.execute("SET LOCAL app.current_tenant_id=%s", [tenant_id])
                cur.execute(
                    """INSERT INTO citator_watches
                         (tenant_id, authority_key, created_by, consented,
                          delivery_channels, state)
                       VALUES (%s::uuid, 'case:legacy-alert', 'legacy-owner', TRUE,
                         '["in_app"]'::jsonb, 'active')
                       RETURNING id::text""",
                    [tenant_id],
                )
                watch_id = cur.fetchone()[0]
                cur.execute(
                    """INSERT INTO citator_alert_events
                         (watch_id, tenant_id, corpus_version, authority_key,
                          event_fingerprint, event_kind, source_url, payload)
                       VALUES (%s::uuid, %s::uuid, %s, 'case:legacy-alert',
                         'legacy-before-evidence-columns', 'history',
                         'https://example.test/legacy-alert', %s::jsonb)
                       RETURNING id::text""",
                    [
                        watch_id,
                        tenant_id,
                        version,
                        json.dumps(
                            {
                                "evidence_fact_id": evidence_fact_id,
                                "evidence_type": "history",
                            }
                        ),
                    ],
                )
                legacy_event_id = cur.fetchone()[0]
            owner.commit()

        # FORCE RLS makes the owner's no-GUC SELECT see no rows—the exact
        # deployment shape that made a pre-query unsafe as validation proof.
        with connect(role_url) as owner:
            with owner.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM citator_alert_events")
                assert cur.fetchone()[0] == 0

        # The real initializer must succeed without a tenant GUC. Constraint
        # validation sees the physical legacy row, catches only its CHECK
        # violation, and leaves NOT VALID enforcement for future writes.
        init_schema(role_url)
        with connect(role_url) as owner:
            with owner.cursor() as cur:
                cur.execute(
                    """SELECT convalidated FROM pg_constraint
                        WHERE conrelid='citator_alert_events'::regclass
                          AND conname='citator_alert_events_evidence_check'"""
                )
                assert cur.fetchone() == (False,)
                cur.execute("SET LOCAL app.current_tenant_id=%s", [tenant_id])
                cur.execute(
                    """SELECT evidence_fact_id, evidence_type,
                              COALESCE(evidence_fact_id::text,
                                       payload->>'evidence_fact_id'),
                              COALESCE(evidence_type, payload->>'evidence_type')
                         FROM citator_alert_events WHERE id=%s::uuid""",
                    [legacy_event_id],
                )
                assert cur.fetchone() == (
                    None,
                    None,
                    evidence_fact_id,
                    "history",
                )
                cur.execute("SAVEPOINT invalid_new_alert_evidence")
                with pytest.raises(DatabaseError):
                    cur.execute(
                        """INSERT INTO citator_alert_events
                             (watch_id, tenant_id, corpus_version, authority_key,
                              event_fingerprint, event_kind, source_url, payload)
                           VALUES (%s::uuid, %s::uuid, %s, 'case:legacy-alert',
                             'invalid-new-evidence', 'history',
                             'https://example.test/invalid-new', '{}'::jsonb)""",
                        [watch_id, tenant_id, version],
                    )
                cur.execute("ROLLBACK TO SAVEPOINT invalid_new_alert_evidence")
                cur.execute("RELEASE SAVEPOINT invalid_new_alert_evidence")
                cur.execute(
                    """INSERT INTO citator_alert_events
                         (watch_id, tenant_id, corpus_version, authority_key,
                          event_fingerprint, event_kind, evidence_fact_id,
                          evidence_type, source_url, payload)
                       VALUES (%s::uuid, %s::uuid, %s, 'case:legacy-alert',
                         'valid-new-evidence', 'history', %s::uuid, 'history',
                         'https://example.test/valid-new', '{}'::jsonb)
                       RETURNING id::text""",
                    [watch_id, tenant_id, version, str(uuid.uuid4())],
                )
                assert cur.fetchone()[0]
            owner.commit()

            # Payload fallback never grants delivery authority: the legacy
            # event is re-resolved, finds no promoted public fact lineage, and
            # is durably suppressed rather than recorded as sent.
            assert record_citator_alert_delivery(
                owner,
                tenant_id=tenant_id,
                alert_event_id=legacy_event_id,
                channel="in_app",
                delivery_key="legacy-fallback-revalidation",
                attempted_outcome="sent",
            )
            with owner.cursor() as cur:
                cur.execute("SET LOCAL app.current_tenant_id=%s", [tenant_id])
                cur.execute(
                    """SELECT outcome, detail FROM citator_alert_deliveries
                        WHERE alert_event_id=%s::uuid
                          AND delivery_key='legacy-fallback-revalidation'""",
                    [legacy_event_id],
                )
                assert cur.fetchone() == (
                    "revoked",
                    "authority evidence lineage is no longer admitted",
                )
            owner.commit()
    finally:
        if role_created:
            with connect(db_url) as admin:
                admin.autocommit = True
                with admin.cursor() as cur:
                    cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                    cur.execute(f'DROP OWNED BY "{role}"')
                    cur.execute(f'DROP ROLE IF EXISTS "{role}"')


def test_legal_only_upgrade_bootstrap_rehearsal():
    """A statute-only legacy database also receives a searchable snapshot."""
    db_url = os.getenv("AUTHORITY_REHEARSAL_DATABASE_URL")
    if not db_url:
        pytest.skip(
            "set AUTHORITY_REHEARSAL_DATABASE_URL for the disposable DB rehearsal"
        )
    schema = "legal_legacy_" + uuid.uuid4().hex[:12]
    scoped_url = (
        db_url
        + ("&" if "?" in db_url else "?")
        + ("options=-csearch_path%3D" + quote(schema + ",public", safe=""))
    )
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            # Legacy schemas still resolve the production pgvector extension
            # from public while their tables live in an isolated search_path.
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(f'CREATE SCHEMA "{schema}"')
            cur.execute(f'SET search_path TO "{schema}", public')
            cur.execute(
                """CREATE TABLE legal_sources (
                    source_key text PRIMARY KEY, display_name text, description text,
                    publisher text NOT NULL, source_type text NOT NULL, jurisdiction text,
                    court_id text, canonical_url text NOT NULL,
                    authority_tier text NOT NULL DEFAULT 'secondary',
                    official_status text NOT NULL DEFAULT 'aggregator',
                    ingestion_mode text NOT NULL DEFAULT 'manual',
                    storage_policy text NOT NULL DEFAULT 'metadata_only',
                    access_type text NOT NULL DEFAULT 'public_web',
                    license_status text NOT NULL DEFAULT 'review_required', terms_url text,
                    sync_frequency text, data_format text, corpus_table text,
                    enabled boolean NOT NULL DEFAULT false, priority integer NOT NULL DEFAULT 100,
                    coverage_start date, coverage_end date, coverage_kind text NOT NULL DEFAULT 'bounded',
                    last_attempted_at timestamptz, last_successful_sync_at timestamptz,
                    item_count bigint NOT NULL DEFAULT 0, chunk_count bigint NOT NULL DEFAULT 0,
                    embedded_chunk_count bigint NOT NULL DEFAULT 0, parser_version text,
                    embedding_model text, embedding_version integer, current_error text,
                    licensing_notes text, expected_cadence text, rights_decision text,
                    claim_safe_wording text, metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                    updated_at timestamptz NOT NULL DEFAULT now())"""
            )
            cur.execute(
                """CREATE TABLE legal_documents (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), source_key text NOT NULL,
                    external_id text NOT NULL, document_type text NOT NULL, title text NOT NULL,
                    citation text, jurisdiction text, authority_tier text NOT NULL,
                    document_status text NOT NULL DEFAULT 'current', publication_date date,
                    effective_date date, termination_date date, canonical_url text NOT NULL,
                    source_modified_at timestamptz, retrieved_at timestamptz NOT NULL DEFAULT now(),
                    content_hash text, raw_media_type text, raw_storage_uri text,
                    parser_version text, text_content text, metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
                    FOREIGN KEY (source_key) REFERENCES legal_sources(source_key) ON DELETE RESTRICT,
                    UNIQUE (source_key, external_id))"""
            )
            cur.execute(
                """CREATE TABLE legal_document_chunks (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), document_id uuid NOT NULL,
                    chunk_index integer NOT NULL, heading_path jsonb NOT NULL DEFAULT '[]'::jsonb,
                    content text NOT NULL, content_hash text NOT NULL, token_count integer,
                    fts tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
                    embedding vector(1024), embedding_model text NOT NULL DEFAULT 'mixedbread-ai/mxbai-embed-large-v1',
                    embedding_version integer NOT NULL DEFAULT 0, metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
                    FOREIGN KEY (document_id) REFERENCES legal_documents(id) ON DELETE CASCADE,
                    UNIQUE (document_id, chunk_index))"""
            )
            cur.execute(
                """CREATE TABLE source_sync_states (
                    source_key text NOT NULL, partition_key text NOT NULL,
                    checkpoint_at timestamptz, cursor_url text,
                    status text NOT NULL DEFAULT 'idle', last_attempted_at timestamptz,
                    last_successful_sync_at timestamptz, rows_processed bigint NOT NULL DEFAULT 0,
                    chunks_created bigint NOT NULL DEFAULT 0, last_error text,
                    metadata jsonb NOT NULL DEFAULT '{}'::jsonb, updated_at timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (source_key, partition_key),
                    FOREIGN KEY (source_key) REFERENCES legal_sources(source_key) ON DELETE CASCADE)"""
            )
            cur.execute(
                """INSERT INTO legal_sources
                    (source_key, publisher, source_type, canonical_url, enabled,
                     storage_policy, rights_decision, expected_cadence, claim_safe_wording)
                    VALUES ('legacy:statute', 'Legacy', 'statute', 'https://legacy.example',
                            true, 'normalized_text', 'official', 'daily', 'Bounded statute fixture')"""
            )
            cur.execute(
                """INSERT INTO legal_documents
                    (source_key, external_id, document_type, title, jurisdiction, authority_tier,
                     canonical_url, content_hash, text_content)
                    VALUES ('legacy:statute', 'statute-1', 'statute', 'Legacy statute', 'US',
                            'binding_primary', 'https://legacy.example/statute', 'legacy-hash',
                            'Legacy statute text') RETURNING id"""
            )
            document_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO legal_document_chunks
                    (document_id, chunk_index, content, content_hash)
                    VALUES (%s, 0, 'Legacy statute text', 'legacy-chunk-hash')""",
                [document_id],
            )
            cur.execute(
                """SELECT count(*) FROM information_schema.columns
                    WHERE table_schema=current_schema()
                      AND table_name IN ('legal_documents', 'legal_document_chunks')
                      AND column_name='corpus_version'"""
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                """SELECT count(*) FROM pg_constraint
                    WHERE conrelid='legal_document_chunks'::regclass
                      AND conname IN ('legal_document_chunks_document_id_fkey',
                                      'legal_document_chunks_document_id_chunk_index_key')"""
            )
            assert cur.fetchone()[0] == 2
        conn.commit()
    init_schema(scoped_url)
    with connect(scoped_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT d.corpus_version, c.corpus_version
                     FROM legal_documents d JOIN legal_document_chunks c ON c.document_id=d.id
                    WHERE d.external_id='statute-1'"""
            )
            assert cur.fetchone() == (("legacy-bootstrap", "legacy-bootstrap"))
            # Migration preserves the historical rows but does not infer a
            # public classification from their old table/source shape.
            result = CourtListenerRepository(conn).search_legal_authorities(
                "Legacy statute"
            )
            assert result == []
            cur.execute(
                """UPDATE legal_sources
                      SET reviewed_at=now(), reviewed_by='legacy-upgrade-reviewer',
                          claim_safe_wording='Reviewed legacy statute fixture only',
                          metadata=metadata ||
                            '{"catalog_schema_version":"legacy-rehearsal",
                              "implementation_status":"legacy-bootstrap",
                              "manifest_reference":"legacy-bootstrap-manifest"}'::jsonb
                    WHERE source_key='legacy:statute'"""
            )
        conn.commit()
        reviewed_version = "legacy-statute-reviewed-" + uuid.uuid4().hex
        stage_corpus_version(
            conn,
            version=reviewed_version,
            manifest_hash="legacy-statute-reviewed",
            as_of="2026-08-31T00:00:00Z",
            actor="legacy-upgrade-reviewer",
            reason="review legacy statute through a staged successor",
            embedding_model="mixedbread-ai/mxbai-embed-large-v1",
            embedding_version="1",
            embedding_dimension=1024,
            inherit_current=False,
        )
        admit_public_source(
            conn,
            source_key="legacy:statute",
            catalog_schema_version="legacy-rehearsal",
            manifest_reference="legacy-bootstrap-manifest",
            manifest_sha256="legacy-statute-reviewed",
            reviewed_by="legacy-upgrade-reviewer",
        )
        ingest_result = ingest_document(
            conn,
            {
                "source_key": "legacy:statute",
                "external_id": "statute-1",
                "document_type": "statute",
                "title": "Legacy statute",
                "citation": None,
                "jurisdiction": "US",
                "authority_tier": "binding_primary",
                "document_status": "current",
                "canonical_url": "https://legacy.example/statute",
                "parser_version": "legacy-reviewed-parser-v1",
                "practice_areas": ["legacy-rehearsal"],
                "parser": "legacy-reviewed-parser-v1",
                "official_status": "official",
                "acquisition_basis": "reviewed legacy migration fixture",
                "coverage_notes": "bounded legacy statute fixture",
            },
            FetchedDocument(
                text="Legacy statute text",
                content_hash="legacy-reviewed-hash",
                media_type="text/html",
                retrieved_at=datetime.now(timezone.utc),
                source_modified_at=None,
                etag="legacy-reviewed-etag",
                resolved_url="https://legacy.example/statute",
            ),
            corpus_version=reviewed_version,
        )
        assert ingest_result["chunks_created"] == 1
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE legal_document_chunks
                      SET embedding=('[' || array_to_string(array_fill(0, ARRAY[1024]), ',') || ']')::vector,
                          embedding_model='mixedbread-ai/mxbai-embed-large-v1',
                          embedding_version=1
                    WHERE corpus_version=%s""",
                [reviewed_version],
            )
        conn.commit()
        _declare_complete_partition(
            conn,
            version=reviewed_version,
            source_key="legacy:statute",
            partition_key="manifest:legacy:statute",
            expected=1,
            observed=1,
        )
        assert materialize_citator_facts(conn, reviewed_version) >= 1
        conn.commit()
        _run_production_audits(
            conn,
            version=reviewed_version,
            auditor="legacy-upgrade-reviewer",
        )
        promote_corpus_version(
            conn,
            version=reviewed_version,
            actor="legacy-upgrade-reviewer",
            reason="promote reviewed legacy statute successor",
        )
        with conn.cursor() as cur:
            result = CourtListenerRepository(conn).search_legal_authorities(
                "Legacy statute"
            )
            assert result and result[0]["title"] == "Legacy statute"
            cur.execute(
                """SELECT convalidated FROM pg_constraint
                    WHERE conrelid='legal_document_chunks'::regclass
                      AND conname='fk_legal_document_chunks_same_version'"""
            )
            assert cur.fetchone() == (True,)
        conn.commit()
    init_schema(scoped_url)
    with connect(scoped_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT corpus_version FROM legal_documents
                    WHERE external_id='statute-1' ORDER BY corpus_version"""
            )
            assert {row[0] for row in cur.fetchall()} == {
                "legacy-bootstrap",
                reviewed_version,
            }


def test_legacy_upgrade_bootstrap_rehearsal():
    """Upgrade a pre-control-plane schema without losing its served corpus."""
    db_url = os.getenv("AUTHORITY_REHEARSAL_DATABASE_URL")
    if not db_url:
        pytest.skip(
            "set AUTHORITY_REHEARSAL_DATABASE_URL for the disposable DB rehearsal"
        )
    schema = "legacy_rehearsal_" + uuid.uuid4().hex[:12]
    scoped_url = (
        db_url
        + ("&" if "?" in db_url else "?")
        + ("options=-csearch_path%3D" + quote(schema + ",public", safe=""))
    )
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            # Match production extension visibility before creating a legacy
            # table that predates the control-plane schema initializer.
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(f'CREATE SCHEMA "{schema}"')
            cur.execute(f'SET search_path TO "{schema}", public')
            cur.execute(
                """CREATE TABLE legal_sources (
                    source_key text PRIMARY KEY, display_name text, description text,
                    publisher text NOT NULL, source_type text NOT NULL, jurisdiction text,
                    court_id text, canonical_url text NOT NULL,
                    authority_tier text NOT NULL DEFAULT 'secondary', official_status text NOT NULL DEFAULT 'aggregator',
                    ingestion_mode text NOT NULL DEFAULT 'manual', storage_policy text NOT NULL DEFAULT 'metadata_only',
                    access_type text NOT NULL DEFAULT 'public_web', license_status text NOT NULL DEFAULT 'review_required',
                    terms_url text, sync_frequency text, data_format text, corpus_table text,
                    enabled boolean NOT NULL DEFAULT false, priority integer NOT NULL DEFAULT 100,
                    coverage_start date, coverage_end date, coverage_kind text NOT NULL DEFAULT 'bounded',
                    last_attempted_at timestamptz, last_successful_sync_at timestamptz,
                    item_count bigint NOT NULL DEFAULT 0, chunk_count bigint NOT NULL DEFAULT 0,
                    embedded_chunk_count bigint NOT NULL DEFAULT 0, parser_version text,
                    embedding_model text, embedding_version integer, current_error text,
                    licensing_notes text, expected_cadence text, rights_decision text,
                    claim_safe_wording text, metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                    updated_at timestamptz NOT NULL DEFAULT now())"""
            )
            cur.execute(
                """CREATE TABLE courts (
                    court_id text PRIMARY KEY, full_name text NOT NULL,
                    metadata jsonb NOT NULL DEFAULT '{}'::jsonb)"""
            )
            cur.execute(
                """CREATE TABLE dockets (
                    docket_id bigint PRIMARY KEY, court_id text REFERENCES courts(court_id),
                    docket_number text, case_name text, date_filed date,
                    metadata jsonb NOT NULL DEFAULT '{}'::jsonb)"""
            )
            cur.execute(
                "INSERT INTO courts (court_id, full_name) VALUES ('legacy-court', 'Legacy Court')"
            )
            cur.execute(
                """CREATE TABLE opinion_clusters (
                    cluster_id bigint PRIMARY KEY, docket_id bigint REFERENCES dockets(docket_id),
                    case_name text, date_filed date, precedential_status text,
                    citations jsonb NOT NULL DEFAULT '[]'::jsonb,
                    metadata jsonb NOT NULL DEFAULT '{}'::jsonb)"""
            )
            cur.execute(
                """CREATE TABLE opinions (
                    opinion_id bigint PRIMARY KEY, cluster_id bigint, type text,
                    author_id bigint, html_with_citations text, plain_text text,
                    sha1 text, source_url text,
                    updated_at timestamptz NOT NULL DEFAULT now(),
                    source_created_at timestamptz, source_modified_at timestamptz,
                    content_hash text, last_synced_at timestamptz,
                    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                    FOREIGN KEY (cluster_id) REFERENCES opinion_clusters(cluster_id))"""
            )
            cur.execute(
                """CREATE TABLE opinion_chunks (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), opinion_id bigint NOT NULL REFERENCES opinions(opinion_id) ON DELETE CASCADE,
                    cluster_id bigint REFERENCES opinion_clusters(cluster_id), court_id text REFERENCES courts(court_id), chunk_index integer NOT NULL,
                    content text NOT NULL, fts tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
                    embedding vector(1024), embedding_model text NOT NULL DEFAULT 'mixedbread-ai/mxbai-embed-large-v1',
                    embedding_version integer NOT NULL DEFAULT 0,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    UNIQUE (opinion_id, chunk_index))"""
            )
            cur.execute(
                """CREATE TABLE opinion_citations (
                    id bigserial PRIMARY KEY, citing_opinion_id bigint REFERENCES opinions(opinion_id), cited_opinion_id bigint,
                    cited_cluster_id bigint, cited_reporter text,
                    cited_volume text, cited_page text, depth integer NOT NULL DEFAULT 0)"""
            )
            cur.execute(
                """CREATE TABLE source_sync_states (
                    source_key text NOT NULL, partition_key text NOT NULL,
                    checkpoint_at timestamptz, cursor_url text,
                    status text NOT NULL DEFAULT 'idle', last_attempted_at timestamptz,
                    last_successful_sync_at timestamptz, rows_processed bigint NOT NULL DEFAULT 0,
                    chunks_created bigint NOT NULL DEFAULT 0, last_error text,
                    metadata jsonb NOT NULL DEFAULT '{}'::jsonb, updated_at timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (source_key, partition_key),
                    FOREIGN KEY (source_key) REFERENCES legal_sources(source_key) ON DELETE CASCADE)"""
            )
            cur.execute(
                """INSERT INTO legal_sources
                    (source_key, publisher, source_type, canonical_url, enabled,
                     storage_policy, rights_decision, expected_cadence,
                     claim_safe_wording)
                    VALUES ('legacy:fixture', 'Legacy', 'case_law',
                            'https://legacy.example', true, 'normalized_text',
                            'official', 'daily', 'Bounded legacy fixture')"""
            )
            cur.execute(
                """INSERT INTO opinion_clusters
                    (cluster_id, case_name, date_filed, citations)
                    VALUES (98000001, 'Legacy served case', '2025-01-01', '[]')"""
            )
            cur.execute(
                """INSERT INTO opinions
                    (opinion_id, cluster_id, author_id, plain_text, source_url)
                    VALUES (98000001, 98000001, 98000001,
                            'Legacy served opinion', 'https://legacy.example/opinion')"""
            )
            cur.execute(
                """INSERT INTO opinion_chunks
                    (opinion_id, cluster_id, court_id, chunk_index, content)
                    VALUES (98000001, 98000001, 'legacy-court', 0,
                            'Legacy served opinion')"""
            )
            cur.execute(
                """INSERT INTO opinion_citations
                    (citing_opinion_id, cited_reporter, cited_volume, cited_page)
                    VALUES (98000001, 'Legacy Reporter', '9', '99')"""
            )
            cur.execute(
                """SELECT count(*) FROM information_schema.columns
                    WHERE table_name IN ('opinion_clusters', 'opinion_chunks')
                      AND column_name='corpus_version'
                      AND table_schema=current_schema()"""
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                """SELECT count(*) FROM pg_constraint
                    WHERE conrelid='opinion_chunks'::regclass
                      AND conname='opinion_chunks_opinion_id_chunk_index_key'"""
            )
            assert cur.fetchone()[0] == 1
        conn.commit()
    init_schema(scoped_url)
    with connect(scoped_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT version, status FROM authority_corpus_versions
                    WHERE version='legacy-bootstrap'"""
            )
            assert cur.fetchone() == ("legacy-bootstrap", "promoted")
            cur.execute(
                """SELECT COUNT(*) FROM authority_case_clusters
                    WHERE corpus_version='legacy-bootstrap' AND cluster_id=98000001"""
            )
            assert cur.fetchone()[0] == 1
            cur.execute(
                """SELECT COUNT(*) FROM authority_case_opinions
                    WHERE corpus_version='legacy-bootstrap' AND opinion_id=98000001"""
            )
            assert cur.fetchone()[0] == 1
            cur.execute(
                """SELECT COUNT(*) FROM authority_case_chunks
                    WHERE corpus_version='legacy-bootstrap' AND opinion_id=98000001"""
            )
            assert cur.fetchone()[0] == 1
            cur.execute(
                """SELECT cited_opinion_id, cited_reporter, cited_volume, cited_page
                    FROM authority_case_citations
                   WHERE corpus_version='legacy-bootstrap'"""
            )
            assert cur.fetchone() == (None, "Legacy Reporter", "9", "99")
            cur.execute(
                """SELECT source_key, public_namespace
                     FROM authority_case_clusters
                    WHERE corpus_version='legacy-bootstrap'
                      AND cluster_id=98000001"""
            )
            assert cur.fetchone() == ("courtlistener:ohio-caselaw", "unknown")
            cur.execute(
                """SELECT COUNT(*) FROM authority_case_chunks
                   WHERE corpus_version IS NULL"""
            )
            assert cur.fetchone()[0] == 0
            assert (
                CourtListenerRepository(conn).search_caselaw("Legacy served opinion")
                == []
            )
            cur.execute(
                """UPDATE legal_sources
                      SET enabled=TRUE, storage_policy='normalized_text',
                          rights_decision='official', reviewed_at=now(),
                          reviewed_by='legacy-upgrade-reviewer',
                          expected_cadence='daily',
                          claim_safe_wording='Reviewed legacy caselaw fixture only',
                          metadata=metadata ||
                            '{"catalog_schema_version":"legacy-rehearsal",
                              "implementation_status":"legacy-bootstrap",
                              "manifest_reference":"legacy-bootstrap-manifest"}'::jsonb
                    WHERE source_key='courtlistener:ohio-caselaw'"""
            )
        conn.commit()
        # A legacy bootstrap is deliberately not inferred to be public.  The
        # reviewed admission applies to a side-by-side staged successor so an
        # already-promoted snapshot is never relabelled in place.
        reviewed_version = "legacy-reviewed-" + uuid.uuid4().hex
        stage_corpus_version(
            conn,
            version=reviewed_version,
            manifest_hash="legacy-bootstrap-reviewed",
            as_of="2026-08-31T00:00:00Z",
            actor="legacy-upgrade-reviewer",
            reason="review legacy bootstrap through a staged successor",
            embedding_model="mixedbread-ai/mxbai-embed-large-v1",
            embedding_version="0",
            embedding_dimension=1024,
        )
        admit_public_source(
            conn,
            source_key="courtlistener:ohio-caselaw",
            catalog_schema_version="legacy-rehearsal",
            manifest_reference="legacy-bootstrap-manifest",
            manifest_sha256="legacy-bootstrap-reviewed",
            reviewed_by="legacy-upgrade-reviewer",
        )
        with conn.cursor() as cur:
            cur.execute(
                """SELECT public_namespace FROM authority_case_clusters
                    WHERE corpus_version=%s
                      AND cluster_id=98000001""",
                [reviewed_version],
            )
            assert cur.fetchone() == ("public-authority",)
            cur.execute(
                """UPDATE authority_case_chunks
                      SET embedding=('[' || array_to_string(array_fill(0, ARRAY[1024]), ',') || ']')::vector,
                          embedding_model='mixedbread-ai/mxbai-embed-large-v1',
                          embedding_version='0'
                    WHERE corpus_version=%s""",
                [reviewed_version],
            )
        conn.commit()
        _declare_complete_partition(
            conn,
            version=reviewed_version,
            source_key="courtlistener:ohio-caselaw",
            partition_key="legacy-court",
            expected=1,
            observed=1,
        )
        assert materialize_citator_facts(conn, reviewed_version) >= 1
        conn.commit()
        _run_production_audits(
            conn,
            version=reviewed_version,
            auditor="legacy-upgrade-reviewer",
        )
        promote_corpus_version(
            conn,
            version=reviewed_version,
            actor="legacy-upgrade-reviewer",
            reason="promote reviewed legacy successor",
        )
        with conn.cursor() as cur:
            served = CourtListenerRepository(conn).search_caselaw(
                "Legacy served opinion"
            )
            assert served and served[0]["opinion_id"] == 98000001
        conn.commit()
    # Runtime singleton writes after the one-time migration are never adopted
    # into whichever release happens to be promoted on a later startup.
    with connect(scoped_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO opinion_clusters
                     (cluster_id, case_name, date_filed, citations)
                   VALUES (98000002, 'Late unversioned singleton', '2026-08-31', '[]')"""
            )
            cur.execute(
                """INSERT INTO opinions
                     (opinion_id, cluster_id, plain_text, source_url)
                   VALUES (98000002, 98000002, 'Late singleton text',
                           'https://legacy.example/late')"""
            )
            cur.execute(
                """INSERT INTO opinion_chunks
                     (opinion_id, cluster_id, chunk_index, content)
                   VALUES (98000002, 98000002, 0, 'Late singleton text')"""
            )
        conn.commit()
    # A populated upgrade is idempotent and must not duplicate immutable rows.
    init_schema(scoped_url)
    with connect(scoped_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) FROM authority_case_chunks
                   WHERE corpus_version='legacy-bootstrap'"""
            )
            assert cur.fetchone()[0] == 1
            cur.execute(
                """SELECT corpus_version FROM opinion_clusters
                    WHERE cluster_id=98000002"""
            )
            assert cur.fetchone() == (None,)
            cur.execute(
                """SELECT corpus_version FROM opinion_chunks
                    WHERE opinion_id=98000002"""
            )
            assert cur.fetchone() == (None,)
            cur.execute(
                """SELECT COUNT(*) FROM authority_case_clusters
                    WHERE cluster_id=98000002"""
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                """SELECT COUNT(*) FROM authority_records
                    WHERE authority_key='case:98000002'"""
            )
            assert cur.fetchone()[0] == 0
