from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/migrations/versions/149_sms_lifecycle.py"
OLD_MIGRATION = ROOT / "backend/migrations/versions/148_sms_lifecycle.py"
CI = ROOT / ".github/workflows/ci.yml"


def test_sms_is_the_single_migration_after_configurable_workflows():
    source = MIGRATION.read_text(encoding="utf-8")

    assert MIGRATION.exists()
    assert not OLD_MIGRATION.exists()
    assert 'revision = "149_sms_lifecycle"' in source
    assert 'down_revision = "148_configurable_workflows"' in source
    assert 'op.drop_constraint("uq_contacts_tenant_id"' not in source
    assert '("contacts", "uq_contacts_tenant_id")' not in source


def test_sms_migration_enforces_rls_and_tenant_composite_references():
    source = MIGRATION.read_text(encoding="utf-8")

    for table in ("sms_provider_configs", "sms_messages", "sms_review_items"):
        assert table in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid" in source
    for constraint in (
        "uq_communication_logs_tenant_id",
        "uq_sms_messages_tenant_id",
        "fk_sms_provider_configs_tenant_user",
        "fk_sms_messages_tenant_contact",
        "fk_sms_messages_tenant_matter",
        "fk_sms_messages_tenant_communication",
        "fk_sms_messages_tenant_user",
        "fk_sms_review_items_tenant_message",
        "fk_sms_review_items_tenant_user",
    ):
        assert constraint in source


def test_ci_rehearses_sms_from_148_without_moving_comp09s_head_assertion():
    source = CI.read_text(encoding="utf-8")

    assert "sms-lifecycle-rehearsal:" in source
    assert "alembic upgrade 148_configurable_workflows" in source
    assert "alembic upgrade 149_sms_lifecycle" in source
    assert "test_sms_lifecycle_db.py" in source
