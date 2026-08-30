import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.control_plane import (  # noqa: E402
    audit_hash,
    coverage_claim,
    embedding_compatibility,
    lag_seconds,
    public_namespace,
    review_source,
    source_identity,
)


def test_public_namespace_rejects_private_sources():
    assert public_namespace("uscourts:federal-rules") == "public-authority"
    with pytest.raises(ValueError):
        public_namespace("tenant:firm-documents")


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
