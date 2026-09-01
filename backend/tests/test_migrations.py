from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_revision_graph_resolves_heads():
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()

    assert heads == ["150_studio_render_jobs"]


def test_configurable_workflow_migration_is_tenant_safe_and_immutable():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "148_configurable_workflows.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "148_configurable_workflows"' in source
    assert 'down_revision = "147_studio_drafts"' in source
    for table in (
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
    ):
        assert table in source
    assert "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid" in source
    assert "WITH CHECK" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "prevent_config_workflow_immutable" in source
    assert "prevent_config_workflow_run_tamper" in source
    assert "prevent_approved_workflow_mutation" in source
    assert "BEFORE UPDATE OR DELETE" in source
    assert "BEFORE INSERT OR UPDATE OR DELETE" in source
    assert "enforce_config_custom_field_value" in source
    assert "prevent_config_field_contract_rewrite" in source
    assert "UPDATE roles SET capabilities = capabilities ||" in source
    assert "manage_workflows" in source
    assert "FOREIGN KEY (tenant_id,linked_contact_id)" in source
    for parent in (
        "matters(tenant_id,id)",
        "contacts(tenant_id,id)",
        "tasks(tenant_id,id)",
        "users(tenant_id,id)",
    ):
        assert parent in source


def test_mediation_confidentiality_migration_is_additive_and_tenant_scoped():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "145_mediation_confidentiality.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "145_mediation_confidentiality"' in source
    assert 'down_revision = "144_brief_checks"' in source
    for table in (
        "mediation_document_recipients",
        "mediation_proposal_recipients",
    ):
        assert f'_enable_tenant_rls("{table}")' in source
        assert "CREATE POLICY {table}_tenant_isolation" in source
        assert '"tenant_id"' in source

    assert "content_sha256" in source
    assert "review_state" in source
    assert "released_by_user_id" in source
    assert "first_viewed_at" in source


def test_background_value_cutover_never_backfills_existing_spend_as_free():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "137_background_ai_value_quota.py"
    ).read_text(encoding="utf-8")

    assert "LEGACY_UNKNOWN_MICROS" in source
    assert "legacy-cutover-unknown" in source
    assert "value_cutover_unknown" in source
    assert "WHERE status IN ('reserved', 'settled', 'unknown')" in source
    assert "app.background_ai_quota_scope', 'on', true" in source
    assert "app.background_ai_quota_scope', 'off', true" in source


def test_smb_agent_lifecycle_indexes_cover_auth_and_cleanup_paths():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "132_smb_agent_lifecycle_indexes.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision = "131_demo_resume_profile"' in source
    assert "ix_smb_agents_api_key_hash" in source
    assert "ix_smb_agents_tenant_status_expiry" in source
    assert '["tenant_id", "status", "pairing_expires_at"]' in source


def test_demo_resume_rls_is_hash_scoped_select_only_and_profile_is_unique():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "131_demo_resume_profile.py"
    ).read_text(encoding="utf-8")

    assert "FOR SELECT TO PUBLIC" in source
    assert "app.demo_resume_email_hash" in source
    assert "resume_email_hash = NULLIF" in source
    assert "WHERE resume_email_hash IS NULL" in source
    assert 'alter_column("demo_sessions", "resume_email_hash"' not in source
    assert "app.rls_bypass" not in source
    assert "uq_llm_routing_profiles_demo_default" in source
    assert 'postgresql_where=sa.text("is_demo_default")' in source


def test_smb_agent_bootstrap_rls_is_exact_select_only_and_not_a_bypass():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "130_smb_agent_bootstrap_rls.py"
    ).read_text(encoding="utf-8")

    assert "FOR SELECT TO PUBLIC" in source
    assert "app.smb_agent_api_key_hash" in source
    assert "app.smb_agent_pairing_code" in source
    assert "app.rls_bypass" not in source
    assert "smb_shares" not in source

    registration = (
        (backend_dir / "app" / "services" / "smb.py")
        .read_text(encoding="utf-8")
        .split("async def register_agent", 1)[1]
        .split("async def record_heartbeat", 1)[0]
    )
    clear_selector = registration.index("clear_smb_agent_bootstrap_lookup")
    bind_tenant = registration.index("await set_tenant_context", clear_selector)
    lock_row = registration.index(".with_for_update()", bind_tenant)
    assert clear_selector < bind_tenant < lock_row
    assert (
        registration.index("SmbAgent.pairing_code == pairing_code", bind_tenant)
        < lock_row
    )


def test_revision_ids_fit_the_alembic_version_column():
    """alembic_version.version_num is varchar(32).

    A longer id passes every local check and then fails at the very end of
    `alembic upgrade`, after the DDL has run, when the stamp is written back.
    """

    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    script = ScriptDirectory.from_config(config)

    oversized = {
        revision.revision: len(revision.revision)
        for revision in script.walk_revisions()
        if len(revision.revision) > 32
    }

    assert not oversized, f"revision ids exceed varchar(32): {oversized}"


def test_staged_review_migration_enforces_approval_identity():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "115_staged_task_review.py"
    ).read_text(encoding="utf-8")

    for constraint in (
        "ck_tasks_staged_reviewers_distinct",
        "ck_tasks_review_evidence_pairs",
        "ck_tasks_staff_reviewer_evidence_actor",
        "ck_tasks_attorney_reviewer_evidence_actor",
        "ck_tasks_staff_stage_reviewer",
        "ck_tasks_attorney_stage_reviewer",
        "ck_tasks_approved_staff_evidence",
    ):
        assert source.count(constraint) == 2
    assert "staff_reviewer_user_id != attorney_reviewer_user_id" in source
    assert "staff_reviewed_by_user_id = staff_reviewer_user_id" in source
    assert "attorney_approved_by_user_id = attorney_reviewer_user_id" in source
    assert "name = 'Administrator' AND is_system IS TRUE" in source
    assert "approve_legal_work" in source


def test_live_demo_foundation_is_additive_and_forces_tenant_rls():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "105_live_demo_foundation.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision = "104_user_professional_context"' in source
    assert (
        'sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)' in source
    )
    assert "CREATE POLICY demo_sessions_tenant_isolation" in source
    assert "ALTER TABLE demo_sessions FORCE ROW LEVEL SECURITY" in source
    assert "ck_demo_sessions_quota_counters" in source


def test_demo_quota_migration_is_tenant_scoped_and_idempotent():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "106_demo_usage_reservations.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision = "105_live_demo_foundation"' in source
    assert "uq_demo_usage_session_key" in source
    assert "ALTER TABLE demo_usage_reservations FORCE ROW LEVEL SECURITY" in source
    assert "tenant_isolation_demo_usage_reservations" in source


def test_document_revision_migration_forces_tenant_rls_and_preserves_sources():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "101_doc_revisions.py"
    ).read_text(encoding="utf-8")

    assert "matter_document_revisions_tenant_isolation" in source
    assert "current_setting('app.current_tenant_id', true)" in source
    assert "ALTER TABLE matter_document_revisions FORCE ROW LEVEL SECURITY" in source
    assert (
        source.count('sa.ForeignKey("matter_documents.id", ondelete="RESTRICT")') == 3
    )
    assert "uq_doc_revisions_tenant_client_request" in source
    assert "ck_doc_revisions_approval_evidence" in source
    assert "'superseded'" in source


def test_chat_task_automation_migration_is_exactly_once_and_defaults_off():
    """Two properties here are load-bearing for client-facing automation.

    The unique constraint is the only thing preventing a double-approved task
    from emailing a client twice, and the false default is what stops every
    existing tenant from inheriting assistant-proposed work on deploy.
    """
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "102_chat_task_automation.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "102_chat_task_automation"' in source
    assert 'down_revision = "101_doc_revisions"' in source
    assert "uq_task_automation_runs_task_key" in source
    assert "task_automation_runs_tenant_isolation" in source
    assert "ALTER TABLE task_automation_runs FORCE ROW LEVEL SECURITY" in source
    # Absence of a tenant context must deny, not raise on ''::uuid.
    assert "NULLIF(current_setting('app.current_tenant_id', true), '')" in source

    flag = source.split('"enable_chat_actions"', 1)[1].split("op.create_table", 1)[0]
    assert 'sa.text("false")' in flag
    assert "nullable=False" in flag

    # Reversible: the downgrade must undo all three schema additions.
    downgrade = source.split("def downgrade()", 1)[1]
    assert "drop_table" in downgrade
    assert '"enable_chat_actions"' in downgrade
    assert '"pending_action"' in downgrade


def test_task_work_board_migration_has_history_rls_and_concurrency_fields():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "100_task_work_board.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "100_task_work_board"' in source
    assert 'down_revision = "099_chat_latency_breakdown"' in source
    assert '"status_changed_at"' in source
    status_changed_at = source.split('"status_changed_at"', 1)[1].split(
        "op.add_column", 1
    )[0]
    assert "nullable=False" in status_changed_at
    assert 'op.alter_column("tasks", "status_changed_at"' not in source
    assert '"waiting_reason"' in source
    assert '"reviewer_user_id"' in source
    assert '"version"' in source
    assert '"enable_task_board"' in source
    assert '"task_events"' in source
    assert "CREATE POLICY task_events_tenant_isolation" in source
    assert "ALTER TABLE task_events FORCE ROW LEVEL SECURITY" in source


def test_zoom_phone_migration_is_fail_closed_and_restores_force_rls():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "089_zoom_phone_durability.py"
    ).read_text(encoding="utf-8")

    no_force = "ALTER TABLE communication_logs NO FORCE ROW LEVEL SECURITY"
    duplicate_gate = "HAVING count(*) > 1"
    force = "ALTER TABLE communication_logs FORCE ROW LEVEL SECURITY"
    unique_index = "uq_commlogs_zoom_phone_external_ref"
    assert source.index(no_force) < source.index(duplicate_gate) < source.index(force)
    assert source.index(force) < source.index(unique_index)
    assert "no customer rows were changed" in source


def test_pdf_preview_evidence_migration_preserves_terminal_audit_and_forces_rls():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "091_pdf_preview_evidence.py"
    ).read_text(encoding="utf-8")

    assert 'sa.ForeignKey("document_templates.id", ondelete="SET NULL")' in source
    assert 'sa.Column("consumed_at"' in source
    assert '"reconciliation_required_at"' in source
    assert "ck_document_template_previews_terminal_state" in source
    assert "document_template_previews_tenant_isolation" in source
    assert "ALTER TABLE document_template_previews FORCE ROW LEVEL SECURITY" in source


def test_zoom_account_binding_backfill_crosses_force_rls_then_restores_it():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "090_zoom_account_binding.py"
    ).read_text(encoding="utf-8")

    app_no_force = "ALTER TABLE tenant_oauth_apps NO FORCE ROW LEVEL SECURITY"
    credential_no_force = "ALTER TABLE tenant_credentials NO FORCE ROW LEVEL SECURITY"
    backfill = "UPDATE tenant_oauth_apps AS app"
    credential_force = "ALTER TABLE tenant_credentials FORCE ROW LEVEL SECURITY"
    app_force = "ALTER TABLE tenant_oauth_apps FORCE ROW LEVEL SECURITY"
    assert (
        source.index(app_no_force)
        < source.index(credential_no_force)
        < source.index(backfill)
        < source.index(credential_force)
        < source.index(app_force)
    )
    assert "app.tenant_id = credential.tenant_id" in source
    assert "app.provider = 'zoom_phone'" in source
    assert "credential.provider = 'zoom_phone'" in source


def test_zoom_phone_api_webhook_split_repairs_unproven_binding_under_owner_role():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "092_zoom_phone_api_webhook_split.py"
    ).read_text(encoding="utf-8")

    app_no_force = "ALTER TABLE tenant_oauth_apps NO FORCE ROW LEVEL SECURITY"
    credential_no_force = "ALTER TABLE tenant_credentials NO FORCE ROW LEVEL SECURITY"
    app_repair = "UPDATE tenant_oauth_apps"
    credential_repair = "UPDATE tenant_credentials"
    credential_force = "ALTER TABLE tenant_credentials FORCE ROW LEVEL SECURITY"
    app_force = "ALTER TABLE tenant_oauth_apps FORCE ROW LEVEL SECURITY"
    assert (
        source.index(app_no_force)
        < source.index(credential_no_force)
        < source.index(app_repair)
        < source.index(credential_repair)
        < source.index(credential_force)
        < source.index(app_force)
    )
    assert "~ '^[0-9]+$'" in source
    assert "UPDATE tenant_oauth_apps AS app" in source
    assert "FROM tenant_credentials AS credential" in source
    assert "app.tenant_id = credential.tenant_id" in source
    assert "SET zoom_account_id = NULL" in source
    assert "SET service_account_email = NULL" in source
    assert "health = 'account_verification_required'" in source
    assert "encrypted_refresh_token IS NOT NULL" in source


def test_online_migrations_seed_non_customer_rls_context():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (backend_dir / "migrations" / "env.py").read_text(encoding="utf-8")
    online = source.split("def do_run_migrations", 1)[1].split(
        "async def run_async_migrations", 1
    )[0]

    sentinel = "00000000-0000-0000-0000-000000000000"
    migration_call = "context.run_migrations()"
    assert sentinel in online
    assert online.index("set_config('app.tenant_id'") < online.index(migration_call)
    assert online.index("set_config('app.current_tenant_id'") < online.index(
        migration_call
    )
    assert online.index("set_config('app.rls_bypass', 'off'") < online.index(
        migration_call
    )


def test_revision_ids_fit_alembic_version_column():
    """alembic_version.version_num is VARCHAR(32) — a longer revision id
    fails at apply time (StringDataRightTruncationError), not at import time,
    so this only ever surfaces during a real deploy unless checked here."""
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    script = ScriptDirectory.from_config(config)

    overlong = [
        rev.revision for rev in script.walk_revisions() if len(rev.revision) > 32
    ]
    assert overlong == []


def test_action_audit_cutover_terminalizes_legacy_work_under_owner_rls_window():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "103_task_action_delivery_audit.py"
    ).read_text(encoding="utf-8")

    run_no_force = "ALTER TABLE task_automation_runs NO FORCE ROW LEVEL SECURITY"
    job_no_force = "ALTER TABLE durable_jobs NO FORCE ROW LEVEL SECURITY"
    run_update = "UPDATE task_automation_runs"
    job_update = "UPDATE durable_jobs"
    job_force = "ALTER TABLE durable_jobs FORCE ROW LEVEL SECURITY"
    run_force = "ALTER TABLE task_automation_runs FORCE ROW LEVEL SECURITY"
    assert (
        source.index(run_no_force)
        < source.index(job_no_force)
        < source.index(run_update)
        < source.index(job_update)
        < source.index(job_force)
        < source.index(run_force)
    )
    assert "delivery_certainty = 'outcome_unknown'" in source
    assert "approval_idempotency_key" in source
    assert "status = 'completed'" in source


def test_mcp_security_backfill_explicitly_enters_force_rls_policy():
    """The migration must also work for managed-Postgres owner roles."""
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "087_mcp_product_security.py"
    ).read_text(encoding="utf-8")

    bypass_on = "set_config('app.rls_bypass', 'on', true)"
    backfill = "UPDATE mcp_product_keys SET monthly_call_limit"
    bypass_off = "set_config('app.rls_bypass', 'off', true)"
    assert source.index(bypass_on) < source.index(backfill) < source.index(bypass_off)


def test_unlinked_sync_email_cleanup_is_bounded_and_truly_reversible():
    """The two properties that make this cleanup safe to run on customer data.

    It hides rows by flipping a status the workspace filters on, which is
    indistinguishable from a user's own delete.  The recorded-before-mutated
    ordering is therefore the only thing that makes the downgrade honest, and
    the WHERE bounds are the only thing keeping filed correspondence out of it.
    """
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "109_hide_unlinked_synced_emails.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "109_hide_unlinked_synced_emails"' in source
    assert 'down_revision = "108_platform_operator_api_keys"' in source

    upgrade = source.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]

    # Only syncer-created mail that never acquired a matter or a contact.
    assert "matter_id IS NULL" in upgrade
    assert "contact_id IS NULL" in upgrade
    assert "external_ref LIKE 'microsoft:%'" in upgrade
    assert "external_ref LIKE 'google:%'" in upgrade
    # Never re-hide, and never touch outbound or non-email records.
    assert "status <> 'deleted'" in upgrade
    assert "channel = 'email'" in upgrade
    assert "direction = 'inbound'" in upgrade

    # Record first, then mutate, and drive the mutation off what was recorded:
    # a row can only be hidden once its restore path exists.
    assert upgrade.index("INSERT INTO communication_log_sync_hides") < upgrade.index(
        "UPDATE communication_logs AS c"
    )
    assert "FROM recorded AS r" in upgrade
    assert "WHERE c.id = r.communication_log_id" in upgrade

    # communication_logs forces RLS against a tenant id migrations do not have,
    # so the backfill must run inside a NO FORCE window or match nothing at all.
    # Losing the trailing restore would leave the table readable across tenants.
    no_force = "ALTER TABLE communication_logs NO FORCE ROW LEVEL SECURITY"
    backfill = "INSERT INTO communication_log_sync_hides"
    force = "ALTER TABLE communication_logs FORCE ROW LEVEL SECURITY"
    assert upgrade.index(no_force) < upgrade.index(backfill) < upgrade.index(force)

    # The ledger only gets its policy after it is written, for the same reason.
    assert upgrade.index(backfill) < upgrade.index(
        "ALTER TABLE communication_log_sync_hides ENABLE ROW LEVEL SECURITY"
    )

    # The ledger is customer data, so it carries tenant_id and is fail-closed.
    assert (
        "ALTER TABLE communication_log_sync_hides FORCE ROW LEVEL SECURITY" in upgrade
    )
    assert "tenant_isolation_communication_log_sync_hides" in upgrade
    assert "NULLIF(current_setting('app.current_tenant_id', true), '')" in upgrade

    downgrade = source.split("def downgrade()", 1)[1]
    assert "SET status = h.previous_status" in downgrade
    # Only revive what is still hidden; a hand-revived row keeps its status.
    assert "AND c.status = 'deleted'" in downgrade
    assert 'op.drop_table("communication_log_sync_hides")' in downgrade
    # The restore needs the same window, and must hand FORCE RLS back.
    assert (
        downgrade.index(no_force)
        < downgrade.index("SET status = h.previous_status")
        < downgrade.index(force)
    )


def test_inbound_email_tables_are_force_rls_with_select_only_route_lookup():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "124_inbound_email.py"
    ).read_text(encoding="utf-8")

    assert '_tenant_rls("inbound_email_aliases")' in source
    assert '_tenant_rls("inbound_emails")' in source
    assert "ON inbound_email_aliases FOR SELECT" in source
    assert "app.inbound_email_route_lookup" in source
    assert "WITH CHECK (current_setting('app.inbound_email_route_lookup'" not in source


def test_studio_draft_migration_is_post_146_force_rls_and_immutable():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "147_studio_drafts.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "147_studio_drafts"' in source
    assert 'down_revision = "146_research_workspaces"' in source
    tables = (
        "studio_source_artifacts",
        "studio_drafts",
        "studio_draft_fields",
        "studio_draft_placements",
        "studio_draft_snapshots",
        "studio_draft_idempotency",
        "studio_draft_audit_events",
    )
    assert "for table in TABLES" in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "app.current_tenant_id" in source
    for table in tables:
        assert f'"{table}"' in source
    assert "studio_drafts_source_identity_guard" in source
    immutable_upgrade = source.split(
        "CREATE FUNCTION prevent_studio_immutable_mutation", 1
    )[1].split("def downgrade", 1)[0]
    for table in (
        "studio_source_artifacts",
        "studio_draft_snapshots",
        "studio_draft_audit_events",
    ):
        assert f'"{table}"' in immutable_upgrade
    assert "CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON" in source
    assert "prevent_studio_immutable_mutation" in source
    assert "app.studio_demo_purge_tenant_id" in source
    assert "app.studio_demo_purge_session_id" in source
    assert "demo.status = 'purging'" in source
    assert "demo.fixture_tenant_id <> demo.tenant_id" in source
    assert "app.studio_retention_purge_reason" not in source
    assert "ck_studio_source_artifacts_format_media" in source
    assert "app.studio_orphan_cleanup_tenant_id" in source
    assert "app.studio_orphan_cleanup_cutoff" in source
    assert "NOT EXISTS (" in immutable_upgrade
    assert "snapshot.source_artifact_id = OLD.id" in immutable_upgrade
    assert '["tenant_id", "source_artifact_id"]' in source
    assert (
        '["studio_source_artifacts.tenant_id", "studio_source_artifacts.id"]' in source
    )


def test_studio_model_and_migration_table_column_parity():
    from app.models.studio_draft import (
        StudioDraft,
        StudioDraftAuditEvent,
        StudioDraftField,
        StudioDraftIdempotency,
        StudioDraftPlacement,
        StudioDraftSnapshot,
        StudioSourceArtifact,
    )

    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "147_studio_drafts.py"
    ).read_text(encoding="utf-8")
    for model in (
        StudioDraft,
        StudioDraftField,
        StudioDraftPlacement,
        StudioDraftSnapshot,
        StudioDraftIdempotency,
        StudioDraftAuditEvent,
        StudioSourceArtifact,
    ):
        assert f'"{model.__tablename__}"' in source
        for column in model.__table__.columns:
            assert f'"{column.name}"' in source
