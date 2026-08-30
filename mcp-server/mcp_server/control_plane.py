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
        cur.execute("""
            SELECT 1 FROM authority_audits
            WHERE corpus_version = %s AND audit_kind IN ('release', 'completeness', 'freshness')
              AND passed = TRUE
            LIMIT 1
            """, [version])
        if cur.fetchone() is None:
            raise PermissionError("a passing release/completeness/freshness audit is required")
        cur.execute("UPDATE authority_corpus_versions SET status='retired' WHERE status='promoted'")
        cur.execute("""
            UPDATE authority_corpus_versions
            SET status='promoted', promoted_at=now(), reason=%s,
                metadata = metadata || %s::jsonb
            WHERE version=%s AND status IN ('staged','canary')
            """, [reason, json.dumps({"promoted_by": actor}), version])
        if cur.rowcount != 1:
            raise ValueError("corpus version is missing or not staged/canary")
    conn.commit()


def rollback_corpus_version(conn: Any, *, version: str, actor: str, reason: str) -> None:
    actor, reason = _authorized(actor, reason)
    with conn.cursor() as cur:
        cur.execute("SELECT rollback_of FROM authority_corpus_versions WHERE version=%s AND status='promoted'", [version])
        row = cur.fetchone()
        if row is None or not row[0]:
            raise ValueError("only a promoted version with a recorded rollback target can be rolled back")
        cur.execute("UPDATE authority_corpus_versions SET status='rolled_back', rolled_back_at=now(), reason=%s WHERE version=%s", [reason, version])
        cur.execute("UPDATE authority_corpus_versions SET status='promoted', promoted_at=now(), metadata=metadata || %s::jsonb WHERE version=%s", [json.dumps({"rollback_by": actor}), row[0]])
    conn.commit()
