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
from mcp_server.loader import init_schema


def test_authority_release_rehearsal():
    db_url = os.getenv("AUTHORITY_REHEARSAL_DATABASE_URL")
    if not db_url:
        pytest.skip("set AUTHORITY_REHEARSAL_DATABASE_URL for the disposable DB rehearsal")

    init_schema(db_url)
    version = "rehearsal-authority-" + uuid.uuid4().hex
    with connect(db_url) as conn:
        stage_corpus_version(
            conn, version=version, manifest_hash="fixture-manifest-hash",
            as_of="2026-08-30T00:00:00Z", actor="rehearsal-admin",
            reason="disposable production-shaped rehearsal",
            embedding_model="mixedbread-ai/mxbai-embed-large-v1",
            embedding_version="1", embedding_dimension=1024,
        )
        for kind, records in (
            ("release", [{"ready": True}]),
            ("completeness", [{"expected": True, "observed": True}]),
            ("freshness", [{"lag_seconds": 1}]),
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
        for kind in ("release", "completeness", "freshness"):
            result = sampled_audit([{"ready": True}] if kind == "release" else
                                   ([{"expected": True, "observed": True}] if kind == "completeness" else [{"lag_seconds": 1}]),
                                   audit_kind=kind)
            record_audit(conn, corpus_version=follow_up, audit_kind=kind,
                         methodology="fixture rollback sample", thresholds={}, result=result,
                         passed=True, auditor="rehearsal-admin")
        promote_corpus_version(conn, version=follow_up, actor="rehearsal-admin", reason="cutover fixture")
        rollback_corpus_version(conn, version=follow_up, actor="rehearsal-admin", reason="bad release fixture")
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM authority_corpus_versions WHERE status='promoted'")
            assert cur.fetchone()[0] == version
