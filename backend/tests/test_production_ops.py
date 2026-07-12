from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "scripts" / "prod_env_preflight.sh"
BASH_BIN = os.environ.get("BASH", "bash")
NEW_FERNET_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
OLD_FERNET_KEY = "KxzLuxmIM2dFDWQmKJL9LVUK5ouA0c3_-4VqCMrn-jY="


def _production_env(**overrides: str) -> str:
    values = {
        "DOMAIN": "ops-test.invalid",
        "BACKEND_URL": "https://ops-test.invalid",
        "FRONTEND_URL": "https://ops-test.invalid",
        "VITE_CONTACT_URL": "mailto:ops@example.invalid",
        "DEV_MODE": "false",
        "PUBLIC_SIGNUP_ENABLED": "false",
        "VITE_PUBLIC_SIGNUP_ENABLED": "false",
        "SECRET_KEY": "ops-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz",
        "MCP_PRODUCT_ENABLED": "false",
        "POSTGRES_PASSWORD": "owner-password-0123456789",
        "CLARITY_APP_PASSWORD": "runtime-password-0123456789",
        "REDIS_PASSWORD": "redis-password-0123456789",
        "REDIS_URL": "redis://:redis-password-0123456789@redis:6379/0",
        "MIGRATOR_DATABASE_URL": "postgresql+asyncpg://legalapp:owner-password-0123456789@postgres:5432/legalapp",
        "APP_DATABASE_URL": "postgresql+asyncpg://clarity_app:runtime-password-0123456789@postgres:5432/legalapp",
        "LITELLM_API_KEY": "litellm-api-key-0123456789",
        "LITELLM_SALT_KEY": "permanent-litellm-salt-key-0123456789",
        "LITELLM_DB_PASSWORD": "litellm-password-0123456789",
        "LITELLM_DATABASE_URL": "postgresql://litellm:litellm-password-0123456789@litellm-postgres:5432/litellm",
        "TOKEN_ENCRYPTION_KEY": OLD_FERNET_KEY,
        "TOKEN_ENCRYPTION_KEYS": f"{NEW_FERNET_KEY},{OLD_FERNET_KEY}",
        "MCP_SERVER_URL": "http://courtlistener-mcp:8000",
        "MCP_UPSTREAM_API_KEY": "mcp-upstream-key-0123456789-abcdef",
        "UPLOADS_HOST_DIR": "/srv/legalapp/uploads",
        "OFFSITE_BACKUP_REQUIRED": "true",
        "OFFSITE_RESTORE_PUBLIC_KEY_FILE": "__TEST_OFFSITE_PUBLIC_KEY__",
        "EMAIL_ENABLED": "true",
        "EMAIL_HOST": "smtp.ops-test.invalid",
        "EMAIL_PORT": "587",
        "EMAIL_USER": "operator@ops-test.invalid",
        "EMAIL_PASS": "smtp-password-0123456789",
        "EMAIL_FROM": "noreply@ops-test.invalid",
        "ZOOM_REQUIRED_TENANT_ID": "00000000-0000-4000-8000-000000000111",
        "ZOOM_REQUIRED_TENANT_PLAN": "intake-only",
    }
    values.update(overrides)
    return "".join(f"{key}={value}\n" for key, value in values.items())


def _run_preflight(
    tmp_path: Path,
    env_text: str,
    *,
    process_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / ".env"
    restore_public_key = tmp_path / "offsite-restore-public.pem"
    restore_public_key.write_text("test public key placeholder\n", encoding="utf-8")
    env_text = env_text.replace("__TEST_OFFSITE_PUBLIC_KEY__", str(restore_public_key))
    env_file.write_text(env_text, encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    env = os.environ.copy()
    # Preflight rejects process variables that would override the validated
    # env file during Compose interpolation. CI exports several such values for
    # the backend itself, so sanitize every guarded key before applying a
    # test's explicit conflict override.
    guarded_keys = {
        match.group(1)
        for match in re.finditer(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)",
            (ROOT / "docker-compose.hypervisor.yml").read_text(encoding="utf-8"),
        )
    }
    guarded_keys.update(
        line.split("=", 1)[0]
        for line in env_text.splitlines()
        if "=" in line and line.split("=", 1)[0]
    )
    for key in guarded_keys:
        env.pop(key, None)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ENV_FILE"] = str(env_file)
    env["COMPOSE_FILES"] = str(ROOT / "docker-compose.hypervisor.yml")
    env.update(process_overrides or {})
    return subprocess.run(
        [BASH_BIN, str(PREFLIGHT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_production_preflight_accepts_staged_keyring_and_dedicated_mcp_auth(
    tmp_path: Path,
) -> None:
    result = _run_preflight(tmp_path, _production_env())
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "Production preflight passed" in output
    assert "ops-secret-key-0123456789" not in output
    assert "mcp-upstream-key-0123456789" not in output


def test_production_preflight_rejects_launch_flags_and_unstaged_credentials(
    tmp_path: Path,
) -> None:
    result = _run_preflight(
        tmp_path,
        _production_env(
            MCP_PRODUCT_ENABLED="true",
            PUBLIC_SIGNUP_ENABLED="true",
            VITE_PUBLIC_SIGNUP_ENABLED="true",
            MCP_UPSTREAM_API_KEY="",
            TOKEN_ENCRYPTION_KEYS=OLD_FERNET_KEY,
        ),
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "MCP_PRODUCT_ENABLED must remain false" in output
    assert "PUBLIC_SIGNUP_ENABLED must remain false" in output
    assert "VITE_PUBLIC_SIGNUP_ENABLED must remain false" in output
    assert "MCP_UPSTREAM_API_KEY must be at least 32 characters" in output
    assert "TOKEN_ENCRYPTION_KEYS must contain at least new_key,old_key" in output
    assert OLD_FERNET_KEY not in output


def test_production_preflight_rejects_public_signup_flag_drift(tmp_path: Path) -> None:
    result = _run_preflight(
        tmp_path,
        _production_env(
            PUBLIC_SIGNUP_ENABLED="false",
            VITE_PUBLIC_SIGNUP_ENABLED="true",
        ),
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "VITE_PUBLIC_SIGNUP_ENABLED must remain false" in output
    assert "PUBLIC_SIGNUP_ENABLED and VITE_PUBLIC_SIGNUP_ENABLED must match" in output


def test_production_preflight_rejects_shared_upstream_auth(tmp_path: Path) -> None:
    shared_secret = "shared-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz"
    result = _run_preflight(
        tmp_path,
        _production_env(
            SECRET_KEY=shared_secret,
            MCP_UPSTREAM_API_KEY=shared_secret,
        ),
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "MCP_UPSTREAM_API_KEY must be a dedicated credential" in output
    assert shared_secret not in output


def test_production_preflight_rejects_missing_or_rotatable_litellm_salt(
    tmp_path: Path,
) -> None:
    api_key = "litellm-api-key-0123456789-abcdef"
    result = _run_preflight(
        tmp_path,
        _production_env(LITELLM_API_KEY=api_key, LITELLM_SALT_KEY=api_key),
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "LITELLM_SALT_KEY must be permanent and distinct" in output
    assert api_key not in output


def test_production_preflight_rejects_conflicting_inherited_compose_value(
    tmp_path: Path,
) -> None:
    inherited_secret = "different-process-password-never-print"
    result = _run_preflight(
        tmp_path,
        _production_env(),
        process_overrides={"POSTGRES_PASSWORD": inherited_secret},
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "inherited POSTGRES_PASSWORD conflicts" in output
    assert inherited_secret not in output


def test_production_preflight_rejects_disabled_email_for_strict_launch(
    tmp_path: Path,
) -> None:
    result = _run_preflight(
        tmp_path,
        _production_env(EMAIL_ENABLED="false", EMAIL_USER="", EMAIL_PASS=""),
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "EMAIL_ENABLED must be true for the sold-tenant launch" in output


def test_litellm_release_contract_is_pinned_and_fail_closed() -> None:
    dockerfile = (ROOT / "litellm" / "Dockerfile").read_text(encoding="utf-8")
    assert "main-latest@sha256:60f548df23a82b7f" in dockerfile
    assert "apk add" not in dockerfile
    assert "reconcile_schema.sh" in dockerfile
    assert 'ENTRYPOINT ["/app/legalapp/runtime_entrypoint.sh"]' in dockerfile

    reconciler = (ROOT / "litellm" / "reconcile_schema.sh").read_text(encoding="utf-8")
    assert "f2d45d3252af3b35d4b223cba74a56c4" in reconciler
    assert "e151961addd5f1146dd1c8fbd98b69cb" in reconciler
    assert "unreviewed LiteLLM schema drift" in reconciler
    assert "LITELLM_SCHEMA_REPAIR_ALLOWED" in reconciler

    runtime_entrypoint = (ROOT / "litellm" / "runtime_entrypoint.sh").read_text(
        encoding="utf-8"
    )
    assert "LITELLM_SCHEMA_REPAIR_ALLOWED=false" in runtime_entrypoint
    assert 'exec /app/docker/prod_entrypoint.sh "$@"' in runtime_entrypoint

    for compose_name in ("docker-compose.yml", "docker-compose.hypervisor.yml"):
        config = yaml.safe_load((ROOT / compose_name).read_text(encoding="utf-8"))
        services = config["services"]
        proxy = services["litellm"]
        assert "LITELLM_SALT_KEY" in proxy["environment"]
        assert proxy["environment"]["DISABLE_SCHEMA_UPDATE"] == "true"
        assert proxy["depends_on"] == {
            "litellm-schema-migrator": {"condition": "service_completed_successfully"}
        }
        assert proxy["healthcheck"]["start_period"] == "90s"
        assert proxy["healthcheck"]["retries"] == 5
        assert services["litellm-migrator"]["entrypoint"] == ["prisma"]
        assert services["litellm-migrator"]["pull_policy"] == "never"
        assert services["litellm-schema-migrator"]["command"] == [
            "/app/legalapp/reconcile_schema.sh"
        ]
        assert (
            services["litellm-schema-migrator"]["environment"][
                "LITELLM_SCHEMA_REPAIR_ALLOWED"
            ]
            == "true"
        )
        assert services["litellm-schema-migrator"]["pull_policy"] == "never"

    hypervisor = yaml.safe_load(
        (ROOT / "docker-compose.hypervisor.yml").read_text(encoding="utf-8")
    )["services"]
    for service in (
        "postgres",
        "redis",
        "litellm-postgres",
        "litellm",
        "backend",
        "scheduler",
        "frontend",
        "nginx",
    ):
        assert hypervisor[service]["restart"] == "unless-stopped"
    for service in ("litellm-migrator", "litellm-schema-migrator", "migrator"):
        assert hypervisor[service]["restart"] == "no"


def test_production_signup_flags_are_explicitly_mapped_and_rollback_images_remain() -> (
    None
):
    for compose_name in ("docker-compose.yml", "docker-compose.hypervisor.yml"):
        services = yaml.safe_load((ROOT / compose_name).read_text(encoding="utf-8"))[
            "services"
        ]
        assert services["backend"]["environment"]["PUBLIC_SIGNUP_ENABLED"] == (
            "${PUBLIC_SIGNUP_ENABLED:-false}"
        )
        assert (
            services["frontend"]["build"]["args"]["VITE_PUBLIC_SIGNUP_ENABLED"]
            == "${VITE_PUBLIC_SIGNUP_ENABLED:-false}"
        )

    prod_services = yaml.safe_load(
        (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )["services"]
    for service in ("backend", "scheduler"):
        assert prod_services[service]["environment"]["PUBLIC_SIGNUP_ENABLED"] == (
            "${PUBLIC_SIGNUP_ENABLED:-false}"
        )
    assert (
        prod_services["frontend"]["build"]["args"]["VITE_PUBLIC_SIGNUP_ENABLED"]
        == "${VITE_PUBLIC_SIGNUP_ENABLED:-false}"
    )

    deploy = (ROOT / "scripts" / "deploy_prod.sh").read_text(encoding="utf-8")
    assert "docker image prune" not in deploy
    assert "rollback-before-$release_tag" in deploy
    assert "release-$release_tag" in deploy
    assert "rollback_manifest" in deploy
    assert 'APP_COMMIT" == "$git_commit' in deploy


def test_upload_bind_scheduler_and_launch_capability_contracts() -> None:
    base = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))[
        "services"
    ]
    hypervisor = yaml.safe_load(
        (ROOT / "docker-compose.hypervisor.yml").read_text(encoding="utf-8")
    )["services"]
    prod = yaml.safe_load(
        (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )["services"]
    assert base["backend"]["volumes"] == ["${UPLOADS_HOST_DIR:-./uploads}:/app/uploads"]
    for services in (hypervisor, prod):
        for service in ("backend", "scheduler"):
            assert any(
                "UPLOADS_HOST_DIR" in mount for mount in services[service]["volumes"]
            )
            for email_key in (
                "EMAIL_ENABLED",
                "EMAIL_HOST",
                "EMAIL_PORT",
                "EMAIL_USER",
                "EMAIL_PASS",
                "EMAIL_FROM",
            ):
                assert email_key in services[service]["environment"]
    assert prod["scheduler"]["healthcheck"] == hypervisor["scheduler"]["healthcheck"]

    deploy = (ROOT / "scripts" / "deploy_prod.sh").read_text(encoding="utf-8")
    assert "chown 10001:10001 /legalapp-uploads" in deploy
    assert "legalapp-upload-proof" in deploy
    assert "litellm-schema-migrator" in deploy
    assert "create_manual_recovery_bundle.sh" in deploy
    assert "restore_manual_recovery_bundle.sh" in deploy
    assert "MANUAL_OFFSITE_WAIT_SECONDS" in deploy

    production_check = (ROOT / "scripts" / "production_check.sh").read_text(
        encoding="utf-8"
    )
    assert "ZOOM_REQUIRED_TENANT_ID" in production_check
    assert "ZOOM_REQUIRED_TENANT_PLAN" in production_check
    assert "custom_config->>'plan'" in production_check
    assert '--tenant-id "$ZOOM_REQUIRED_TENANT_ID"' in production_check
    assert "SMTP no-delivery capability probe" in production_check
    assert "client.starttls" in production_check
    assert "client.login" in production_check
    assert "scheduler runtime SMTP configuration is disabled or incomplete" in (
        production_check
    )
    assert "backend and scheduler runtime SMTP configurations differ" in production_check
    assert "inherited EMAIL_ENABLED conflicts" in production_check


def test_production_guards_cover_litellm_data_and_schema() -> None:
    data_guard = (ROOT / "scripts" / "prod_data_guard.sh").read_text(encoding="utf-8")
    production_check = (ROOT / "scripts" / "production_check.sh").read_text(
        encoding="utf-8"
    )
    backup = (ROOT / "scripts" / "backup_db.sh").read_text(encoding="utf-8")
    restore = (ROOT / "scripts" / "restore_rehearsal.sh").read_text(encoding="utf-8")
    manual_restore = (ROOT / "scripts" / "restore_manual_recovery_bundle.sh").read_text(
        encoding="utf-8"
    )

    assert "LITELLM_PREDEPLOY_BACKUP=" in data_guard
    assert 'assert_counts_not_decreased "LiteLLM"' in data_guard
    assert 'postdeploy "${1:-}" "${2:-}"' in data_guard
    assert "umask 077" in data_guard
    assert "SELECT pg_export_snapshot();" in data_guard
    assert '--snapshot="$snapshot_id"' in data_guard
    assert data_guard.count("SET TRANSACTION SNAPSHOT :'snapshot_id';") == 2
    assert "prisma migrate diff --exit-code" in production_check
    assert "for service in postgres redis litellm-postgres litellm backend" in (
        production_check
    )
    assert "LITELLM_BACKUP_FILE" in backup
    assert "SELECT pg_export_snapshot();" in backup
    assert "SNAPSHOT_EXPORT_TIMEOUT_SECONDS:=30" in backup
    assert 'read -r -t "$SNAPSHOT_EXPORT_TIMEOUT_SECONDS"' in backup
    assert '--snapshot="$snapshot_id"' in backup
    assert backup.count("SET TRANSACTION SNAPSHOT :'snapshot_id';") == 2
    assert "create_consistent_database_backup" in backup
    assert '"$COUNTS_FILE" snapshot_app_counts' in backup
    assert "snapshot_litellm_counts" in backup
    assert "close_exported_snapshot abort" in backup
    assert 'install -m 600 "$ENV_FILE" "$ESCROW_FILE"' in backup
    assert '"$ESCROW_FILE"' in backup
    assert 'backup_paths+=("$CERTS_DIR")' in backup
    assert "upload_backup_artifact.py" in backup
    assert '"$UPLOAD_ARCHIVE" "$UPLOAD_MANIFEST" "$UPLOAD_CHECKSUM_FILE"' in backup
    assert "matching immutable upload artifact" in restore
    assert "upload_backup_artifact.py" in restore
    assert "umask 077" in backup
    assert "load_env_defaults" in backup
    assert "inherited_env" in backup
    assert "LITELLM_CONTAINER" in restore
    assert "matching encrypted environment/key escrow" in restore
    assert "escrow_has_value LITELLM_SALT_KEY" in restore
    assert "escrow_has_value TOKEN_ENCRYPTION_KEYS" in restore
    assert "isolated-clean-host-restore" in manual_restore
    assert "legalapp-restored.counts.tsv" in manual_restore
    assert "litellm-restored.counts.tsv" in manual_restore
    assert "upload_backup_artifact.py" in manual_restore
    assert "Escrowed TLS certificate and private key do not match" in manual_restore
    assert "MANUAL_RESTORE_PROOF=" in manual_restore
    assert "OFFSITE_RESTORE_SIGNING_KEY_FILE" in manual_restore
    assert "openssl dgst -sha256 -sign" in manual_restore


def test_tls_and_recurring_backup_ops_support_multi_compose() -> None:
    for script_name in ("init-letsencrypt.sh", "renew-cert.sh"):
        script = (ROOT / "nginx" / script_name).read_text(encoding="utf-8")
        assert "COMPOSE_FILES" in script
        assert "COMPOSE_FILE_LIST" in script
        assert "COMPOSE+=( -f" in script

    service = (ROOT / "ops" / "systemd" / "legalapp-backup.service.in").read_text(
        encoding="utf-8"
    )
    timer = (ROOT / "ops" / "systemd" / "legalapp-backup.timer").read_text(
        encoding="utf-8"
    )
    installer = (ROOT / "scripts" / "install_backup_timer.sh").read_text(
        encoding="utf-8"
    )
    assert "OFFSITE_BACKUP_REQUIRED=true" in service
    assert "PRUNE_OLD_BACKUPS=false" in service
    assert 'Environment="PATH=@EXEC_PATH@"' in service
    assert "OnFailure=" in service
    assert "RandomizedDelaySec=" in timer and "Persistent=true" in timer
    assert "RESTIC_REPOSITORY must be configured" in installer
    assert 'RESTIC_BIN="$(command -v restic)"' in installer
    assert "@EXEC_PATH@" in installer


def test_fresh_host_workflow_rehearses_both_production_topologies() -> None:
    workflow = (ROOT / ".github" / "workflows" / "fresh-host-rehearsal.yml").read_text(
        encoding="utf-8"
    )
    rehearsal = (ROOT / "scripts" / "rehearse_fresh_host.sh").read_text(
        encoding="utf-8"
    )

    assert "topology: [hypervisor, base-prod]" in workflow
    assert "FRESH_HOST_TOPOLOGY: ${{ matrix.topology }}" in workflow
    assert "OFFSITE_RESTORE_PUBLIC_KEY_FILE=" in rehearsal
