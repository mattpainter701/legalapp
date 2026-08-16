from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_revision_graph_resolves_heads():
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()

    assert heads == ["105_live_demo_foundation"]


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
