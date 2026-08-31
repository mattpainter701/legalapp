from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/migrations/versions/148_configurable_workflows.py"
REHEARSAL = ROOT / "scripts/rehearse_configurable_workflows.py"

TABLES = {
    "custom_field_definitions",
    "matter_custom_field_values",
    "contact_custom_field_values",
    "matter_workflow_templates",
    "matter_workflow_template_versions",
    "matter_workflow_stage_definitions",
    "matter_workflow_checklist_definitions",
    "matter_workflow_field_requirements",
    "matter_workflow_runs",
    "matter_workflow_run_events",
    "matter_workflow_run_steps",
}


def test_revision_and_down_revision_are_current_chain() -> None:
    source = MIGRATION.read_text()
    assert 'revision = "148_configurable_workflows"' in source
    assert 'down_revision = "146_research_workspaces"' in source


def test_every_comp09_table_has_forced_fail_closed_rls() -> None:
    source = MIGRATION.read_text()
    for table in TABLES:
        assert (
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in source
            or "_rls(table)" in source
        )
        assert (
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in source
            or "_rls(table)" in source
        )
    assert "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid" in source
    assert "WITH CHECK" in source


def test_composite_parent_targets_and_immutable_triggers_are_present() -> None:
    source = MIGRATION.read_text()
    assert "uq_contacts_tenant_id" in source and "uq_tasks_tenant_id" in source
    for target in (
        "matters(tenant_id,id)",
        "contacts(tenant_id,id)",
        "tasks(tenant_id,id)",
        "users(tenant_id,id)",
    ):
        assert target in source
    assert "BEFORE UPDATE OR DELETE" in source
    assert "BEFORE INSERT OR UPDATE OR DELETE" in source
    assert "prevent_config_workflow_immutable" in source
    assert "prevent_config_workflow_run_tamper" in source
    assert "prevent_approved_workflow_mutation" in source
    assert "148_configurable_workflows_created" in source
    assert "OLD.template_version_id" in source and "NEW.template_version_id" in source


def test_typed_values_and_contact_relationships_are_database_enforced() -> None:
    source = MIGRATION.read_text()
    assert "validate_config_workflow_options" in source
    assert "prevent_config_field_contract_rewrite" in source
    assert "enforce_config_custom_field_value" in source
    assert "linked_contact_id" in source
    assert "FOREIGN KEY (tenant_id,linked_contact_id)" in source
    assert "invalid typed custom field value" in source
    assert "ck_matter_workflow_stage_definitions_label" in source
    assert "ck_matter_workflow_checklist_definitions_title" in source


def test_rehearsal_requires_runtime_role_and_reports_evidence() -> None:
    source = REHEARSAL.read_text()
    assert "MIGRATOR_DATABASE_URL" in source and "RLS_TEST_DATABASE_URL" in source
    assert "rolsuper, rolbypassrls" in source
    assert "148_configurable_workflows" in source
    assert "visible_rows_by_tenant" in source
    assert "database_rejections" in source
    assert "concurrent_claim_rowcounts" in source
    assert "concurrent_apply" in source
    assert "compensating_rollback" in source
    assert "same_blockers" in source
    assert "failed_apply" in source
    assert "failed apply left partial effects" in source
    assert "no_context" in source and "cross_write" in source
    assert "thread.join" in source and "[0, 1]" in source
    assert "approved_child_insert" in source
    assert "history_event_update" in source
    assert "typed_value_mismatch" in source
    assert "cross_linked_contact_fk" in source
    assert "run_snapshot_update" in source
    assert "sensitive_field_downgrade" in source
    assert "existing system Administrator roles lack manage_workflows" in source


def test_migration_backfills_existing_system_administrators() -> None:
    source = MIGRATION.read_text()
    assert "UPDATE roles SET capabilities = capabilities ||" in source
    assert r"'[\"manage_workflows\"]'::jsonb" in source
    assert "name = 'Administrator' AND is_system IS TRUE" in source
