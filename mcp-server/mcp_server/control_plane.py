"""Pure policy and evidence helpers for the public-authority control plane.

This module intentionally contains no tenant identifiers, document text, or
private-corpus paths.  It is safe to use from ingestion, operator tooling, and
customer-facing coverage projections.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

RIGHTS_DECISIONS = {"official", "open", "licensed", "prohibited", "pending_review"}
CLAIM_STATES = {"supported", "limited", "suppressed"}
AUTHORITY_SCHEMA_VERSION = "authority-control-plane-v2"


def public_namespace(source_key: str) -> str:
    if not source_key or source_key.startswith(("tenant:", "firm:", "private:")):
        raise ValueError("private sources cannot enter the public authority namespace")
    return "public-authority"


def source_identity(source_key: str, external_id: str, content: str | bytes) -> dict[str, str]:
    if not source_key or not external_id:
        raise ValueError("source_key and external_id are required")
    raw = content if isinstance(content, bytes) else content.encode("utf-8")
    return {"source_key": source_key, "external_id": external_id,
            "content_hash": hashlib.sha256(raw).hexdigest()}


def review_source(source: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized reviewed manifest or fail closed."""
    decision = str(source.get("rights_decision") or "pending_review")
    if decision not in RIGHTS_DECISIONS:
        raise ValueError(f"unsupported rights decision: {decision}")
    enabled = bool(source.get("enabled"))
    if enabled and decision in {"prohibited", "pending_review"}:
        raise ValueError("unreviewed or prohibited sources cannot be enabled")
    if enabled and not source.get("claim_safe_wording"):
        raise ValueError("enabled sources require claim-safe customer wording")
    result = dict(source)
    result.update({"rights_decision": decision,
                   "source_tier": source.get("source_tier") or source.get("authority_tier"),
                   "geographic_scope": source.get("geographic_scope") or ([source["jurisdiction"]] if source.get("jurisdiction") else []),
                   "temporal_scope": source.get("temporal_scope") or {"start": source.get("coverage_start"), "end": source.get("coverage_end")},
                   "expected_cadence": source.get("expected_cadence") or source.get("sync_frequency"),
                   "completeness_caveats": source.get("completeness_caveats") or source.get("coverage_notes") or "Bounded source scope; completeness is not established.",
                   "claim_state": "supported" if enabled and decision in {"official", "open", "licensed"} else "suppressed"})
    return result


def coverage_claim(*, promoted: bool, audit_passed: bool, source: dict[str, Any], stale: bool, failed: bool) -> dict[str, str]:
    if not promoted or not audit_passed or failed or source.get("rights_decision") in {"prohibited", "pending_review"}:
        state = "suppressed"
    elif stale:
        state = "limited"
    else:
        state = "supported"
    wording = source.get("claim_safe_wording") or "Searchable excerpts from this reviewed source; scope and currentness are bounded."
    return {"state": state, "wording": wording if state != "suppressed" else "Coverage claim suppressed pending source, release, or audit evidence."}


def embedding_compatibility(query: dict[str, Any], corpus: dict[str, Any]) -> dict[str, Any]:
    exact = (query.get("model"), query.get("version"), query.get("dimension")) == (
        corpus.get("model"), corpus.get("version"), corpus.get("dimension"))
    return {"compatible": exact, "mode": "semantic" if exact else "keyword", "reason": None if exact else "embedding model/version/dimension mismatch"}


def audit_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def lag_seconds(last_successful: str | datetime | None, cadence_seconds: int | None, now: datetime | None = None) -> int | None:
    if not last_successful:
        return None
    observed = last_successful if isinstance(last_successful, datetime) else datetime.fromisoformat(last_successful.replace("Z", "+00:00"))
    observed = observed if observed.tzinfo else observed.replace(tzinfo=timezone.utc)
    return max(0, int(((now or datetime.now(timezone.utc)) - observed).total_seconds()) - int(cadence_seconds or 0))


def _authorized(actor: str | None, reason: str | None) -> tuple[str, str]:
    actor = (actor or "").strip()
    reason = (reason or "").strip()
    if not actor or not reason:
        raise PermissionError("operator identity and auditable reason are required")
    return actor[:200], reason[:1000]


def record_audit(conn: Any, *, corpus_version: str, audit_kind: str, methodology: str,
                 thresholds: dict[str, Any], result: dict[str, Any], passed: bool,
                 auditor: str, metadata: dict[str, Any] | None = None) -> str:
    actor, _ = _authorized(auditor, methodology)
    if bool(passed) != bool(result.get("passed")):
        raise ValueError("passed must match the computed audit result")
    immutable = audit_hash({"corpus_version": corpus_version, "audit_kind": audit_kind,
                            "methodology": methodology, "thresholds": thresholds,
                            "result": result, "passed": passed, "auditor": actor})
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO authority_audits
                (corpus_version, audit_kind, methodology, thresholds, result,
                 passed, auditor, immutable_hash, metadata)
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s::jsonb)
            """, [corpus_version, audit_kind, methodology, json.dumps(thresholds),
                   json.dumps(result), passed, actor, immutable, json.dumps(metadata or {})])
    conn.commit()
    return immutable


def promote_corpus_version(conn: Any, *, version: str, actor: str, reason: str) -> None:
    actor, reason = _authorized(actor, reason)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('authority-corpus-release'))")
        cur.execute("SELECT status FROM authority_corpus_versions WHERE version=%s", [version])
        target = cur.fetchone()
        if not target or target[0] not in {"staged", "canary"}:
            raise ValueError("corpus version is missing or not staged/canary")
        cur.execute("""
            SELECT COUNT(DISTINCT audit_kind) FROM authority_audits
            WHERE corpus_version = %s
              AND audit_kind IN ('release', 'completeness', 'freshness')
              AND passed = TRUE
            """, [version])
        audit_row = cur.fetchone()
        if not audit_row or audit_row[0] < 3:
            raise PermissionError("passing release, completeness, and freshness audits are required")
        cur.execute("UPDATE authority_corpus_versions SET status='retired' WHERE status='promoted' AND version <> %s", [version])
        cur.execute("""
            UPDATE authority_corpus_versions
            SET status='promoted', promoted_at=now(), reason=%s,
                metadata = metadata || %s::jsonb
            WHERE version=%s AND status IN ('staged','canary')
            """, [reason, json.dumps({"promoted_by": actor}), version])
        if cur.rowcount != 1:
            raise RuntimeError("corpus promotion transition was not applied")
    conn.commit()


def rollback_corpus_version(conn: Any, *, version: str, actor: str, reason: str) -> None:
    actor, reason = _authorized(actor, reason)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('authority-corpus-release'))")
        cur.execute("SELECT rollback_of FROM authority_corpus_versions WHERE version=%s AND status='promoted'", [version])
        row = cur.fetchone()
        if row is None or not row[0]:
            raise ValueError("only a promoted version with a recorded rollback target can be rolled back")
        cur.execute("UPDATE authority_corpus_versions SET status='rolled_back', rolled_back_at=now(), reason=%s WHERE version=%s", [reason, version])
        cur.execute("UPDATE authority_corpus_versions SET status='promoted', promoted_at=now(), metadata=metadata || %s::jsonb WHERE version=%s", [json.dumps({"rollback_by": actor}), row[0]])
    conn.commit()


def stage_corpus_version(conn: Any, *, version: str, manifest_hash: str,
                         as_of: str, actor: str, reason: str,
                         embedding_model: str, embedding_version: str,
                         embedding_dimension: int) -> None:
    """Create a staged immutable release and record its rollback target."""
    actor, reason = _authorized(actor, reason)
    if not manifest_hash or not as_of or embedding_dimension < 1:
        raise ValueError("manifest hash, as-of date, and embedding contract are required")
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM authority_corpus_versions WHERE status='promoted' ORDER BY promoted_at DESC NULLS LAST LIMIT 1")
        current = cur.fetchone()
        cur.execute("""
            INSERT INTO authority_corpus_versions
              (version, status, manifest_hash, as_of, rollback_of,
               embedding_model, embedding_version, embedding_dimension,
               reason, metadata)
            VALUES (%s, 'staged', %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (version) DO NOTHING
            """, [version, manifest_hash, as_of, current[0] if current else None,
                   embedding_model, embedding_version, embedding_dimension,
                   reason, json.dumps({"staged_by": actor})])
        if cur.rowcount != 1:
            raise ValueError("corpus version already exists")
        if current:
            # Seed a side-by-side candidate from the last good snapshot. New
            # harvest/chunk material can then replace only the candidate rows.
            cur.execute("""
                INSERT INTO authority_case_clusters
                  (corpus_version, cluster_id, docket_id, case_name, date_filed, citations)
                SELECT %s, cluster_id, docket_id, case_name, date_filed, citations
                FROM authority_case_clusters WHERE corpus_version=%s
                ON CONFLICT DO NOTHING
            """, [version, current[0]])
            cur.execute("""
                INSERT INTO authority_case_opinions
                  (corpus_version, opinion_id, source_url, plain_text)
                SELECT %s, opinion_id, source_url, plain_text
                FROM authority_case_opinions WHERE corpus_version=%s
                ON CONFLICT DO NOTHING
            """, [version, current[0]])
            cur.execute("""
                INSERT INTO authority_case_chunks
                  (corpus_version, opinion_id, cluster_id, court_id, chunk_index,
                   content, embedding, embedding_model, embedding_version)
                SELECT %s, opinion_id, cluster_id, court_id, chunk_index,
                       content, embedding, embedding_model, embedding_version
                FROM authority_case_chunks WHERE corpus_version=%s
                ON CONFLICT DO NOTHING
            """, [version, current[0]])
    conn.commit()


def sampled_audit(records: list[dict[str, Any]], *, audit_kind: str,
                  minimum_completeness: float = 0.95,
                  maximum_lag_seconds: int = 172800) -> dict[str, Any]:
    """Evaluate a bounded sample; callers cannot supply the pass/fail result."""
    if audit_kind not in {"release", "completeness", "freshness", "isolation"}:
        raise ValueError("unsupported sampled audit kind")
    total = len(records)
    if not total:
        return {"audit_kind": audit_kind, "sample_size": 0, "passed": False, "reason": "empty sample"}
    if audit_kind == "release":
        passed = bool(records) and all(bool(row.get("ready")) for row in records)
        return {"audit_kind": audit_kind, "sample_size": total, "passed": passed,
                "criteria": ["reviewed_sources", "no_failed_partitions", "manifest_bound_documents"]}
    if audit_kind == "completeness":
        observed = sum(1 for row in records if float(row.get("observed", 0) or 0) >= float(row.get("expected", 0) or 0))
        ratio = observed / total
        passed = ratio >= minimum_completeness
        return {"audit_kind": audit_kind, "sample_size": total, "observed": observed,
                "ratio": ratio, "threshold": minimum_completeness, "passed": passed}
    if audit_kind == "freshness":
        fresh = sum(1 for row in records if row.get("lag_seconds") is not None
                    and int(row["lag_seconds"]) <= maximum_lag_seconds)
        ratio = fresh / total
        return {"audit_kind": audit_kind, "sample_size": total, "fresh": fresh,
                "ratio": ratio, "max_lag_seconds": maximum_lag_seconds, "passed": ratio >= minimum_completeness}
    isolated = sum(1 for row in records if row.get("namespace") == "public-authority" and not row.get("private"))
    return {"audit_kind": audit_kind, "sample_size": total, "isolated": isolated,
            "passed": isolated == total}


def claim_embedding_shard(conn: Any, *, shard_key: str, worker_id: str, lease_seconds: int = 900) -> bool:
    """Atomically lease one shard; expired leases are reclaimable."""
    if not shard_key or not worker_id or lease_seconds < 1:
        raise ValueError("shard key, worker identity, and positive lease are required")
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE authority_embedding_shards
            SET status='leased', lease_owner=%s,
                lease_expires_at=now() + (%s * interval '1 second'),
                heartbeat_at=now(), attempts=attempts+1, updated_at=now()
            WHERE (shard_key=%s AND status IN ('queued','retryable'))
               OR (shard_key=%s AND status='leased' AND lease_expires_at < now())
            """, [worker_id, lease_seconds, shard_key, shard_key])
        claimed = cur.rowcount == 1
    conn.commit()
    return claimed


def heartbeat_embedding_shard(conn: Any, *, shard_key: str, worker_id: str,
                              lease_seconds: int = 900) -> bool:
    with conn.cursor() as cur:
        cur.execute("""UPDATE authority_embedding_shards SET heartbeat_at=now(),
                       lease_expires_at=now() + (%s * interval '1 second'), updated_at=now()
                       WHERE shard_key=%s AND status='leased' AND lease_owner=%s
                         AND lease_expires_at > now()""", [lease_seconds, shard_key, worker_id])
        ok = cur.rowcount == 1
    conn.commit()
    return ok


def finish_embedding_shard(conn: Any, *, shard_key: str, worker_id: str, success: bool,
                           error: str | None = None, throughput_per_minute: float | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute("""UPDATE authority_embedding_shards
                       SET status=CASE WHEN %s THEN 'complete'
                                      WHEN attempts >= 3 THEN 'dead_letter'
                                      ELSE 'retryable' END,
                           last_error=%s, dead_letter_reason=%s,
                           throughput_per_minute=%s, updated_at=now()
                       WHERE shard_key=%s AND status='leased' AND lease_owner=%s""",
                    [success, None if success else (error or "")[:2000],
                     None if success else (error or "")[:2000],
                     throughput_per_minute, shard_key, worker_id])
        if cur.rowcount != 1:
            raise PermissionError("shard lease is missing, expired, or owned by another worker")
    conn.commit()
