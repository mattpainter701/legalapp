import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.control_plane import (  # noqa: E402
    audit_hash,
    cadence_seconds,
    coverage_claim,
    embedding_compatibility,
    lag_seconds,
    public_namespace,
    review_source,
    source_identity,
    sampled_audit,
)


def test_cadence_mapping_is_explicit_and_unknown_is_not_healthy():
    assert cadence_seconds("daily") == 86400
    assert cadence_seconds("weekly") == 604800
    assert cadence_seconds("monthly") == 2592000
    assert cadence_seconds("quarterly") == 7776000
    assert cadence_seconds("annual") == 31536000
    assert cadence_seconds("on_demand") is None
    assert cadence_seconds("unknown") is None


def test_public_namespace_rejects_private_sources():
    assert (
        public_namespace("uscourts:federal-rules", admitted=True)
        == "public-authority"
    )
    with pytest.raises(ValueError):
        public_namespace("custom-private:unreviewed")
    with pytest.raises(ValueError):
        public_namespace("tenant:firm-documents", admitted=True)


def test_source_identity_is_deduplicable_without_private_content():
    identity = source_identity("courtlistener:opinions", "42", "opinion text")
    assert identity["source_key"] == "courtlistener:opinions"
    assert len(identity["content_hash"]) == 64
    assert "opinion text" not in identity


def test_review_source_fails_closed_and_normalizes_scope():
    source = review_source({
        "source_key": "uscourts:rules",
        "enabled": True,
        "rights_decision": "official",
        "authority_tier": "binding_primary",
        "jurisdiction": "US",
        "claim_safe_wording": "Federal rules in the named edition only.",
    })
    assert source["source_tier"] == "binding_primary"
    assert source["geographic_scope"] == ["US"]
    assert source["claim_state"] == "supported"
    with pytest.raises(ValueError):
        review_source({"source_key": "x", "enabled": True, "rights_decision": "pending_review"})


def test_claim_state_suppresses_unaudited_or_failed_releases():
    source = {"rights_decision": "official", "claim_safe_wording": "Named source only."}
    assert coverage_claim(promoted=False, audit_passed=True, source=source, stale=False, failed=False)["state"] == "suppressed"
    assert coverage_claim(promoted=True, audit_passed=True, source=source, stale=True, failed=False)["state"] == "limited"
    assert coverage_claim(promoted=True, audit_passed=True, source=source, stale=False, failed=False)["state"] == "supported"


def test_embedding_mismatch_degrades_to_keyword_without_padding():
    result = embedding_compatibility(
        {"model": "mxbai", "version": 2, "dimension": 1024},
        {"model": "mxbai", "version": 1, "dimension": 1024},
    )
    assert result == {"compatible": False, "mode": "keyword", "reason": "embedding model/version/dimension mismatch"}


def test_lag_and_audit_hash_are_stable():
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    assert lag_seconds("2026-08-29T00:00:00Z", 3600, now) == 82800
    assert audit_hash({"b": 2, "a": 1}) == audit_hash({"a": 1, "b": 2})


def test_sampled_audits_compute_thresholds_instead_of_accepting_pass_flags():
    result = sampled_audit(
        [{"expected": True, "observed": True}, {"expected": True, "observed": False}],
        audit_kind="completeness",
        minimum_completeness=0.75,
    )
    assert result["ratio"] == 0.5
    assert result["passed"] is False

    freshness = sampled_audit(
        [{"lag_seconds": 10}, {"lag_seconds": 999}],
        audit_kind="freshness",
        maximum_lag_seconds=100,
    )
    assert freshness["fresh"] == 1
    assert freshness["passed"] is False


def test_release_audit_requires_every_sampled_criterion():
    assert sampled_audit(
        [{"ready": True}, {"ready": False}], audit_kind="release"
    )["passed"] is False
    result = sampled_audit(
        [{"ready": True}, {"ready": True}], audit_kind="release"
    )
    assert result["passed"] is True
    assert "manifest_bound_documents" in result["criteria"]
