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
    assert "MIGRATION_DIFF_BASE=$migration_diff_base" in workflow_text


def test_retired_skynet_workflow_is_verification_only_and_pins_commit() -> None:
    deploy = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    host_entrypoint = (ROOT / "scripts" / "lawhand-deploy-from-github").read_text(
        encoding="utf-8"
    )
    host_deploy = (ROOT / "scripts" / "deploy_skynet_runner.sh").read_text(
        encoding="utf-8"
    )

    assert "Verify retired Skynet production runner" in deploy
    assert "Runner verification must be dispatched from main" in deploy
    assert "Pin the exact commit" in deploy
    assert "runs-on: [self-hosted, linux, x64, skynet, lawhand-prod]" in deploy
    assert "actions/checkout" not in deploy
    assert 'lawhand-deploy-from-github verify "$RELEASE_SHA"' in deploy
    assert "lawhand-deploy-from-github deploy" not in deploy
    assert "Advance production release marker" not in deploy
    assert "git/refs/tags/production" not in deploy

    assert "rev-parse 'origin/main^{commit}'" in host_entrypoint
    assert '[[ "$requested_sha" == "$main_sha" ]]' in host_entrypoint
    assert 'reset --hard "$requested_sha"' in host_entrypoint
    assert '[[ "$checked_out_sha" == "$requested_sha" ]]' in host_entrypoint
    assert 'readonly DEPLOY_UID="$(id -u "$DEPLOY_USER")"' in host_entrypoint
    assert 'XDG_RUNTIME_DIR="$DEPLOY_RUNTIME_DIR"' in host_entrypoint
    assert (
        'DBUS_SESSION_BUS_ADDRESS="unix:path=$DEPLOY_RUNTIME_DIR/bus"'
        in host_entrypoint
    )

    # ENV_FILE is the child preflight's public input. Keeping a readonly local
    # with that name makes Bash reject the per-command environment assignment.
    assert 'readonly PROD_ENV_FILE="$ROOT_DIR/.env"' in host_deploy
    assert 'ENV_FILE="$PROD_ENV_FILE" COMPOSE_FILES="$COMPOSE_FILE"' in host_deploy
    assert "readonly ENV_FILE=" not in host_deploy


def test_production_acceptance_preflights_root_entrypoint_capability() -> None:
    workflow = (ROOT / ".github" / "workflows" / "production-acceptance.yml").read_text(
        encoding="utf-8"
    )
    entrypoint = (ROOT / "scripts" / "lawhand-ionos-deploy-from-github").read_text(
        encoding="utf-8"
    )

    assert "Run acceptance on IONOS" in workflow
    assert "runs-on: [self-hosted, Linux, X64, ionos, lawhand-prod]" in workflow
    assert "group: law-hand-ionos-production" in workflow
    assert "release_sha is not a forward update from the production tag" in workflow
    assert "Require successful CI and CodeQL for accepted release" in workflow
    assert "for workflow in ci.yml codeql.yml" in workflow
    assert '--commit "$RELEASE_SHA"' in workflow
    assert "--event push" in workflow
    assert "Preflight root-owned acceptance entrypoint" in workflow
    assert 'if ! test -f "$entrypoint" || ! test -x "$entrypoint"' in workflow
    assert "if ! stat -c '%U:%G %a' \"$entrypoint\"" in workflow
    assert "root:root 755" in workflow
    assert (
        "if ! grep -Fqx '  verify|stage|deploy|accept) ;;' \"$entrypoint\""
        in workflow
    )
    assert (
        "Operator action: install the versioned scripts/lawhand-ionos-deploy-from-github"
        in workflow
    )
    assert (
        "sudo -n /usr/local/sbin/lawhand-ionos-deploy-from-github accept"
        in workflow
    )
    assert "Advance production release marker" in workflow
    assert "needs: [release-gate, production]" in workflow
    assert "contents: write" in workflow
    assert "main moved during acceptance; production tag was not changed" in workflow
    assert "production tag moved during acceptance" in workflow
    assert "git/refs/tags/production" in workflow
    assert "  verify|stage|deploy|accept) ;;" in entrypoint


def test_ionos_candidate_uses_pinned_main_without_runner_checkout_or_release_tag() -> (
    None
):
    workflow = (
        ROOT / ".github" / "workflows" / "deploy-ionos-candidate.yml"
    ).read_text(encoding="utf-8")
    entrypoint = (ROOT / "scripts" / "lawhand-ionos-deploy-from-github").read_text(
        encoding="utf-8"
    )
    host_deploy = (ROOT / "scripts" / "deploy_ionos_runner.sh").read_text(
        encoding="utf-8"
    )

    assert "Require successful CI and CodeQL for mutation" in workflow
    assert "for workflow in ci.yml codeql.yml" in workflow
    assert '--commit "$RELEASE_SHA"' in workflow
    assert "--event push" in workflow
    assert "runs-on: [self-hosted, Linux, X64, ionos, lawhand-prod]" in workflow
    assert "environment:" in workflow and "ionos-production" in workflow
    assert "- accept" not in workflow
    assert "ACCEPT-IONOS-PRODUCTION" not in workflow
    assert "actions/checkout" not in workflow
    assert "git/refs/tags/production" not in workflow
    assert "sudo -n /usr/local/sbin/lawhand-ionos-deploy-from-github" in workflow
    assert "Require successful QA acceptance when enabled" in workflow
    assert "vars.LAWHAND_QA_GATE_REQUIRED == 'true'" in workflow
    assert "--workflow qa-acceptance.yml" in workflow
    assert "No successful QA acceptance exists" in workflow

    assert "rev-parse 'origin/main^{commit}'" in entrypoint
    assert '[[ "$requested_sha" == "$main_sha" ]]' in entrypoint
    assert 'reset --hard "$requested_sha"' in entrypoint
    assert "  verify|stage|deploy|accept) ;;" in entrypoint
    assert 'readonly APP_DIR="/srv/lawhand/app"' in entrypoint
    assert 'readonly PROD_ENV_FILE="/etc/lawhand/core.env"' in host_deploy
    assert "docker-compose.cube-m.yml" in host_deploy


def test_qa_acceptance_deploys_and_validates_exact_main() -> None:
    workflow = (ROOT / ".github" / "workflows" / "qa-acceptance.yml").read_text(
        encoding="utf-8"
    )
    health = (ROOT / ".github" / "workflows" / "dev1-health.yml").read_text(
        encoding="utf-8"
    )

    assert "name: QA acceptance" in workflow
    assert "group: law-hand-skynet-dev1" in workflow
    assert "QA acceptance must be dispatched from main" in workflow
    assert "release_sha must be a full lowercase commit SHA" in workflow
    assert "release_sha must equal the main SHA selected for this dispatch" in workflow
    assert "for workflow in ci.yml codeql.yml" in workflow
    assert "runs-on: [self-hosted, Linux, X64, skynet, lawhand-prod]" in workflow
    assert "environment:" in workflow and "skynet-development" in workflow
    qa_deploy_block = workflow.split("  qa-deploy:", 1)[1].split(
        "  qa-acceptance:", 1
    )[0]
    assert "actions/checkout" not in qa_deploy_block
    assert (
        'sudo -n /usr/local/sbin/lawhand-dev1-deploy-from-github deploy "$RELEASE_SHA"'
        in workflow
    )
    assert "LAWHAND_QA_ACCESS_CLIENT_ID" in workflow
    assert "LAWHAND_QA_ACCESS_CLIENT_SECRET" in workflow
    assert "CF-Access-Client-Id" in workflow
    assert "CF-Access-Client-Secret" in workflow
    assert '.commit == $expected' in workflow
    assert "LAWHAND_QA_HOSTNAME is missing or invalid" in workflow
    assert "Checkout exact QA API smoke harness" in workflow
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in workflow
    assert "LAWHAND_QA_DEMO_ACCESS_CODE" in workflow
    assert "python scripts/demo_live_smoke.py" in workflow
    assert "QA_DEMO_ACCESS_CODE: ${{ secrets.LAWHAND_QA_DEMO_ACCESS_CODE }}" in workflow
    assert "if: secrets.LAWHAND_QA_DEMO_ACCESS_CODE" not in workflow
    assert "if: env.QA_DEMO_ACCESS_CODE != ''" in workflow
    assert "Synthetic smoke: optional" in workflow

    assert "vars.LAWHAND_DEV1_ENABLED == 'true'" in health
    assert "skynet-development" in health
    assert "LAWHAND_QA_ACCESS_CLIENT_ID" in health
    assert "CF-Access-Client-Secret" in health
    assert 'test("^[0-9a-f]{40}$")' in health
