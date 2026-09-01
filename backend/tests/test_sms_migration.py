from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/migrations/versions/153_sms_lifecycle.py"
OLD_MIGRATION = ROOT / "backend/migrations/versions/148_sms_lifecycle.py"
CI = ROOT / ".github/workflows/ci.yml"


def test_sms_is_the_single_migration_after_configurable_workflows():
    source = MIGRATION.read_text(encoding="utf-8")

    assert MIGRATION.exists()
    assert not OLD_MIGRATION.exists()
    assert 'revision = "153_sms_lifecycle"' in source
    assert 'down_revision = "152_file_open_intents"' in source
    assert '"sms_revoked_at"' in source
    for immutable_snapshot_field in (
        '"phone_verified"',
        '"consented_at"',
        '"consent_expires_at"',
        '"consent_language"',
        '"consent_timezone"',
        '"quiet_hours_start"',
        '"quiet_hours_end"',
    ):
        assert immutable_snapshot_field in source
    assert 'op.drop_constraint("uq_contacts_tenant_id"' not in source
    assert '("contacts", "uq_contacts_tenant_id")' not in source


def test_sms_migration_enforces_rls_and_tenant_composite_references():
    source = MIGRATION.read_text(encoding="utf-8")
    upgrade = source.split("def downgrade() -> None:", 1)[0]

    for table in (
        "sms_consent_events",
        "sms_number_suppressions",
        "sms_number_suppression_events",
        "sms_provider_configs",
        "sms_provider_credentials",
        "sms_messages",
        "sms_review_items",
    ):
        assert table in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid" in source
    for constraint in (
        "uq_communication_logs_tenant_id",
        "uq_leads_tenant_id",
        "uq_lead_channel_consents_tenant_id",
        "fk_lead_channel_consents_tenant_lead",
        "fk_sms_consent_events_tenant_consent",
        "fk_sms_consent_events_tenant_lead",
        "fk_sms_consent_events_tenant_contact",
        "fk_sms_consent_events_tenant_user",
        "uq_sms_number_suppressions_tenant_id",
        "fk_sms_number_suppression_events_tenant_suppression",
        "uq_sms_messages_tenant_id",
        "fk_sms_provider_configs_tenant_user",
        "fk_sms_provider_credentials_tenant_user",
        "fk_sms_messages_tenant_provider_credential",
        "fk_sms_messages_tenant_contact",
        "fk_sms_messages_tenant_matter",
        "fk_sms_messages_tenant_communication",
        "fk_sms_messages_tenant_user",
        "fk_sms_messages_tenant_reconciler",
        "fk_sms_messages_tenant_attestor",
        "fk_sms_review_items_tenant_message",
        "fk_sms_review_items_tenant_user",
        "fk_task_automation_runs_tenant_sms_message",
        "fk_task_automation_runs_tenant_task",
    ):
        assert constraint in source
    assert "prevent_sms_evidence_event_mutation" in source
    assert "sms_demo_purge_authorized" in source
    for index_name in (
        "idx_sms_provider_configs_tenant",
        "idx_sms_provider_credentials_tenant_generation",
        "idx_sms_messages_tenant_provider_credential",
        "uq_sms_provider_configs_active_account_service",
        "uq_sms_provider_configs_active_account_number",
        "idx_sms_number_suppressions_tenant_state",
        "idx_sms_number_suppression_events_tenant_number",
        "idx_sms_messages_tenant_contact",
        "idx_sms_messages_tenant_matter",
        "idx_sms_messages_reconciliation",
        "idx_sms_review_items_tenant_status",
        "idx_task_automation_runs_tenant_sms_message",
    ):
        assert index_name in source
    assert "is_active AND messaging_service_sid IS NOT NULL" in source
    assert "is_active AND from_number IS NOT NULL" in source
    for check_name in (
        "ck_lead_channel_consents_sms_status",
        "ck_lead_channel_consents_mobile_e164",
        "ck_lead_channel_consents_sms_active_evidence",
        "ck_sms_consent_events_sms_status",
        "ck_sms_consent_events_mobile_e164",
        "ck_sms_consent_events_active_evidence",
        "ck_sms_provider_configs_provider",
        "ck_sms_provider_configs_generation",
        "ck_sms_provider_configs_sender_ready",
        "ck_sms_provider_configs_active",
        "ck_sms_provider_configs_active_evidence",
        "ck_sms_provider_configs_from_number_e164",
        "ck_sms_provider_credentials_provider",
        "ck_sms_provider_credentials_generation",
        "ck_sms_provider_credentials_retirement",
        "ck_sms_provider_credentials_from_number_e164",
        "ck_sms_number_suppression_events_action",
        "ck_sms_number_suppression_events_state",
        "ck_sms_messages_direction",
        "ck_sms_messages_status",
        "ck_sms_messages_direction_status",
        "ck_sms_messages_delivery_certainty",
        "ck_sms_messages_provider_status",
        "ck_sms_messages_request_digest",
        "ck_sms_messages_from_number_e164",
        "ck_sms_messages_to_number_e164",
        "ck_sms_messages_reconciliation_resolution",
        "ck_sms_messages_reconciliation_evidence",
        "ck_sms_messages_provider_unknown_reconciliation",
        "ck_sms_messages_provider_truth",
        "ck_sms_messages_status_certainty",
        "ck_sms_review_items_status",
        "ck_sms_review_items_review_evidence",
    ):
        assert check_name in source
    assert '"task_automation_runs"' in source
    assert '"delivery_certainty"' in source
    assert 'sa.Column("delivery_certainty_v2", sa.String(50), nullable=True)' in source
    assert 'sa.Column("reconciliation_resolution", sa.String(64))' in source
    assert "sync_task_automation_delivery_certainty" in source
    assert "task_automation_delivery_certainty_sync" in source
    assert "postgresql_not_valid=True" in source
    assert "VALIDATE CONSTRAINT fk_task_automation_runs_tenant_task" in source
    assert "WHEN NEW.delivery_certainty = 'failed_after_acceptance'" in source
    assert "THEN 'provider_failed_after_acceptance'" in source
    assert "op.alter_column" not in upgrade
    assert (
        'op.drop_constraint(\n        "task_automation_runs_task_id_fkey"'
        not in upgrade
    )
    assert "task_automation_runs_task_id_fkey" not in source
    assert "SET delivery_certainty_v2 = delivery_certainty" not in upgrade
    assert "AND delivery_certainty = 'confirmed_received')" in source
    for provider_truth_field in (
        '"provider_messaging_service_sid"',
        '"provider_credential_id"',
        '"provider_submission_started_at"',
        '"provider_created_at"',
        '"operator_observed_absent_at"',
        '"operator_observed_absent_by_user_id"',
        '"delivery_certainty"',
    ):
        assert provider_truth_field in source


def test_ci_rehearses_sms_from_148_with_149_as_the_canonical_head():
    source = CI.read_text(encoding="utf-8")
    start = source.index("sms-lifecycle-rehearsal:")
    end = source.index("# ─── Backend: Tests", start)
    job = source[start:end]

    assert "sms-lifecycle-rehearsal:" in source
    assert "alembic upgrade 148_configurable_workflows" in source
    assert "alembic upgrade 153_sms_lifecycle" in source
    assert "rehearse_demo_purge_schema_guard.py --expected all" in source
    assert "153_sms_lifecycle" in (ROOT / "backend/tests/test_migrations.py").read_text(
        encoding="utf-8"
    )
    assert "test_sms_lifecycle_db.py" in source
    assert "python -m pytest -vv --maxfail=1" in job
    assert "Rehearse SMS expand-contract downgrade compatibility" in source
    assert "alembic downgrade 148_configurable_workflows" in source
    assert "legacy_fk_count <> 1" in source
    assert "composite_fk_count <> 0" in source
    assert "v2_column_count <> 0" in source
    migration = job.index("alembic upgrade 153_sms_lifecycle")
    purge_guard = job.index("rehearse_demo_purge_schema_guard.py --expected all")
    downgrade = job.index("alembic downgrade 148_configurable_workflows")
    reupgrade = job.index("alembic upgrade 153_sms_lifecycle", downgrade)
    runtime_role = job.index("Provision a production-shaped SMS runtime role")
    pytest_rehearsal = job.index("python -m pytest -vv")
    assert (
        migration
        < purge_guard
        < downgrade
        < reupgrade
        < runtime_role
        < pytest_rehearsal
    )
    assert "sms-dispatch-reconciliation" in (
        ROOT / "backend/app/services/scheduler.py"
    ).read_text(encoding="utf-8")
