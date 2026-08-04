from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from check_migration_safety import analyze_upgrade_source, evaluate_changes  # noqa: E402


MIGRATION = Path("backend/migrations/versions/100_candidate.py")


def _messages(source: str) -> list[str]:
    return [finding.message for finding in analyze_upgrade_source(source, MIGRATION)]


def test_additive_migration_with_scoped_backfill_is_allowed() -> None:
    source = """
from alembic import op
import sqlalchemy as sa

def upgrade():
    op.add_column("users", sa.Column("display_code", sa.String(), nullable=True))
    op.execute("UPDATE users SET display_code = id::text WHERE display_code IS NULL")
    op.create_index("ix_users_display_code", "users", ["display_code"])

def downgrade():
    op.drop_index("ix_users_display_code", table_name="users")
    op.drop_column("users", "display_code")
"""

    assert _messages(source) == []


def test_destructive_downgrade_is_not_mistaken_for_upgrade_risk() -> None:
    source = """
from alembic import op

def upgrade():
    op.create_table("safe_addition")

def downgrade():
    op.execute("TRUNCATE TABLE customer_data")
    op.drop_table("safe_addition")
"""

    assert _messages(source) == []


def test_destructive_upgrade_operations_are_rejected() -> None:
    source = """
from alembic import op

def upgrade():
    op.drop_column("users", "email")
    op.drop_constraint("uq_users_tenant_email", "users")
    op.execute("DELETE FROM documents WHERE status = 'old'")
    op.execute("TRUNCATE TABLE matters")

def downgrade():
    pass
"""

    messages = _messages(source)
    assert any("op.drop_column" in message for message in messages)
    assert any("op.drop_constraint" in message for message in messages)
    assert any("DELETE FROM" in message for message in messages)
    assert any("TRUNCATE" in message for message in messages)


def test_unscoped_rewrite_and_in_place_constraint_are_rejected() -> None:
    source = """
from alembic import op
import sqlalchemy as sa

def upgrade():
    op.execute("UPDATE users SET tenant_id = '00000000-0000-0000-0000-000000000000'")
    op.alter_column("users", "email", nullable=False)
    op.alter_column("users", "email", type_=sa.Text())
"""

    messages = _messages(source)
    assert any("unscoped UPDATE" in message for message in messages)
    assert any("nullable=False" in message for message in messages)
    assert any("type_" in message for message in messages)


def test_temporary_force_rls_relaxation_must_be_restored() -> None:
    restored = """
from alembic import op

def upgrade():
    table = "users"
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE users SET full_name = 'Unknown' WHERE full_name IS NULL")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
"""
    weakened = """
from alembic import op

def upgrade():
    op.execute("ALTER TABLE users DISABLE ROW LEVEL SECURITY")
"""

    assert _messages(restored) == []
    assert any("RLS is disabled" in message for message in _messages(weakened))


def test_existing_revision_changes_are_rejected() -> None:
    _additions, findings = evaluate_changes(
        [("M", Path("backend/migrations/versions/099_existing.py"), None)]
    )

    assert len(findings) == 1
    assert "immutable" in findings[0].message


def test_ci_exposes_named_tenant_data_safety_gate() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["tenant-data-safety"]
    step_names = {step["name"] for step in job["steps"]}

    assert job["name"] == "Tenant data safety - migrations and RLS"
    assert job["env"]["PYTHONPATH"] == "backend"
    assert "Reject destructive or rewritten migrations" in step_names
    assert "Resolve the deployed production migration baseline" in step_names
    assert "Rehearse upgrade over two-tenant customer data" in step_names
    assert "Verify tenant schema and effective isolation" in step_names

    workflow_text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "refs/tags/production:refs/tags/production" in workflow_text
    assert 'MIGRATION_DIFF_BASE=$migration_diff_base' in workflow_text


def test_production_deploy_pins_commit_and_requires_its_ci_run() -> None:
    deploy = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    host_entrypoint = (ROOT / "scripts" / "lawhand-deploy-from-github").read_text(
        encoding="utf-8"
    )

    assert "Require successful CI for exact release commit" in deploy
    assert '[[ "$RELEASE_REF" != refs/heads/main ]]' in deploy
    assert 'head_sha="$RELEASE_SHA"' in deploy
    assert '[[ "$conclusion" != success ]]' in deploy
    assert "runs-on: [self-hosted, linux, x64, skynet, lawhand-prod]" in deploy
    assert "environment:" in deploy
    assert "actions/checkout" not in deploy
    assert "sudo -n /usr/local/sbin/lawhand-deploy-from-github" in deploy
    assert "Advance production release marker" in deploy
    assert "git/refs/tags/production" in deploy

    assert "rev-parse 'origin/main^{commit}'" in host_entrypoint
    assert '[[ "$requested_sha" == "$main_sha" ]]' in host_entrypoint
    assert 'reset --hard "$requested_sha"' in host_entrypoint
    assert '[[ "$checked_out_sha" == "$requested_sha" ]]' in host_entrypoint
