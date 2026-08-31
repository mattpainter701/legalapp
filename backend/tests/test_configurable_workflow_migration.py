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
    assert 'down_revision = "147_studio_drafts"' in source


def test_postgresql_rehearsal_registers_uuid_adaptation() -> None:
    source = REHEARSAL.read_text()
    assert "from psycopg2.extras import register_uuid" in source
    assert "register_uuid()" in source


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


def test_trigger_functions_pin_trusted_relations_against_temp_shadowing() -> None:
    source = MIGRATION.read_text()
    assert "FROM public.custom_field_definitions" in source
    assert "FROM public.matter_custom_field_values" in source
    assert "FROM public.contact_custom_field_values" in source
    assert "FROM public.matter_workflow_template_versions" in source
    assert "FROM public.tenants tenant" in source
    assert "JOIN public.demo_sessions demo" in source
    assert source.count("SET search_path = pg_catalog, public") >= 7
    rehearsal = REHEARSAL.read_text()
    assert "CREATE TEMP TABLE custom_field_definitions" in rehearsal
    assert "CREATE TEMP TABLE matter_workflow_template_versions" in rehearsal
    assert "temp_shadow_typed_value" in rehearsal
    assert "temp_shadow_approved_definition" in rehearsal
    assert "assert_demo_purge_lifecycle" in rehearsal
    assert "verified_demo_purge" in rehearsal
    assert "purge_demo_tenant(session, tenant_id)" in rehearsal


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
    assert source.count("FOR SHARE;") == 5
    assert "AND entity_type=NEW.entity_type\n       FOR SHARE;" in source
    assert "workflow template approval transition must be exact" in source


def test_immutable_history_has_only_verified_demo_purge_delete_carve_out() -> None:
    source = MIGRATION.read_text()
    assert "config_workflow_demo_purge_authorized" in source
    assert "app.config_workflow_demo_purge_tenant_id" in source
    assert "app.config_workflow_demo_purge_session_id" in source
    for contract in (
        "tenant.billing_tier = 'demo'",
        "tenant.domain LIKE '%.demo.invalid'",
        "tenant.is_active = false",
        "tenant.expires_at <= now()",
        "demo.status = 'purging'",
        "demo.expires_at <= now()",
        "demo.fixture_tenant_id <> demo.tenant_id",
        "demo.purge_started_at IS NOT NULL",
    ):
        assert contract in source
    purge = (ROOT / "backend/app/services/demo_purge.py").read_text()
    assert "_CONFIG_WORKFLOW_PURGE_ORDER" in purge
    assert "_authorize_config_workflow_demo_purge" in purge
    assert "_CONFIG_WORKFLOW_TABLES" in purge


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
    assert "workflow_dependency_serialization" in source
    assert "preview_snapshot_races" in source
    for race in (
        "archive",
        "field_deactivation",
        "active_field_phantom",
        "matter_value",
        "assignee_deactivation",
        "apply_first_exactly_once_and_replayed",
        "writer_first_stale_409_zero_effects",
        "preview_first_snapshot_coherent",
        "writer_first_snapshot_includes_mutation",
    ):
        assert race in source
    assert "no_context" in source and "cross_write" in source
    assert "thread.join" in source and "[0, 1]" in source
    for evidence in (
        "approved_stage_insert",
        "approved_stage_update",
        "approved_stage_delete",
        "approved_checklist_insert",
        "approved_checklist_update",
        "approved_checklist_delete",
        "approved_requirement_insert",
        "approved_requirement_update",
        "approved_requirement_delete",
        "mutated_draft_approval_transition",
        "approval_child_serialization",
        "mutation_blocked_until_approval_commit",
        "mutation_rejected_after_approval",
        "approved_snapshot_unchanged",
        "custom_field_contract_serialization",
        "value_blocked_contract_rewrite",
        "contract_rewrite_blocked_value",
        "draft_to_approved_transitions",
        "draft_stage_insert_update_delete",
        "draft_checklist_insert_update_delete",
        "draft_requirement_insert_update_delete",
    ):
        assert evidence in source
    assert "history_event_update" in source
    assert "typed_value_mismatch" in source
    assert "cross_linked_contact_fk" in source
    assert "run_snapshot_update" in source
    assert "sensitive_field_downgrade" in source
    assert "existing system Administrator roles lack manage_workflows" in source
    assert "successful_rollback" in source
    assert "autoflush=False" in source


def test_migration_backfills_existing_system_administrators() -> None:
    source = MIGRATION.read_text()
    assert "UPDATE roles SET capabilities = capabilities ||" in source
    assert r"'[\"manage_workflows\"]'::jsonb" in source
    assert "name = 'Administrator' AND is_system IS TRUE" in source


def test_downgrade_deliberately_preserves_unattributed_capability_grants() -> None:
    source = MIGRATION.read_text()
    downgrade = source.split("def downgrade() -> None:", 1)[1]
    assert "Deliberately retain manage_workflows" in downgrade
    assert "UPDATE roles" not in downgrade
