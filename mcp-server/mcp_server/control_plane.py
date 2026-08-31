"""Pure policy and evidence helpers for the public-authority control plane.

This module intentionally contains no tenant identifiers, document text, or
private-corpus paths.  It is safe to use from ingestion, operator tooling, and
customer-facing coverage projections.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

RIGHTS_DECISIONS = {"official", "open", "licensed", "prohibited", "pending_review"}
CLAIM_STATES = {"supported", "limited", "suppressed"}
AUTHORITY_SCHEMA_VERSION = "authority-control-plane-v2"
CADENCE_SECONDS = {
    "hourly": 3600,
    "hour": 3600,
    "daily": 86400,
    "day": 86400,
    "weekly": 604800,
    "week": 604800,
    "monthly": 2592000,
    "month": 2592000,
    "quarterly": 7776000,
    "annual": 31536000,
}


def cadence_seconds(value: str | None) -> int | None:
    """Return only explicitly supported cadence names; unknown is not healthy."""
    key = str(value or "").strip().lower().replace(" ", "_")
    return CADENCE_SECONDS.get(key)


def public_namespace(source_key: str, *, admitted: bool = False) -> str:
    """Return the public label only after the canonical admission was verified.

    Source-key shape is not authorization.  Database callers must first resolve
    the source/version through ``public_authority_source_lineage`` and pass the
    resulting admission decision explicitly; arbitrary non-reserved keys fail
    closed just like tenant/private prefixes.
    """
    if (
        not admitted
        or not source_key
        or source_key.startswith(("tenant:", "firm:", "private:"))
    ):
        raise ValueError("reviewed public-source admission is required")
    return "public-authority"


def source_identity(
    source_key: str, external_id: str, content: str | bytes
) -> dict[str, str]:
    if not source_key or not external_id:
        raise ValueError("source_key and external_id are required")
    raw = content if isinstance(content, bytes) else content.encode("utf-8")
    return {
        "source_key": source_key,
        "external_id": external_id,
        "content_hash": hashlib.sha256(raw).hexdigest(),
    }


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
    result.update(
        {
            "rights_decision": decision,
            "source_tier": source.get("source_tier") or source.get("authority_tier"),
            "geographic_scope": source.get("geographic_scope")
            or ([source["jurisdiction"]] if source.get("jurisdiction") else []),
            "temporal_scope": source.get("temporal_scope")
            or {
                "start": source.get("coverage_start"),
                "end": source.get("coverage_end"),
            },
            "expected_cadence": source.get("expected_cadence")
            or source.get("sync_frequency"),
            "completeness_caveats": source.get("completeness_caveats")
            or source.get("coverage_notes")
            or "Bounded source scope; completeness is not established.",
            "claim_state": "supported"
            if enabled and decision in {"official", "open", "licensed"}
            else "suppressed",
        }
    )
    return result


def coverage_claim(
    *,
    promoted: bool,
    audit_passed: bool,
    source: dict[str, Any],
    stale: bool,
    failed: bool,
) -> dict[str, str]:
    if (
        not promoted
        or not audit_passed
        or failed
        or source.get("rights_decision") in {"prohibited", "pending_review"}
    ):
        state = "suppressed"
    elif stale:
        state = "limited"
    else:
        state = "supported"
    wording = (
        source.get("claim_safe_wording")
        or "Searchable excerpts from this reviewed source; scope and currentness are bounded."
    )
    return {
        "state": state,
        "wording": wording
        if state != "suppressed"
        else "Coverage claim suppressed pending source, release, or audit evidence.",
    }


def embedding_compatibility(
    query: dict[str, Any], corpus: dict[str, Any]
) -> dict[str, Any]:
    exact = (query.get("model"), query.get("version"), query.get("dimension")) == (
        corpus.get("model"),
        corpus.get("version"),
        corpus.get("dimension"),
    )
    return {
        "compatible": exact,
        "mode": "semantic" if exact else "keyword",
        "reason": None if exact else "embedding model/version/dimension mismatch",
    }


def audit_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def lag_seconds(
    last_successful: str | datetime | None,
    cadence_seconds: int | None,
    now: datetime | None = None,
) -> int | None:
    if not last_successful:
        return None
    observed = (
        last_successful
        if isinstance(last_successful, datetime)
        else datetime.fromisoformat(last_successful.replace("Z", "+00:00"))
    )
    observed = observed if observed.tzinfo else observed.replace(tzinfo=timezone.utc)
    return max(
        0,
        int(((now or datetime.now(timezone.utc)) - observed).total_seconds())
        - int(cadence_seconds or 0),
    )


def _authorized(actor: str | None, reason: str | None) -> tuple[str, str]:
    actor = (actor or "").strip()
    reason = (reason or "").strip()
    if not actor or not reason:
        raise PermissionError("operator identity and auditable reason are required")
    return actor[:200], reason[:1000]


def record_audit(
    conn: Any,
    *,
    corpus_version: str,
    audit_kind: str,
    methodology: str,
    thresholds: dict[str, Any],
    result: dict[str, Any],
    passed: bool,
    auditor: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    actor, _ = _authorized(auditor, methodology)
    if bool(passed) != bool(result.get("passed")):
        raise ValueError("passed must match the computed audit result")
    immutable = audit_hash(
        {
            "corpus_version": corpus_version,
            "audit_kind": audit_kind,
            "methodology": methodology,
            "thresholds": thresholds,
            "result": result,
            "passed": passed,
            "auditor": actor,
        }
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO authority_audits
                (corpus_version, audit_kind, methodology, thresholds, result,
                 passed, auditor, immutable_hash, metadata)
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s::jsonb)
            """,
            [
                corpus_version,
                audit_kind,
                methodology,
                json.dumps(thresholds),
                json.dumps(result),
                passed,
                actor,
                immutable,
                json.dumps(metadata or {}),
            ],
        )
    conn.commit()
    return immutable


def promote_corpus_version(conn: Any, *, version: str, actor: str, reason: str) -> None:
    actor, reason = _authorized(actor, reason)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext('authority-corpus-release'))"
        )
        cur.execute(
            "SELECT status FROM authority_corpus_versions WHERE version=%s", [version]
        )
        target = cur.fetchone()
        if not target or target[0] not in {"staged", "canary"}:
            raise ValueError("corpus version is missing or not staged/canary")
        cur.execute(
            """SELECT embedding_model, embedding_version, embedding_dimension
                 FROM authority_corpus_versions WHERE version=%s""",
            [version],
        )
        target_contract = cur.fetchone()
        if (
            not target_contract
            or any(value is None for value in target_contract)
            or not str(target_contract[0]).strip()
            or not str(target_contract[1]).strip()
        ):
            raise PermissionError("target corpus embedding contract is incomplete")
        cur.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT DISTINCT ON (audit_kind) audit_kind, passed
                FROM authority_audits
                WHERE corpus_version = %s
                  AND audit_kind IN ('release', 'completeness', 'freshness', 'isolation')
                ORDER BY audit_kind, sampled_at DESC, id DESC
            ) latest
            WHERE passed = TRUE
            """,
            [version],
        )
        audit_row = cur.fetchone()
        if not audit_row or audit_row[0] < 4:
            raise PermissionError(
                "latest passing release, completeness, freshness, and isolation audits are required"
            )
        cur.execute(
            """SELECT
                 (SELECT COUNT(*)
                    FROM legal_document_chunks c
                    JOIN legal_documents d ON d.id=c.document_id
                   WHERE d.corpus_version=%s AND c.corpus_version=%s)
                 +
                 (SELECT COUNT(*) FROM authority_case_chunks
                   WHERE corpus_version=%s)""",
            [version, version, version],
        )
        if not cur.fetchone()[0]:
            raise PermissionError(
                "a corpus version must contain searchable authority chunks"
            )
        cur.execute(
            """SELECT COUNT(*) FROM legal_document_chunks c
                 JOIN legal_documents d ON d.id=c.document_id
                WHERE d.corpus_version=%s
                  AND (c.corpus_version IS DISTINCT FROM %s
                       OR c.embedding IS NULL
                       OR c.embedding_model IS DISTINCT FROM %s
                       OR c.embedding_version::text IS DISTINCT FROM %s
                       OR vector_dims(c.embedding) IS DISTINCT FROM %s)""",
            [
                version,
                version,
                target_contract[0],
                target_contract[1],
                target_contract[2],
            ],
        )
        legal_invalid = cur.fetchone()[0]
        cur.execute(
            """SELECT COUNT(*) FROM authority_case_chunks
                WHERE corpus_version=%s
                  AND (embedding IS NULL
                       OR embedding_model IS DISTINCT FROM %s
                       OR embedding_version IS DISTINCT FROM %s
                       OR vector_dims(embedding) IS DISTINCT FROM %s)""",
            [version, target_contract[0], target_contract[1], target_contract[2]],
        )
        authority_invalid = cur.fetchone()[0]
        if legal_invalid or authority_invalid:
            raise PermissionError(
                "target corpus contains chunks without complete matching embeddings"
            )
        cur.execute(
            """
            SELECT COUNT(*) FROM authority_case_chunks ch
            LEFT JOIN authority_case_opinions op
              ON op.corpus_version=ch.corpus_version AND op.opinion_id=ch.opinion_id
            LEFT JOIN authority_case_clusters cl
              ON cl.corpus_version=ch.corpus_version AND cl.cluster_id=ch.cluster_id
            WHERE ch.corpus_version=%s AND (op.opinion_id IS NULL OR cl.cluster_id IS NULL)
        """,
            [version],
        )
        if cur.fetchone()[0]:
            raise PermissionError("corpus snapshot contains orphaned chunks")
        cur.execute(
            """SELECT COUNT(*)
                 FROM authority_case_clusters cl
                 JOIN legal_sources s ON s.source_key=cl.source_key
                WHERE cl.corpus_version=%s
                  AND (cl.public_namespace IS DISTINCT FROM 'public-authority'
                       OR NOT EXISTS (SELECT 1 FROM public_authority_source_lineage pas WHERE pas.source_key=cl.source_key AND pas.corpus_version=cl.corpus_version)
                       OR s.public_namespace IS DISTINCT FROM 'public-authority'
                       OR s.enabled IS DISTINCT FROM TRUE
                       OR s.storage_policy = 'prohibited'
                       OR s.rights_decision NOT IN ('official','open','licensed')
                       OR s.reviewed_at IS NULL OR s.reviewed_by IS NULL
                       OR cl.source_key IS NULL)""",
            [version],
        )
        if cur.fetchone()[0]:
            raise PermissionError(
                "corpus snapshot contains non-public authority lineage"
            )
        cur.execute(
            """SELECT COUNT(*)
                 FROM legal_documents d
                 JOIN legal_sources s ON s.source_key=d.source_key
                WHERE d.corpus_version=%s
                  AND (d.public_namespace IS DISTINCT FROM 'public-authority'
                       OR NOT EXISTS (SELECT 1 FROM public_authority_source_lineage pas WHERE pas.source_key=d.source_key AND pas.corpus_version=d.corpus_version)
                       OR s.public_namespace IS DISTINCT FROM 'public-authority'
                       OR s.enabled IS DISTINCT FROM TRUE
                       OR s.storage_policy = 'prohibited'
                       OR s.rights_decision NOT IN ('official','open','licensed')
                       OR s.reviewed_at IS NULL OR s.reviewed_by IS NULL
                       OR d.source_key IS NULL)""",
            [version],
        )
        if cur.fetchone()[0]:
            raise PermissionError(
                "corpus contains legal material without public authority lineage"
            )
        cur.execute(
            """SELECT COUNT(*)
                 FROM authority_records r
                WHERE r.corpus_version=%s
                  AND NOT EXISTS (
                    SELECT 1 FROM public_authority_source_lineage pas
                     WHERE pas.source_key=r.source_key
                       AND pas.corpus_version=r.corpus_version
                  )""",
            [version],
        )
        if cur.fetchone()[0]:
            raise PermissionError(
                "corpus contains citator records without public authority lineage"
            )
        cur.execute(
            """SELECT COUNT(*) FROM (
                 SELECT cp.source_key
                   FROM authority_harvest_checkpoints cp
                  WHERE cp.corpus_version=%s
                    AND NOT EXISTS (
                      SELECT 1 FROM public_authority_source_lineage pas
                       WHERE pas.source_key=cp.source_key
                         AND pas.corpus_version=cp.corpus_version)
                 UNION ALL
                 SELECT l.source_key
                   FROM corpus_coverage_ledger l
                  WHERE l.source_release=%s
                    AND NOT EXISTS (
                      SELECT 1 FROM public_authority_source_lineage pas
                       WHERE pas.source_key=l.source_key
                         AND pas.corpus_version=l.source_release)
               ) invalid_telemetry""",
            [version, version],
        )
        if cur.fetchone()[0]:
            raise PermissionError(
                "corpus contains telemetry without public authority lineage"
            )
        cur.execute(
            "UPDATE authority_corpus_versions SET status='retired' WHERE status='promoted' AND version <> %s",
            [version],
        )
        cur.execute(
            """
            UPDATE authority_corpus_versions
            SET status='promoted', promoted_at=now(), reason=%s,
                metadata = metadata || %s::jsonb
            WHERE version=%s AND status IN ('staged','canary')
            """,
            [reason, json.dumps({"promoted_by": actor}), version],
        )
        if cur.rowcount != 1:
            raise RuntimeError("corpus promotion transition was not applied")
    conn.commit()


def rollback_corpus_version(
    conn: Any, *, version: str, actor: str, reason: str
) -> None:
    actor, reason = _authorized(actor, reason)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext('authority-corpus-release'))"
        )
        cur.execute(
            "SELECT rollback_of FROM authority_corpus_versions WHERE version=%s AND status='promoted'",
            [version],
        )
        row = cur.fetchone()
        if row is None or not row[0]:
            raise ValueError(
                "only a promoted version with a recorded rollback target can be rolled back"
            )
        cur.execute(
            "UPDATE authority_corpus_versions SET status='rolled_back', rolled_back_at=now(), reason=%s WHERE version=%s",
            [reason, version],
        )
        cur.execute(
            "UPDATE authority_corpus_versions SET status='promoted', promoted_at=now(), metadata=metadata || %s::jsonb WHERE version=%s",
            [json.dumps({"rollback_by": actor}), row[0]],
        )
    conn.commit()


def stage_corpus_version(
    conn: Any,
    *,
    version: str,
    manifest_hash: str,
    as_of: str,
    actor: str,
    reason: str,
    embedding_model: str,
    embedding_version: str,
    embedding_dimension: int,
    inherit_current: bool = True,
) -> None:
    """Create a staged release and optionally seed it from the current snapshot.

    ``inherit_current=False`` is the explicit full-rebuild path.  It avoids
    copying immutable deterministic evidence when an operator is assembling a
    replacement corpus entirely from reviewed source inputs.
    """
    actor, reason = _authorized(actor, reason)
    if (
        not manifest_hash
        or not as_of
        or not str(embedding_model or "").strip()
        or not str(embedding_version or "").strip()
        or embedding_dimension != 1024
    ):
        raise ValueError(
            "manifest hash, as-of date, and 1024-dimensional embedding contract are required"
        )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT version FROM authority_corpus_versions WHERE status='promoted' ORDER BY promoted_at DESC NULLS LAST LIMIT 1"
        )
        current = cur.fetchone()
        cur.execute(
            """
            INSERT INTO authority_corpus_versions
              (version, status, manifest_hash, as_of, rollback_of,
               embedding_model, embedding_version, embedding_dimension,
               reason, metadata)
            VALUES (%s, 'staged', %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (version) DO NOTHING
            """,
            [
                version,
                manifest_hash,
                as_of,
                current[0] if current else None,
                embedding_model,
                embedding_version,
                embedding_dimension,
                reason,
                json.dumps({"staged_by": actor}),
            ],
        )
        if cur.rowcount != 1:
            raise ValueError("corpus version already exists")
        if current and inherit_current:
            # Seed a side-by-side candidate from the last good snapshot. New
            # harvest/chunk material can then replace only the candidate rows.
            cur.execute(
                """
                INSERT INTO authority_case_clusters
                  (corpus_version, source_key, public_namespace, cluster_id,
                   docket_id, case_name, date_filed, citations)
                SELECT %s, source_key, public_namespace, cluster_id,
                       docket_id, case_name, date_filed, citations
                FROM authority_case_clusters WHERE corpus_version=%s
                ON CONFLICT DO NOTHING
            """,
                [version, current[0]],
            )
            cur.execute(
                """
                INSERT INTO authority_case_opinions
                  (corpus_version, opinion_id, cluster_id, source_url, plain_text)
                SELECT %s, opinion_id, cluster_id, source_url, plain_text
                FROM authority_case_opinions WHERE corpus_version=%s
                ON CONFLICT DO NOTHING
            """,
                [version, current[0]],
            )
            cur.execute(
                """
                INSERT INTO authority_case_chunks
                  (corpus_version, opinion_id, cluster_id, court_id, chunk_index,
                   content, embedding, embedding_model, embedding_version)
                SELECT %s, opinion_id, cluster_id, court_id, chunk_index,
                       content, embedding, embedding_model, embedding_version
                FROM authority_case_chunks WHERE corpus_version=%s
                ON CONFLICT DO NOTHING
            """,
                [version, current[0]],
            )
            cur.execute(
                """
                INSERT INTO authority_case_citations
                  (corpus_version, citing_opinion_id, cited_opinion_id,
                   cited_cluster_id, cited_reporter, cited_volume, cited_page, depth)
                SELECT %s, citing_opinion_id, cited_opinion_id, cited_cluster_id,
                       cited_reporter, cited_volume, cited_page, depth
                FROM authority_case_citations WHERE corpus_version=%s
                ON CONFLICT DO NOTHING
            """,
                [version, current[0]],
            )
            # Deterministic citator facts are copied with the candidate
            # snapshot.  Derived treatment is intentionally not copied: it
            # must be recomputed/reviewed against the candidate's as-of state.
            cur.execute(
                """
                INSERT INTO authority_records
                  (corpus_version, authority_key, authority_kind, source_key,
                   canonical_citation, title, court, decision_date, effective_date,
                   repeal_date, source_url, source_as_of, source_version,
                   currentness_state, deterministic_metadata)
                SELECT %s, authority_key, authority_kind, source_key,
                       canonical_citation, title, court, decision_date, effective_date,
                       repeal_date, source_url, source_as_of, %s,
                       currentness_state, deterministic_metadata
                FROM authority_records WHERE corpus_version=%s
                ON CONFLICT DO NOTHING
                """,
                [version, version, current[0]],
            )
            cur.execute(
                """
                INSERT INTO authority_history_facts
                  (corpus_version, authority_key, fact_kind, related_authority_key,
                   court, event_date, source_url, evidence_span, evidence_locator,
                   source_hash, observed_at, metadata)
                SELECT %s, authority_key, fact_kind, related_authority_key,
                       court, event_date, source_url, evidence_span, evidence_locator,
                       source_hash, observed_at, metadata
                FROM authority_history_facts WHERE corpus_version=%s
                ON CONFLICT DO NOTHING
                """,
                [version, current[0]],
            )
            cur.execute(
                """
                INSERT INTO authority_citation_facts
                  (corpus_version, citing_authority_key, cited_authority_key,
                   citing_opinion_id, cited_opinion_id, depth, issue_context,
                   source_url, evidence_span, evidence_locator, source_hash,
                   observed_at, metadata)
                SELECT %s, citing_authority_key, cited_authority_key,
                       citing_opinion_id, cited_opinion_id, depth, issue_context,
                       source_url, evidence_span, evidence_locator, source_hash,
                       observed_at, metadata
                FROM authority_citation_facts WHERE corpus_version=%s
                ON CONFLICT DO NOTHING
                """,
                [version, current[0]],
            )
    conn.commit()


def sampled_audit(
    records: list[dict[str, Any]],
    *,
    audit_kind: str,
    minimum_completeness: float = 0.95,
    maximum_lag_seconds: int = 172800,
) -> dict[str, Any]:
    """Evaluate a bounded sample; callers cannot supply the pass/fail result."""
    if audit_kind not in {"release", "completeness", "freshness", "isolation"}:
        raise ValueError("unsupported sampled audit kind")
    total = len(records)
    if not total:
        return {
            "audit_kind": audit_kind,
            "sample_size": 0,
            "passed": False,
            "reason": "empty sample",
        }
    if audit_kind == "release":
        passed = bool(records) and all(bool(row.get("ready")) for row in records)
        return {
            "audit_kind": audit_kind,
            "sample_size": total,
            "passed": passed,
            "criteria": [
                "reviewed_sources",
                "no_failed_partitions",
                "manifest_bound_documents",
            ],
        }
    if audit_kind == "completeness":
        observed = sum(
            1
            for row in records
            if row.get("declared", True)
            and float(row.get("expected", 0) or 0) > 0
            and float(row.get("observed", 0) or 0) >= float(row.get("expected", 0) or 0)
        )
        ratio = observed / total
        passed = ratio >= minimum_completeness
        return {
            "audit_kind": audit_kind,
            "sample_size": total,
            "observed": observed,
            "ratio": ratio,
            "threshold": minimum_completeness,
            "passed": passed,
        }
    if audit_kind == "freshness":
        fresh = sum(
            1
            for row in records
            if row.get("lag_seconds") is not None
            and int(row["lag_seconds"]) <= maximum_lag_seconds
        )
        ratio = fresh / total
        return {
            "audit_kind": audit_kind,
            "sample_size": total,
            "fresh": fresh,
            "ratio": ratio,
            "max_lag_seconds": maximum_lag_seconds,
            "passed": ratio >= minimum_completeness,
        }
    isolated = sum(
        1
        for row in records
        if row.get("namespace") == "public-authority" and not row.get("private")
    )
    return {
        "audit_kind": audit_kind,
        "sample_size": total,
        "isolated": isolated,
        "passed": isolated == total,
    }


def claim_embedding_shard(
    conn: Any, *, shard_key: str, worker_id: str, lease_seconds: int = 900
) -> bool:
    """Atomically lease one shard; expired leases are reclaimable."""
    if not shard_key or not worker_id or lease_seconds < 1:
        raise ValueError("shard key, worker identity, and positive lease are required")
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE authority_embedding_shards
            SET status='leased', lease_owner=%s,
                lease_expires_at=now() + (%s * interval '1 second'),
                heartbeat_at=now(), attempts=attempts+1, updated_at=now()
            WHERE (shard_key=%s AND status IN ('queued','retryable'))
               OR (shard_key=%s AND status='leased' AND lease_expires_at < now())
            """,
            [worker_id, lease_seconds, shard_key, shard_key],
        )
        claimed = cur.rowcount == 1
    conn.commit()
    return claimed


def heartbeat_embedding_shard(
    conn: Any, *, shard_key: str, worker_id: str, lease_seconds: int = 900
) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE authority_embedding_shards SET heartbeat_at=now(),
                       lease_expires_at=now() + (%s * interval '1 second'), updated_at=now()
                       WHERE shard_key=%s AND status='leased' AND lease_owner=%s
                         AND lease_expires_at > now()""",
            [lease_seconds, shard_key, worker_id],
        )
        ok = cur.rowcount == 1
    conn.commit()
    return ok


def finish_embedding_shard(
    conn: Any,
    *,
    shard_key: str,
    worker_id: str,
    success: bool,
    error: str | None = None,
    throughput_per_minute: float | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE authority_embedding_shards
                       SET status=CASE WHEN %s THEN 'complete'
                                      WHEN attempts >= 3 THEN 'dead_letter'
                                      ELSE 'retryable' END,
                           last_error=%s, dead_letter_reason=%s,
                           throughput_per_minute=%s, updated_at=now()
                       WHERE shard_key=%s AND status='leased' AND lease_owner=%s
                         AND lease_expires_at > now()""",
            [
                success,
                None if success else (error or "")[:2000],
                None if success else (error or "")[:2000],
                throughput_per_minute,
                shard_key,
                worker_id,
            ],
        )
        if cur.rowcount != 1:
            raise PermissionError(
                "shard lease is missing, expired, or owned by another worker"
            )
    conn.commit()


TREATMENT_LABELS = {"negative", "positive", "distinguished", "no_decision", "unknown"}
TREATMENT_REVIEW_DECISIONS = {
    "accepted",
    "overridden",
    "rejected",
    "needs_more_evidence",
}
_CITATOR_SCOPE_MAX_SECONDS = 300
_CITATOR_REVIEWER_PURPOSE = "citator:reviewer:authorize"
_CITATOR_WATCH_PURPOSE = "citator:watch:save"


def _decode_assertion_part(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _canonical_command_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _consume_citator_command_assertion(
    conn: Any,
    assertion: str,
    *,
    signer_secret_name: str,
    purpose: str,
    expected_actor: str,
    expected_body: dict[str, Any],
    tenant_id: str | None = None,
    matter_id: str | None = None,
    max_seconds: int,
) -> None:
    """Verify and atomically consume a body-bound backend command assertion."""
    signer_secret = os.getenv(signer_secret_name, "")
    if len(signer_secret) < 32:
        raise PermissionError("citator command assertion signing is not configured")
    try:
        encoded_payload, encoded_signature = assertion.split(".", 1)
        payload = _decode_assertion_part(encoded_payload)
        signature = _decode_assertion_part(encoded_signature)
        claims = json.loads(payload)
        if not isinstance(claims, dict):
            raise ValueError("assertion claims must be an object")
        issued = int(claims["issued"])
        expires = int(claims["expires"])
        nonce = str(claims["nonce"])
        body_sha256 = str(claims["body_sha256"])
        actor = str(claims["actor"])
    except (KeyError, ValueError, TypeError, binascii.Error, json.JSONDecodeError):
        raise PermissionError("invalid citator command assertion") from None
    expected = hmac.new(signer_secret.encode(), payload, hashlib.sha256).digest()
    now = int(time.time())
    if (
        not hmac.compare_digest(signature, expected)
        or not nonce
        or claims.get("purpose") != purpose
        or actor != expected_actor
        or issued > now + 30
        or expires <= now
        or expires <= issued
        or expires - issued > max_seconds
        or body_sha256 != _canonical_command_hash(expected_body)
        or (tenant_id is not None and str(claims.get("tenant_id")) != str(tenant_id))
        or (matter_id is not None and str(claims.get("matter_id")) != str(matter_id))
    ):
        raise PermissionError(
            "invalid, expired, or mismatched citator command assertion"
        )
    with conn.cursor() as cur:
        cur.execute("DELETE FROM citator_command_assertions WHERE expires_at < now()")
        cur.execute(
            """INSERT INTO citator_command_assertions
                   (nonce, purpose, actor, credential_id, tenant_id, matter_id,
                    body_sha256, issued_at, expires_at)
               VALUES (%s, %s, %s, %s, %s::uuid, %s::uuid, %s,
                       to_timestamp(%s), to_timestamp(%s))
               ON CONFLICT (nonce) DO NOTHING RETURNING nonce""",
            [
                nonce,
                purpose,
                actor,
                str(claims.get("credential") or "") or None,
                tenant_id,
                matter_id,
                body_sha256,
                issued,
                expires,
            ],
        )
        if cur.fetchone() is None:
            raise PermissionError("replayed citator command assertion")


def _consume_citator_matter_scope(
    conn: Any,
    assertion: str,
    *,
    tenant_id: str,
    matter_id: str,
    principal: str,
    authority_key: str,
    delivery_channels: list[str],
    quiet_hours: dict[str, Any],
) -> None:
    """Require a short-lived backend assertion of canonical matter ownership.

    The public-authority database intentionally does not mirror LawHand's
    private ``matters`` table.  It therefore cannot infer that an arbitrary
    UUID belongs to a tenant.  A backend service must first make that
    authoritative lookup and then mint this HMAC-bound assertion.  Absence,
    expiry, a malformed signature, or any identity mismatch fails closed.
    """
    _consume_citator_command_assertion(
        conn,
        assertion,
        signer_secret_name="MCP_CITATOR_SCOPE_ASSERTION_SECRET",
        purpose=_CITATOR_WATCH_PURPOSE,
        expected_actor=principal,
        expected_body={
            "action": "save",
            "tenant_id": str(tenant_id),
            "matter_id": str(matter_id),
            "principal": principal,
            "authority_key": authority_key,
            "delivery_channels": delivery_channels,
            "quiet_hours": quiet_hours,
        },
        tenant_id=tenant_id,
        matter_id=matter_id,
        max_seconds=_CITATOR_SCOPE_MAX_SECONDS,
    )


def _citator_authority_is_permitted(
    conn: Any, *, corpus_version: str, authority_key: str
) -> bool:
    """Require a reviewed public source, never infer public status from a URL."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM authority_records r
            JOIN authority_corpus_versions v ON v.version=r.corpus_version
            JOIN public_authority_source_lineage pas
              ON pas.source_key=r.source_key AND pas.corpus_version=r.corpus_version
            WHERE r.corpus_version=%s AND r.authority_key=%s
              AND v.status='promoted'
              AND r.currentness_state='current'
            """,
            [corpus_version, authority_key],
        )
        row = cur.fetchone()
    return bool(row)


def record_treatment_assessment(
    conn: Any,
    *,
    corpus_version: str,
    authority_key: str,
    treatment_label: str,
    confidence: float,
    policy_version: str,
    evidence_fact_ids: list[str],
    model_version: str | None = None,
    summary: str | None = None,
    abstained: bool = False,
    abstention_reason: str | None = None,
    actor: str,
    stale_at: datetime | None = None,
) -> str:
    """Append a machine assessment only after source/evidence validation.

    The caller may record an abstention. It may not replace that abstention with
    a default positive result, and it may not submit a custom/private source
    through the public authority namespace.
    """
    actor, _ = _authorized(actor, "citator assessment")
    if treatment_label not in TREATMENT_LABELS:
        raise ValueError("unsupported treatment label")
    if not 0 <= float(confidence) <= 1:
        raise ValueError("confidence must be between 0 and 1")
    if not policy_version.strip():
        raise ValueError("policy version is required")
    if abstained:
        if not abstention_reason:
            raise ValueError("an abstention reason is required")
        treatment_label = "unknown"
    elif not evidence_fact_ids:
        raise ValueError("non-abstaining treatment requires linked source evidence")
    if not _citator_authority_is_permitted(
        conn, corpus_version=corpus_version, authority_key=authority_key
    ):
        raise PermissionError(
            "citator assessment requires promoted, reviewed public authority evidence"
        )
    bound_evidence: list[dict[str, Any]] = []
    if not abstained:
        fact_ids = list(dict.fromkeys(str(value) for value in evidence_fact_ids))
        if len(fact_ids) != len(evidence_fact_ids):
            raise ValueError("treatment evidence fact IDs must not be duplicated")
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text AS fact_id, source_url, evidence_span,
                       evidence_locator, source_hash, 'history' AS fact_type
                FROM authority_history_facts
                WHERE corpus_version=%s AND authority_key=%s AND id::text = ANY(%s)
                UNION ALL
                SELECT id::text AS fact_id, source_url, evidence_span,
                       evidence_locator, source_hash, 'citation' AS fact_type
                FROM authority_citation_facts
                WHERE corpus_version=%s AND cited_authority_key=%s AND id::text = ANY(%s)
                """,
                [
                    corpus_version,
                    authority_key,
                    fact_ids,
                    corpus_version,
                    authority_key,
                    fact_ids,
                ],
            )
            columns = [column.name for column in cur.description]
            bound_evidence = [dict(zip(columns, row)) for row in cur.fetchall()]
        if len(bound_evidence) != len(fact_ids):
            raise PermissionError(
                "every treatment evidence fact must belong to the same authority and corpus version"
            )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO authority_treatment_assessments
              (corpus_version, authority_key, treatment_label, summary, confidence,
               abstained, abstention_reason, evidence_fact_ids, evidence_links,
               model_version, policy_version, assessment_state, stale_at, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                    %s, %s, %s, %s, %s::jsonb)
            RETURNING id::text
            """,
            [
                corpus_version,
                authority_key,
                treatment_label,
                (summary or "")[:4000] or None,
                float(confidence),
                abstained,
                (abstention_reason or "")[:1000] or None,
                json.dumps(fact_ids if not abstained else []),
                json.dumps(bound_evidence),
                (model_version or "")[:200] or None,
                policy_version[:200],
                "abstained" if abstained else "provisional",
                stale_at,
                json.dumps({"recorded_by": actor}),
            ],
        )
        row = cur.fetchone()
    conn.commit()
    return str(row[0])


def authorize_citator_reviewer(
    conn: Any,
    *,
    principal: str,
    authorization_basis: str,
    actor: str,
    authorization_assertion: str,
) -> None:
    """Register an internal attorney-review principal with an audit basis.

    No public Research MCP caller can perform this operation. Until an
    authenticated tenant/RBAC adapter registers a principal, treatment review
    is unavailable rather than treating a display name as legal authority.
    """
    if not principal.strip() or not authorization_basis.strip():
        raise ValueError("reviewer principal and authorization basis are required")
    actor, _ = _authorized(actor, "citator reviewer authorization")
    _consume_citator_command_assertion(
        conn,
        authorization_assertion,
        signer_secret_name="MCP_OPERATOR_ASSERTION_SECRET",
        purpose=_CITATOR_REVIEWER_PURPOSE,
        expected_actor=actor,
        expected_body={
            "action": "authorize_reviewer",
            "principal": principal.strip(),
            "authorization_basis": authorization_basis.strip()[:1000],
        },
        max_seconds=60,
    )
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO authority_reviewer_principals
                   (principal, authorization_basis, active, verified_by, revoked_at, metadata)
               VALUES (%s, %s, TRUE, %s, NULL, %s::jsonb)
               ON CONFLICT (principal) DO UPDATE SET
                 authorization_basis=EXCLUDED.authorization_basis, active=TRUE,
                 verified_by=EXCLUDED.verified_by, verified_at=now(), revoked_at=NULL,
                 metadata=EXCLUDED.metadata""",
            [
                principal.strip(),
                authorization_basis.strip()[:1000],
                actor,
                json.dumps({"authorization_contract": "internal-rbac-principal"}),
            ],
        )
    conn.commit()


def review_treatment_assessment(
    conn: Any,
    *,
    assessment_id: str,
    reviewer: str,
    decision: str,
    override_label: str | None = None,
    note: str | None = None,
) -> str:
    """Append, rather than overwrite, an attorney review/override decision."""
    reviewer, _ = _authorized(reviewer, "citator treatment review")
    if decision not in TREATMENT_REVIEW_DECISIONS:
        raise ValueError("unsupported treatment review decision")
    if decision == "overridden" and override_label not in TREATMENT_LABELS:
        raise ValueError("an override treatment label is required")
    with conn.cursor() as cur:
        cur.execute(
            """SELECT active, authorization_basis FROM authority_reviewer_principals
                 WHERE principal=%s""",
            [reviewer],
        )
        authorized = cur.fetchone()
        if not authorized or not authorized[0] or not authorized[1]:
            raise PermissionError(
                "reviewer is not an authorized citator review principal"
            )
        # Reviewing a previously-created assessment is still a public
        # authority mutation.  Re-check the current promoted lineage in the
        # INSERT statement so a source revocation that races the review cannot
        # preserve an apparently valid attorney decision for suppressed data.
        cur.execute(
            """INSERT INTO authority_treatment_reviews
                 (assessment_id, reviewer, decision, override_label, note, metadata)
               SELECT a.id, %s, %s, %s, %s, %s::jsonb
                 FROM authority_treatment_assessments a
                 JOIN authority_records r
                   ON r.corpus_version=a.corpus_version
                  AND r.authority_key=a.authority_key
                 JOIN authority_corpus_versions v
                   ON v.version=a.corpus_version AND v.status='promoted'
                 JOIN public_authority_source_lineage pas
                   ON pas.source_key=r.source_key
                  AND pas.corpus_version=r.corpus_version
                WHERE a.id=%s::uuid
                  AND r.currentness_state='current'
               RETURNING id::text""",
            [
                reviewer,
                decision,
                override_label if decision == "overridden" else None,
                (note or "")[:4000] or None,
                json.dumps(
                    {
                        "review_workflow": "attorney_review_first",
                        "authorization_basis": authorized[1],
                        "principal": reviewer,
                    }
                ),
                assessment_id,
            ],
        )
        row = cur.fetchone()
        if not row:
            raise PermissionError(
                "treatment review requires current promoted public authority evidence"
            )
    conn.commit()
    return str(row[0])


def save_citator_watch(
    conn: Any,
    *,
    tenant_id: str,
    matter_id: str | None,
    authority_key: str,
    created_by: str,
    delivery_channels: list[str],
    quiet_hours: dict[str, Any] | None = None,
    matter_scope_assertion: str = "",
) -> str:
    """Create an idempotent consented watch under explicit tenant RLS context."""
    if not tenant_id or not matter_id or not authority_key or not created_by:
        raise ValueError("tenant, matter, authority, and actor are required")
    if not delivery_channels:
        raise ValueError("at least one consented alert channel is required")
    normalized_quiet_hours = quiet_hours or {}
    _consume_citator_matter_scope(
        conn,
        matter_scope_assertion,
        tenant_id=tenant_id,
        matter_id=matter_id,
        principal=created_by,
        authority_key=authority_key,
        delivery_channels=delivery_channels,
        quiet_hours=normalized_quiet_hours,
    )
    with conn.cursor() as cur:
        cur.execute(
            """SELECT version FROM authority_corpus_versions
                 WHERE status='promoted' ORDER BY promoted_at DESC NULLS LAST LIMIT 1"""
        )
        promoted = cur.fetchone()
    if not promoted or not _citator_authority_is_permitted(
        conn, corpus_version=str(promoted[0]), authority_key=authority_key
    ):
        raise PermissionError(
            "watches require promoted, reviewed public authority evidence"
        )
    with conn.cursor() as cur:
        cur.execute("SET LOCAL app.current_tenant_id = %s", [tenant_id])
        cur.execute(
            """
            INSERT INTO citator_watches
              (tenant_id, matter_id, authority_key, created_by, consented,
               delivery_channels, quiet_hours, state)
            VALUES (%s::uuid, %s::uuid, %s, %s, TRUE, %s::jsonb, %s::jsonb, 'active')
            ON CONFLICT (tenant_id, matter_id, authority_key, created_by)
            DO UPDATE SET delivery_channels=EXCLUDED.delivery_channels,
                          quiet_hours=EXCLUDED.quiet_hours,
                          consented=TRUE,
                          state='active', revoked_at=NULL, deleted_at=NULL
            RETURNING id::text, (xmax = 0)
            """,
            [
                tenant_id,
                matter_id,
                authority_key,
                created_by,
                json.dumps(delivery_channels),
                json.dumps(normalized_quiet_hours),
            ],
        )
        row = cur.fetchone()
        cur.execute(
            """INSERT INTO citator_watch_audits
                 (watch_id, tenant_id, action, actor, detail)
               VALUES (%s::uuid, %s::uuid, %s, %s, %s::jsonb)""",
            [
                row[0],
                tenant_id,
                "created" if row[1] else "updated",
                created_by,
                json.dumps(
                    {
                        "authority_key": authority_key,
                        "delivery_channels": delivery_channels,
                    }
                ),
            ],
        )
    conn.commit()
    return str(row[0])


def revoke_citator_watch(conn: Any, *, tenant_id: str, watch_id: str) -> bool:
    """Revoke delivery before any further alert attempt; preserve an audit-safe row."""
    with conn.cursor() as cur:
        cur.execute("SET LOCAL app.current_tenant_id = %s", [tenant_id])
        cur.execute(
            """UPDATE citator_watches SET state='revoked', consented=FALSE,
                       revoked_at=now() WHERE id=%s::uuid AND tenant_id=%s::uuid
                       AND state <> 'deleted'""",
            [watch_id, tenant_id],
        )
        changed = cur.rowcount == 1
        if changed:
            cur.execute(
                """INSERT INTO citator_watch_audits
                     (watch_id, tenant_id, action, actor, detail)
                   VALUES (%s::uuid, %s::uuid, 'revoked', 'system-or-user', '{}'::jsonb)""",
                [watch_id, tenant_id],
            )
    conn.commit()
    return changed


def _citator_alert_evidence(
    conn: Any, *, corpus_version: str, authority_key: str, evidence_fact_id: str
) -> tuple[str, dict[str, Any]]:
    """Bind alert provenance to a persisted fact, never caller URL/payload JSON."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text AS fact_id, source_url, evidence_span, evidence_locator,
                   source_hash, 'history' AS fact_type
            FROM authority_history_facts
            WHERE corpus_version=%s AND authority_key=%s AND id::text=%s
            UNION ALL
            SELECT id::text AS fact_id, source_url, evidence_span, evidence_locator,
                   source_hash, 'citation' AS fact_type
            FROM authority_citation_facts
            WHERE corpus_version=%s AND cited_authority_key=%s AND id::text=%s
            """,
            [
                corpus_version,
                authority_key,
                evidence_fact_id,
                corpus_version,
                authority_key,
                evidence_fact_id,
            ],
        )
        columns = [column.name for column in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    if len(rows) != 1 or not rows[0].get("source_url"):
        raise PermissionError(
            "citator alert requires a stored same-version authority evidence fact"
        )
    evidence = rows[0]
    return str(evidence["source_url"]), {
        "evidence_fact_id": evidence["fact_id"],
        "evidence_type": evidence["fact_type"],
        "evidence_span": evidence["evidence_span"],
        "evidence_locator": evidence["evidence_locator"],
        "source_hash": evidence["source_hash"],
    }


def enqueue_citator_alert(
    conn: Any,
    *,
    tenant_id: str,
    watch_id: str,
    corpus_version: str,
    authority_key: str,
    event_fingerprint: str,
    event_kind: str,
    evidence_fact_id: str,
) -> str | None:
    """Append a deduplicated alert only for an active consented watch.

    A ``None`` result is an intentional suppression (revoked, paused, or no
    consent), not a failed delivery that could be retried into a revoked watch.
    """
    if event_kind not in {"history", "treatment", "currentness", "source_gap"}:
        raise ValueError("unsupported citator alert kind")
    if not _citator_authority_is_permitted(
        conn, corpus_version=corpus_version, authority_key=authority_key
    ):
        raise PermissionError(
            "alerts require promoted, reviewed public authority evidence"
        )
    source_url, payload = _citator_alert_evidence(
        conn,
        corpus_version=corpus_version,
        authority_key=authority_key,
        evidence_fact_id=evidence_fact_id,
    )
    with conn.cursor() as cur:
        cur.execute("SET LOCAL app.current_tenant_id = %s", [tenant_id])
        cur.execute(
            """SELECT authority_key FROM citator_watches
                WHERE id=%s::uuid AND tenant_id=%s::uuid
                  AND state='active' AND consented IS TRUE
                FOR UPDATE""",
            [watch_id, tenant_id],
        )
        watch = cur.fetchone()
        if not watch or watch[0] != authority_key:
            conn.commit()
            return None
        cur.execute(
            """INSERT INTO citator_alert_events
                 (watch_id, tenant_id, corpus_version, authority_key,
                  event_fingerprint, event_kind, source_url, payload)
               VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s::jsonb)
               ON CONFLICT (watch_id, event_fingerprint) DO NOTHING
               RETURNING id::text""",
            [
                watch_id,
                tenant_id,
                corpus_version,
                authority_key,
                event_fingerprint,
                event_kind,
                source_url,
                json.dumps(payload),
            ],
        )
        row = cur.fetchone()
    conn.commit()
    return str(row[0]) if row else None


def quiet_hours_active(
    quiet_hours: dict[str, Any] | None, now: datetime | None = None
) -> bool:
    """Evaluate an optional, explicit wall-clock quiet window.

    Bad or partial configuration is fail-safe for delivery: it is treated as a
    quiet window rather than guessing a customer timezone.
    """
    quiet_hours = quiet_hours or {}
    if not quiet_hours:
        return False
    try:
        start = str(quiet_hours["start"])
        end = str(quiet_hours["end"])
        zone = ZoneInfo(str(quiet_hours.get("timezone") or "UTC"))
        start_minutes = int(start[:2]) * 60 + int(start[3:5])
        end_minutes = int(end[:2]) * 60 + int(end[3:5])
        local = (now or datetime.now(timezone.utc)).astimezone(zone)
        minute = local.hour * 60 + local.minute
    except (KeyError, ValueError, IndexError):
        return True
    if start_minutes == end_minutes:
        return True
    return (
        start_minutes <= minute < end_minutes
        if start_minutes < end_minutes
        else minute >= start_minutes or minute < end_minutes
    )


def record_citator_alert_delivery(
    conn: Any,
    *,
    tenant_id: str,
    alert_event_id: str,
    channel: str,
    delivery_key: str,
    attempted_outcome: str = "queued",
    detail: str | None = None,
    now: datetime | None = None,
) -> str:
    """Append an idempotent delivery attempt after consent/revocation recheck.

    This persistence primitive never contacts a customer. A separately
    authorized sender can call it around an actual send, but cannot bypass the
    quiet-hour or revocation outcome stored here.
    """
    if attempted_outcome not in {"queued", "sent", "failed"}:
        raise ValueError("attempted alert outcome must be queued, sent, or failed")
    with conn.cursor() as cur:
        cur.execute("SET LOCAL app.current_tenant_id = %s", [tenant_id])
        cur.execute(
            """
            SELECT w.state, w.consented, w.quiet_hours, w.delivery_channels,
                   e.corpus_version, e.authority_key
            FROM citator_alert_events e
            JOIN citator_watches w ON w.id=e.watch_id
            WHERE e.id=%s::uuid AND e.tenant_id=%s::uuid
            FOR UPDATE OF w
            """,
            [alert_event_id, tenant_id],
        )
        watch = cur.fetchone()
        if not watch or watch[0] in {"revoked", "deleted"}:
            outcome = "revoked"
        elif not _citator_authority_is_permitted(
            conn, corpus_version=str(watch[4]), authority_key=str(watch[5])
        ):
            outcome = "revoked"
        elif channel not in set(watch[3] or []):
            raise PermissionError("alert channel is not consented for this watch")
        elif not watch[1]:
            outcome = "suppressed_no_consent"
        elif quiet_hours_active(watch[2], now=now):
            outcome = "suppressed_quiet_hours"
        else:
            outcome = attempted_outcome
        cur.execute(
            """
            INSERT INTO citator_alert_deliveries
              (alert_event_id, tenant_id, channel, delivery_key, outcome, detail)
            VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s)
            ON CONFLICT (alert_event_id, channel, delivery_key) DO NOTHING
            RETURNING id::text
            """,
            [
                alert_event_id,
                tenant_id,
                channel[:100],
                delivery_key[:200],
                outcome,
                (detail or "")[:2000] or None,
            ],
        )
        row = cur.fetchone()
    conn.commit()
    return str(row[0]) if row else outcome
