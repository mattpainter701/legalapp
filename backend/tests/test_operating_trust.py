"""Production-shaped customer lifecycle rehearsal for COMP-04."""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.database import set_tenant_context
from app.models.compliance import AgreementDefinition
from app.models.external_import import ExternalImportRun, ExternalSystemConnection
from app.services import platform_auth
from app.services.operating_trust import (
    assert_public_safe_text,
    assert_safe_evidence,
    database_export_mode,
    issue_tenant_export_snapshot,
    reconcile_counts,
    support_acknowledgement_due,
    verify_tenant_export_snapshot,
)
from tests.platform_auth_helpers import TEST_PLATFORM_SIGNING_KEY, platform_headers


def _operator_headers(actor: str) -> dict[str, str]:
    platform_auth.settings.PLATFORM_TOKEN_SIGNING_KEY = TEST_PLATFORM_SIGNING_KEY
    token, _, _ = platform_auth.issue_platform_token(
        subject=actor,
        scopes=["platform:write"],
        allowed_scopes=["platform:write"],
    )
    return {"Authorization": f"Bearer {token}"}


def test_reconciliation_and_public_safety_fail_closed():
    assert reconcile_counts({"matters": 2}, {"matters": 1}) == [
        {"category": "matters", "expected": 2, "actual": 1, "delta": -1}
    ]
    with pytest.raises(ValueError):
        assert_public_safe_text("database password=hunter2 on 10.0.0.4")
    assert reconcile_counts({"empty": 0}, {})[0]["reason"] == "missing_category"


def test_evidence_safety_distinguishes_timestamps_from_private_addresses():
    assert_safe_evidence({"created_at": "2026-08-30T01:47:10.123456+00:00"})
    for address in (
        "10.0.0.4",
        "127.0.0.1",
        "169.254.1.2",
        "172.31.4.5",
        "192.168.1.2",
    ):
        with pytest.raises(ValueError):
            assert_safe_evidence({"summary": f"internal host {address}"})
    with pytest.raises(ValueError, match="prohibited field"):
        assert_safe_evidence({"encrypted_auth_token": "ciphertext"})


def test_sms_export_modes_are_explicit_and_unknown_sms_tables_fail_closed():
    assert database_export_mode("sms_messages") == "existing-product-export-path"
    assert (
        database_export_mode("sms_number_suppressions")
        == "existing-product-export-path"
    )
    for table in (
        "sms_consent_events",
        "sms_number_suppression_events",
        "sms_review_items",
    ):
        assert database_export_mode(table) == "immutable-evidence-summary"
    assert (
        database_export_mode("sms_provider_configs")
        == "security-metadata-only-no-secret-values"
    )
    with pytest.raises(ValueError, match="unclassified"):
        database_export_mode("sms_future_sensitive_store")


def test_tenant_export_snapshot_is_signed_and_tenant_bound():
    tenant_id = uuid.uuid4()
    inventory = {
        "schema": "lawhand.tenant-export-inventory",
        "counts": {"database:matters": 2},
        "categories": [
            {
                "category": "database:matters",
                "record_count": 2,
                "export_mode": "existing-product-export-path",
            }
        ],
        "tenant_table_count": 1,
        "providers": [],
        "retention_policy_version": 3,
        "legal_hold": False,
        "boundary": "Point-in-time test inventory.",
    }
    issued = issue_tenant_export_snapshot(
        inventory, tenant_id=tenant_id, contract_version="test-contract"
    )
    verified = verify_tenant_export_snapshot(
        issued["snapshot_token"],
        tenant_id=tenant_id,
        contract_version="test-contract",
    )
    assert verified["inventory"]["counts"] == inventory["counts"]
    assert verified["snapshot_sha256"] == issued["snapshot_sha256"]
    token = issued["snapshot_token"]
    token_index = len(token) // 2
    tampered_token = (
        token[:token_index]
        + ("A" if token[token_index] != "A" else "B")
        + token[token_index + 1 :]
    )
    with pytest.raises(ValueError):
        verify_tenant_export_snapshot(
            tampered_token,
            tenant_id=tenant_id,
            contract_version="test-contract",
        )
    with pytest.raises(ValueError):
        verify_tenant_export_snapshot(
            token,
            tenant_id=uuid.uuid4(),
            contract_version="test-contract",
        )
    malformed = {
        **inventory,
        "categories": [
            {
                **inventory["categories"][0],
                "export_mode": "unbounded-secret-export",
            }
        ],
    }
    with pytest.raises(ValueError, match="malformed"):
        issue_tenant_export_snapshot(
            malformed, tenant_id=tenant_id, contract_version="test-contract"
        )


def test_support_clock_pauses_outside_published_s2_hours():
    friday = datetime(2026, 8, 28, 16, 0, tzinfo=ZoneInfo("America/Chicago"))
    due = support_acknowledgement_due(friday, severity="S2", objective_minutes=240)
    assert due.astimezone(ZoneInfo("America/Chicago")) == datetime(
        2026, 8, 31, 11, 0, tzinfo=ZoneInfo("America/Chicago")
    )


def test_operating_trust_migration_forces_rls_and_immutable_ledgers():
    source = (
        Path(__file__).parents[1] / "migrations" / "versions" / "143_operating_trust.py"
    ).read_text()
    for table in (
        "customer_lifecycle_receipts",
        "support_requests",
        "offboarding_cases",
        "offboarding_approvals",
    ):
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in source.replace(
            "{table}", table
        )
    for table in (
        "customer_lifecycle_receipts",
        "offboarding_approvals",
        "public_incidents",
        "public_incident_updates",
    ):
        assert table in source
    assert "reject_operating_trust_ledger_mutation" in source


@pytest.mark.asyncio
async def test_support_and_public_incident_lifecycle(client, test_tenant):
    opened = await client.post(
        "/api/compliance/operating/support",
        json={
            "severity": "S1",
            "channel": "customer support",
            "subject": "Production access unavailable",
            "safe_summary": "Authorized users cannot reach the application.",
        },
    )
    assert opened.status_code == 200, opened.text
    request_id = opened.json()["id"]
    assert opened.json()["acknowledgement_objective_minutes"] == 60

    acknowledged = await client.patch(
        f"/api/platform/operating-trust/tenants/{test_tenant.id}/support/{request_id}",
        headers=platform_headers(["platform:write"]),
        json={"status": "acknowledged", "escalation_level": 1},
    )
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["status"] == "acknowledged"

    incident = await client.post(
        "/api/platform/operating-trust/incidents",
        headers=platform_headers(["platform:write"]),
        json={
            "title": "Application access degraded",
            "severity": "S1",
            "affected_services": ["LawHand application"],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "message": "We are investigating reports of failed application access.",
        },
    )
    assert incident.status_code == 200, incident.text
    public_id = incident.json()["id"]
    assert incident.json()["state"] == "investigating"

    resolved = await client.post(
        f"/api/platform/operating-trust/incidents/{public_id}/updates",
        headers=platform_headers(["platform:write"]),
        json={
            "state": "resolved",
            "message": "Access has recovered and monitoring is complete.",
        },
    )
    assert resolved.status_code == 200, resolved.text

    status = await client.get("/api/public/status")
    assert status.status_code == 200
    assert status.json()["published_incident_state"] == "none_active"
    assert status.json()["service_health"] == "not_asserted_by_incident_ledger"
    assert status.json()["recent_incidents"][0]["state"] == "resolved"


@pytest.mark.asyncio
async def test_bk28_migration_and_tenant_export_receipts_reconcile(
    client, db_session, test_tenant, test_user
):
    await set_tenant_context(db_session, str(test_tenant.id))
    now = datetime.now(timezone.utc)
    definition = AgreementDefinition(
        kind="subscription-terms",
        version="rehearsal-1",
        title="Rehearsal subscription terms",
        document_url="https://example.invalid/terms/rehearsal-1",
        content_hash="d" * 64,
        required_for_onboarding=True,
        effective_at=now - timedelta(days=1),
        counsel_owned=True,
        published_by_actor_id="test-counsel",
    )
    db_session.add(definition)
    await db_session.commit()
    accepted = await client.post(
        "/api/compliance/agreements/subscription-terms/accept",
        json={
            "expected_version": "rehearsal-1",
            "expected_content_hash": "d" * 64,
            "signer_name": "Test Attorney",
            "signer_title": "Firm administrator",
            "authority_attested": True,
        },
    )
    assert accepted.status_code == 200, accepted.text
    onboarding = await client.post(
        "/api/compliance/operating/receipts",
        json={
            "receipt_type": "onboarding",
            "scope": {"deployment": "supported hosted topology"},
            "actual_counts": {},
            "signer_title": "Firm administrator",
            "authority_attested": True,
            "outcome": "Current required terms and onboarding scope accepted.",
        },
    )
    assert onboarding.status_code == 200, onboarding.text
    assert onboarding.json()["status"] == "accepted"
    assert onboarding.json()["actual_counts"]["agreement_acceptances"] == 1

    await set_tenant_context(db_session, str(test_tenant.id))
    connection = ExternalSystemConnection(
        tenant_id=test_tenant.id,
        provider="tabs3",
        external_key="comp04-rehearsal",
        display_name="COMP-04 migration rehearsal",
        created_by_user_id=test_user.id,
    )
    db_session.add(connection)
    await db_session.flush()
    run = ExternalImportRun(
        tenant_id=test_tenant.id,
        connection_id=connection.id,
        provider="tabs3",
        source_system="Tabs3",
        status="staged",
        row_counts={"clients": 2, "matters": 3},
        checksum_summary={"bundle": "b" * 64},
        warnings=[],
        errors=[],
        created_by_user_id=test_user.id,
    )
    db_session.add(run)
    await db_session.commit()

    migration = await client.post(
        "/api/compliance/operating/receipts",
        json={
            "receipt_type": "migration",
            "scope": {"accepted_tables": ["clients", "matters"]},
            "actual_counts": {"clients": 2, "matters": 3},
            "source_import_run_id": str(run.id),
            "signer_title": "Firm administrator",
            "authority_attested": True,
            "outcome": "Staged records reconciled to the accepted source manifest.",
        },
    )
    assert migration.status_code == 200, migration.text
    assert migration.json()["status"] == "accepted"
    assert migration.json()["discrepancies"] == []
    assert migration.json()["source_import_run_id"] == str(run.id)

    inventory = await client.get("/api/compliance/operating/export-inventory")
    assert inventory.status_code == 200, inventory.text
    counts = inventory.json()["counts"]
    snapshot_token = inventory.json()["snapshot_token"]
    snapshot_sha256 = inventory.json()["snapshot_sha256"]
    assert inventory.json()["snapshot_max_age_seconds"] == 3600
    assert inventory.json()["tenant_table_count"] > 50
    assert "database:external_import_runs" in counts
    assert "database:tenant_agreement_acceptances" in counts
    assert "database:customer_lifecycle_receipts" in counts
    assert "file-store:local-references" in counts
    category_modes = {
        item["category"]: item["export_mode"] for item in inventory.json()["categories"]
    }
    assert category_modes["database:users"] == "security-metadata-only-no-secret-values"
    assert (
        category_modes["database:customer_lifecycle_receipts"]
        == "immutable-evidence-summary"
    )
    assert category_modes["database:sms_messages"] == "existing-product-export-path"
    assert (
        category_modes["database:sms_number_suppressions"]
        == "existing-product-export-path"
    )
    for category in (
        "database:sms_consent_events",
        "database:sms_number_suppression_events",
        "database:sms_review_items",
    ):
        assert category_modes[category] == "immutable-evidence-summary"
    assert (
        category_modes["database:sms_provider_configs"]
        == "security-metadata-only-no-secret-values"
    )
    token_index = len(snapshot_token) // 2
    tampered_token = (
        snapshot_token[:token_index]
        + ("A" if snapshot_token[token_index] != "A" else "B")
        + snapshot_token[token_index + 1 :]
    )
    rejected_export = await client.post(
        "/api/compliance/operating/receipts",
        json={
            "receipt_type": "tenant_export",
            "scope": {"format": "customer-authorized export bundle"},
            "actual_counts": counts,
            "artifact_reference": "tenant-export:rehearsal-001",
            "artifact_sha256": "a" * 64,
            "inventory_snapshot_token": tampered_token,
            "signer_title": "Firm administrator",
            "authority_attested": True,
            "outcome": "Every declared inventory category reconciled to the export manifest.",
        },
    )
    assert rejected_export.status_code == 409
    export = await client.post(
        "/api/compliance/operating/receipts",
        json={
            "receipt_type": "tenant_export",
            "scope": {"format": "customer-authorized export bundle"},
            "actual_counts": counts,
            "artifact_reference": "tenant-export:rehearsal-001",
            "artifact_sha256": "a" * 64,
            "inventory_snapshot_token": snapshot_token,
            "signer_title": "Firm administrator",
            "authority_attested": True,
            "outcome": "Every declared inventory category reconciled to the export manifest.",
        },
    )
    assert export.status_code == 200, export.text
    assert export.json()["status"] == "completed"
    assert export.json()["expected_counts"] == counts
    assert export.json()["scope"]["inventory_snapshot_sha256"] == snapshot_sha256
    assert "snapshot_token" not in export.json()["scope"]
    assert export.json()["receipt_hash"]


@pytest.mark.asyncio
async def test_offboarding_requires_no_hold_two_operators_and_disposition_proof(
    client, test_tenant
):
    tenant_id = test_tenant.id
    requested = await client.post(
        "/api/compliance/operating/offboarding",
        json={
            "delete_categories": ["database:messages"],
            "return_categories": ["database:matter_documents"],
            "signer_title": "Firm administrator",
            "authority_attested": True,
            "reason": "Customer-authorized lifecycle rehearsal; no destructive action.",
        },
    )
    assert requested.status_code == 200, requested.text
    case_id = requested.json()["case_id"]
    assert requested.json()["status"] == "requested"
    assert "no data was deleted" in requested.json()["receipt"]["outcome"].lower()

    first = await client.post(
        f"/api/platform/operating-trust/tenants/{tenant_id}/offboarding/{case_id}/approve",
        headers=_operator_headers("operator-one@example.com"),
        json={"reason": "Scope and export evidence reviewed."},
    )
    assert first.status_code == 200, first.text
    assert first.json()["approval_count"] == 1
    assert first.json()["status"] == "requested"

    duplicate = await client.post(
        f"/api/platform/operating-trust/tenants/{tenant_id}/offboarding/{case_id}/approve",
        headers=_operator_headers("operator-one@example.com"),
        json={"reason": "Duplicate must not count."},
    )
    assert duplicate.status_code == 409

    second = await client.post(
        f"/api/platform/operating-trust/tenants/{tenant_id}/offboarding/{case_id}/approve",
        headers=_operator_headers("operator-two@example.com"),
        json={"reason": "Legal hold and provider scope independently reviewed."},
    )
    assert second.status_code == 200, second.text
    assert second.json()["approval_count"] == 2
    assert second.json()["status"] == "approved"

    completed = await client.post(
        f"/api/platform/operating-trust/tenants/{tenant_id}/offboarding/{case_id}/complete",
        headers=_operator_headers("operator-two@example.com"),
        json={
            "actual_counts": {
                "database:messages": 0,
                "database:matter_documents": 0,
            },
            "providers": [
                {
                    "provider": "customer cloud",
                    "status": "customer_controlled",
                    "evidence_reference": "provider-disposition:001",
                }
            ],
            "backups": [
                {
                    "backup_class": "application-database",
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(days=30)
                    ).isoformat(),
                    "evidence_reference": "backup-expiry:database-001",
                },
                {
                    "backup_class": "tenant-file-store",
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(days=30)
                    ).isoformat(),
                    "evidence_reference": "backup-expiry:files-001",
                },
            ],
            "evidence_reference": "deletion-proof:001",
            "evidence_sha256": "c" * 64,
            "outcome": "Authorized deletion actions were reconciled; immutable contractual evidence remains retained.",
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert len(completed.json()["approvals"]) == 2
    assert completed.json()["scope"]["execution_boundary"].startswith("evidence-only")


@pytest.mark.asyncio
async def test_offboarding_is_evidenced_but_blocked_by_legal_hold(client):
    hold = await client.put(
        "/api/compliance/retention",
        json={
            "chat_attachments_days": 30,
            "legal_hold": True,
            "legal_hold_reason": "Pending litigation preservation notice",
        },
    )
    assert hold.status_code == 200, hold.text
    requested = await client.post(
        "/api/compliance/operating/offboarding",
        json={
            "delete_categories": ["database:messages"],
            "signer_title": "Firm administrator",
            "authority_attested": True,
            "reason": "Offboarding request received during preservation hold.",
        },
    )
    assert requested.status_code == 200, requested.text
    assert requested.json()["status"] == "hold_blocked"
    assert requested.json()["receipt"]["status"] == "blocked"
    assert requested.json()["receipt"]["legal_hold_snapshot"]["active"] is True
    assert requested.json()["receipt"]["legal_hold_snapshot"]["set_at"].endswith(
        "+00:00"
    )
