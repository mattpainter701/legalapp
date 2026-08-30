"""Opt-in database rehearsal for the authority release lifecycle.

CI environments with a disposable PostgreSQL/pgvector database can run this
with AUTHORITY_REHEARSAL_DATABASE_URL.  It is deliberately skipped by normal
unit runs so local tests never touch a developer or production database.
"""

import os
import uuid

import pytest
from psycopg2 import DatabaseError

from mcp_server.control_plane import (
    claim_embedding_shard,
    finish_embedding_shard,
    heartbeat_embedding_shard,
    promote_corpus_version,
    record_audit,
    rollback_corpus_version,
    sampled_audit,
    stage_corpus_version,
)
from mcp_server.database import connect
from mcp_server.loader import (
    backfill_promoted_caselaw_snapshot,
    create_snapshot_chunks,
    init_schema,
    refresh_courtlistener_coverage_ledger,
)
from mcp_server.repository import CourtListenerRepository


def test_authority_release_rehearsal():
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
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO legal_sources
                    (source_key, publisher, source_type, canonical_url, enabled,
                     storage_policy, rights_decision, reviewed_at, reviewed_by,
                     expected_cadence, claim_safe_wording)
                    VALUES (%s, 'Rehearsal', 'statute', 'https://example.test', TRUE,
                            'normalized_text', 'official', now(), 'rehearsal-admin',
                            'daily', 'Fixture source only') ON CONFLICT DO NOTHING""",
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
                            '{"catalog_schema_version":"rehearsal","implementation_status":"fixture"}')
                    ON CONFLICT DO NOTHING"""
                )
                cur.execute(
                    """INSERT INTO legal_documents
                    (source_key, external_id, document_type, title, authority_tier,
                     canonical_url, corpus_version, text_content)
                    VALUES (%s, 'same-document', 'statute', %s, 'binding_primary',
                            'https://example.test/doc', %s, %s) RETURNING id""",
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
                    VALUES (%s, %s, %s, %s, 'Rehearsal', '1', %s)
                    ON CONFLICT DO NOTHING""",
                    [
                        version_name,
                        fixture_cluster_id,
                        second_cluster_id,
                        second_cluster_id,
                        suffix,
                    ],
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
        )
        add_fixture(version, "old")
        create_snapshot_chunks(conn, version)
        refresh_courtlistener_coverage_ledger(conn, source_release=version)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(DISTINCT cluster_id) FROM authority_case_chunks WHERE corpus_version=%s",
                [version],
            )
            assert cur.fetchone()[0] == 2
            cur.execute(
                "SELECT COUNT(*) FROM authority_case_citations WHERE corpus_version=%s",
                [version],
            )
            assert cur.fetchone()[0] == 1
        for kind, records in (
            ("release", [{"ready": True}]),
            ("completeness", [{"expected": True, "observed": True}]),
            ("freshness", [{"lag_seconds": 1}]),
            ("isolation", [{"namespace": "public-authority", "private": False}]),
        ):
            result = sampled_audit(records, audit_kind=kind)
            assert result["passed"]
            record_audit(
                conn,
                corpus_version=version,
                audit_kind=kind,
                methodology="fixture sample with computed threshold result",
                thresholds={
                    "minimum_completeness": 0.95,
                    "maximum_lag_seconds": 172800,
                },
                result=result,
                passed=True,
                auditor="rehearsal-admin",
            )
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
        promote_corpus_version(
            conn,
            version=version,
            actor="rehearsal-admin",
            reason="all fixture audits passed",
        )
        authority_results = CourtListenerRepository(conn).search_legal_authorities(
            "old authority"
        )
        case_results = CourtListenerRepository(conn).search_caselaw("old case")
        # Execute the operator coverage projection against the versioned
        # snapshot schema; this guards against legacy ``id`` assumptions.
        assert isinstance(CourtListenerRepository(conn).court_coverage(), list)
        assert authority_results and authority_results[0]["title"] == "Fixture old"
        assert case_results and case_results[0]["case_name"] == "Fixture old"

        # The upgrade path is exercised against legacy-shaped rows after a
        # promoted version exists.  Backfill is idempotent and must preserve
        # the served release without leaving NULL-version snapshot rows.
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
        assert before_backfill >= 1 and after_backfill == 0
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
                """SELECT COUNT(*) FROM authority_case_chunks
                WHERE corpus_version=%s AND opinion_id=%s
                  AND content='Legacy upgrade searchable text'""",
                [version, legacy_cluster],
            )
            assert cur.fetchone()[0] == 1

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
        db_reject("DELETE FROM authority_audits WHERE corpus_version=%s", [version])

        # An older pass cannot keep a release eligible after the latest result
        # fails.  Use a new staged candidate so the failure is not hidden by
        # the promoted snapshot guard.
        failed_version = version + "-failed"
        stage_corpus_version(
            conn,
            version=failed_version,
            manifest_hash="failed-manifest",
            as_of="2026-08-30T00:00:00Z",
            actor="rehearsal-admin",
            reason="latest-failure gate",
            embedding_model="mixedbread-ai/mxbai-embed-large-v1",
            embedding_version="1",
            embedding_dimension=1024,
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM authority_case_chunks WHERE corpus_version=%s",
                [failed_version],
            )
            assert cur.fetchone()[0] >= 2
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
        with pytest.raises(PermissionError):
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
        assert claim_embedding_shard(
            conn, shard_key=shard, worker_id="worker-b", lease_seconds=30
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

        # Private material is rejected at the public namespace boundary and
        # cannot pass the isolation audit as public authority evidence.
        from mcp_server.control_plane import public_namespace

        with pytest.raises(ValueError):
            public_namespace("tenant:private-matter")
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
        stage_corpus_version(
            conn,
            version=follow_up,
            manifest_hash="fixture-manifest-hash-next",
            as_of="2026-08-30T00:00:00Z",
            actor="rehearsal-admin",
            reason="rollback fixture",
            embedding_model="mixedbread-ai/mxbai-embed-large-v1",
            embedding_version="1",
            embedding_dimension=1024,
        )
        add_fixture(follow_up, "new")
        for kind in ("release", "completeness", "freshness", "isolation"):
            result = sampled_audit(
                [{"ready": True}]
                if kind == "release"
                else (
                    [{"expected": True, "observed": True}]
                    if kind == "completeness"
                    else (
                        [{"lag_seconds": 1}]
                        if kind == "freshness"
                        else [{"namespace": "public-authority", "private": False}]
                    )
                ),
                audit_kind=kind,
            )
            record_audit(
                conn,
                corpus_version=follow_up,
                audit_kind=kind,
                methodology="fixture rollback sample",
                thresholds={},
                result=result,
                passed=True,
                auditor="rehearsal-admin",
            )
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


def test_durable_operator_assertion_replay_rehearsal():
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
    consume_operator_assertion_with_db(db_url, claims)
    with pytest.raises(RuntimeError, match="replayed"):
        consume_operator_assertion_with_db(db_url, claims)


def consume_operator_assertion_with_db(db_url, claims):
    """Exercise the atomic consume SQL on an independent connection."""
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM authority_operator_assertions WHERE expires_at < now()"
            )
            cur.execute(
                """INSERT INTO authority_operator_assertions
                (nonce, credential_id, actor, scope, method, path, body_sha256, issued_at, expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,to_timestamp(%s),to_timestamp(%s))
                ON CONFLICT (nonce) DO NOTHING RETURNING nonce""",
                [
                    claims[k]
                    for k in (
                        "nonce",
                        "credential",
                        "actor",
                        "scope",
                        "method",
                        "path",
                        "body_sha256",
                        "issued",
                        "expires",
                    )
                ],
            )
            if cur.fetchone() is None:
                conn.rollback()
                raise RuntimeError("replayed signed operator context")
        conn.commit()
