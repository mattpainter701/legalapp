"""Focused citator contracts: source isolation, review state, and safe alerts."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.control_plane import (  # noqa: E402
    quiet_hours_active,
    record_treatment_assessment,
    save_citator_watch,
)
from mcp_server.repository import CourtListenerRepository  # noqa: E402
from mcp_server.schema import SCHEMA_SQL  # noqa: E402
from mcp_server.tools import TOOL_NAMES, build_tool_manifest  # noqa: E402


class EmptyCursor:
    description = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *_args, **_kwargs):
        return None

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class EmptyConnection:
    def cursor(self):
        return EmptyCursor()


def test_citator_schema_separates_snapshot_facts_from_machine_interpretation():
    assert "CREATE TABLE IF NOT EXISTS authority_records" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS authority_history_facts" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS authority_citation_facts" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS authority_treatment_assessments" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS authority_treatment_reviews" in SCHEMA_SQL
    assert "citator evidence is append-only" in SCHEMA_SQL
    assert "authority.snapshot_backfill" in SCHEMA_SQL
    loader = (ROOT / "mcp_server" / "loader.py").read_text()
    assert "backfill_promoted_citator_facts" in loader
    assert "d.termination_date IS NOT NULL" in loader
    assert "JOIN public_authority_source_lineage pas" in loader


def test_citator_watch_schema_has_tenant_rls_dedupe_and_revocation_states():
    assert "CREATE TABLE IF NOT EXISTS citator_watches" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS citator_alert_events" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS citator_watch_audits" in SCHEMA_SQL
    assert "ENABLE ROW LEVEL SECURITY" in SCHEMA_SQL
    assert "app.current_tenant_id" in SCHEMA_SQL
    assert "UNIQUE (watch_id, event_fingerprint)" in SCHEMA_SQL
    assert "citator_alert_events_watch_tenant_fk" in SCHEMA_SQL
    assert "citator_alert_deliveries_event_tenant_fk" in SCHEMA_SQL
    assert "suppressed_no_consent" in SCHEMA_SQL
    assert "revoked" in SCHEMA_SQL
    assert "authority_reviewer_principals" in SCHEMA_SQL
    assert "citator_command_assertions" in SCHEMA_SQL


def test_treatment_refuses_an_unsupported_inference_before_touching_storage():
    with pytest.raises(ValueError, match="linked source evidence"):
        record_treatment_assessment(
            EmptyConnection(),
            corpus_version="fixture",
            authority_key="case:1",
            treatment_label="positive",
            confidence=0.8,
            policy_version="citator-policy-v1",
            evidence_fact_ids=[],
            actor="review-worker",
        )


def test_watch_requires_matter_scope_and_consent_channel_before_database_access():
    with pytest.raises(ValueError, match="tenant, matter"):
        save_citator_watch(
            EmptyConnection(),
            tenant_id="tenant",
            matter_id=None,
            authority_key="case:1",
            created_by="user",
            delivery_channels=["in_app"],
        )


def test_watch_scope_assertion_fails_closed_when_unconfigured_or_invalid(monkeypatch):
    args = dict(
        tenant_id="tenant",
        matter_id="matter",
        authority_key="case:1",
        created_by="principal",
        delivery_channels=["in_app"],
        matter_scope_assertion="not-an-assertion",
    )
    monkeypatch.delenv("MCP_CITATOR_SCOPE_ASSERTION_SECRET", raising=False)
    with pytest.raises(PermissionError, match="not configured"):
        save_citator_watch(EmptyConnection(), **args)
    monkeypatch.setenv("MCP_CITATOR_SCOPE_ASSERTION_SECRET", "c" * 48)
    with pytest.raises(PermissionError, match="invalid"):
        save_citator_watch(EmptyConnection(), **args)
    with pytest.raises(ValueError, match="consented alert channel"):
        save_citator_watch(
            EmptyConnection(),
            tenant_id="tenant",
            matter_id="matter",
            authority_key="case:1",
            created_by="user",
            delivery_channels=[],
        )


def test_quiet_hours_suppress_delivery_across_midnight_and_for_invalid_configuration():
    assert quiet_hours_active(
        {"start": "22:00", "end": "07:00", "timezone": "UTC"},
        now=datetime(2026, 8, 30, 23, 0, tzinfo=timezone.utc),
    )
    assert not quiet_hours_active(
        {"start": "22:00", "end": "07:00", "timezone": "UTC"},
        now=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
    )
    assert quiet_hours_active({"start": "bad"})


def test_unknown_authority_returns_incomplete_not_good_law():
    result = CourtListenerRepository(EmptyConnection()).authority_treatment(123)

    assert result["status"] == "unavailable"
    assert result["machine_interpretation"]["treatment_label"] == "unknown"
    assert "No good-law" in result["claim"]
    assert "public URL" in result["limitations"][1]


def test_manifest_publishes_citator_read_contract_with_limitations():
    manifest = build_tool_manifest()
    tools = {tool["name"]: tool for tool in manifest["tools"]}

    assert "get_citator_status" in TOOL_NAMES
    assert "get_citator_status" in tools
    assert "good-law" in tools["get_authority_treatment"]["description"]
    assert tools["get_citator_status"]["inputSchema"]["required"] == []
