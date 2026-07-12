from __future__ import annotations

import json
import os
import re
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "scripts" / "prod_env_preflight.sh"
CAPACITY_CHECK = ROOT / "scripts" / "check_host_capacity.sh"
PRODUCTION_CHECK = ROOT / "scripts" / "production_check.sh"
BASH_BIN = os.environ.get("BASH", "bash")
NEW_FERNET_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
OLD_FERNET_KEY = "KxzLuxmIM2dFDWQmKJL9LVUK5ouA0c3_-4VqCMrn-jY="


def _production_env(**overrides: str) -> str:
    values = {
        "DOMAIN": "ops-test.invalid",
        "BACKEND_URL": "https://ops-test.invalid",
        "FRONTEND_URL": "https://ops-test.invalid",
        "VITE_PUBLIC_SITE_URL": "https://ops-test.invalid",
        "VITE_CONTACT_URL": "mailto:ops@example.invalid",
        "DEV_MODE": "false",
        "PUBLIC_SIGNUP_ENABLED": "false",
        "VITE_PUBLIC_SIGNUP_ENABLED": "false",
        "SECRET_KEY": "ops-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz",
        "MCP_PRODUCT_ENABLED": "false",
        "PLATFORM_LEGACY_BOOTSTRAP_ENABLED": "false",
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
        "HOST_STATUS_HOST_DIR": "/srv/legalapp/host-status",
        "HOST_DISK_STATUS_FILE": "/run/legalapp-host-status/disk-status.json",
        "HEALTH_HOST_DISK_MAX_AGE_SECONDS": "180",
        "DISK_PATH": "/",
        "DISK_MAX_PERCENT": "85",
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
    compose_files: str | None = None,
    process_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / ".env"
    restore_public_key = tmp_path / "offsite-restore-public.pem"
    restore_public_key.write_text("test public key placeholder\n", encoding="utf-8")
    env_text = env_text.replace("__TEST_OFFSITE_PUBLIC_KEY__", str(restore_public_key))
    env_file.write_text(env_text, encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = info ]; then printf '/var/lib/docker\\n'; fi\n"
        'if [ "${1:-}" = compose ]; then\n'
        "  printf '%s\\n' \"$FAKE_COMPOSE_CONFIG_JSON\"\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    python3 = fake_bin / "python3"
    python3.write_text(
        "#!/bin/sh\nprintf '%s' \"$FAKE_BIND_SOURCES\"\n",
        encoding="utf-8",
    )
    python3.chmod(python3.stat().st_mode | stat.S_IXUSR)
    env = os.environ.copy()
    # Preflight rejects process variables that would override the validated
    # env file during Compose interpolation. CI exports several such values for
    # the backend itself, so sanitize every guarded key before applying a
    # test's explicit conflict override.
    selected_compose_files = (
        compose_files or (ROOT / "docker-compose.hypervisor.yml").as_posix()
    )
    guarded_keys: set[str] = set()
    for selected_compose_file in selected_compose_files.split():
        guarded_keys.update(
            match.group(1)
            for match in re.finditer(
                r"\$\{([A-Za-z_][A-Za-z0-9_]*)",
                Path(selected_compose_file).read_text(encoding="utf-8"),
            )
        )
    guarded_keys.update(
        line.split("=", 1)[0]
        for line in env_text.splitlines()
        if "=" in line and line.split("=", 1)[0]
    )
    for key in guarded_keys:
        env.pop(key, None)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ENV_FILE"] = str(env_file)
    env["COMPOSE_FILES"] = selected_compose_files
    env["HOST_CAPACITY_OVERRIDE"] = "true"
    env["HOST_CAPACITY_OVERRIDE_REASON"] = (
        "isolated preflight unit test; not production evidence"
    )
    env["FAKE_COMPOSE_CONFIG_JSON"] = json.dumps(
        {
            "services": {
                "postgres": {
                    "volumes": [
                        {
                            "type": "bind",
                            "source": "/data/legalapp/postgres",
                            "target": "/var/lib/postgresql/data",
                        }
                    ]
                },
                "litellm-postgres": {
                    "volumes": [
                        {
                            "type": "bind",
                            "source": "/data/legalapp/litellm-postgres",
                            "target": "/var/lib/postgresql/data",
                        }
                    ]
                },
            }
        }
    )
    env["FAKE_BIND_SOURCES"] = (
        "/data/legalapp/postgres\n/data/legalapp/litellm-postgres\n"
    )
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


def _run_production_policy_check(
    tmp_path: Path,
    *,
    legacy_value: str | None,
    mcp_value: str | None = "false",
    process_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env_text = _production_env()
    lines = [
        line
        for line in env_text.splitlines()
        if not line.startswith(
            ("PLATFORM_LEGACY_BOOTSTRAP_ENABLED=", "MCP_PRODUCT_ENABLED=")
        )
    ]
    if legacy_value is not None:
        lines.append(f"PLATFORM_LEGACY_BOOTSTRAP_ENABLED={legacy_value}")
    if mcp_value is not None:
        lines.append(f"MCP_PRODUCT_ENABLED={mcp_value}")
    env_file = tmp_path / "production-check.env"
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("PLATFORM_LEGACY_BOOTSTRAP_ENABLED", None)
    env.pop("MCP_PRODUCT_ENABLED", None)
    env["ENV_FILE"] = str(env_file)
    env["COMPOSE_FILES"] = str(ROOT / "docker-compose.hypervisor.yml")
    env["ZOOM_REQUIRED"] = "false"
    env["MONITOR_STATE_FILE"] = str(tmp_path / "monitor.state")
    env.update(process_overrides or {})
    return subprocess.run(
        [BASH_BIN, str(PRODUCTION_CHECK)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def _validate_capacity(
    *,
    profile: str = "vps",
    cpus: int,
    memory_gib: int,
    disk_total_gib: int,
    disk_free_gib: int,
    disk_used_gib: int | None = None,
    disk_max_percent: int = 85,
) -> subprocess.CompletedProcess[str]:
    kib_per_gib = 1024 * 1024
    if disk_used_gib is None:
        disk_used_gib = disk_total_gib - disk_free_gib
    command = (
        f"source {shlex.quote(CAPACITY_CHECK.as_posix())}; "
        f"DISK_MAX_PERCENT={disk_max_percent} validate_capacity "
        f"{shlex.quote(profile)} {cpus} {memory_gib * kib_per_gib} "
        f"{disk_total_gib * kib_per_gib} {disk_used_gib * kib_per_gib} "
        f"{disk_free_gib * kib_per_gib} /data"
    )
    return subprocess.run(
        [BASH_BIN, "-c", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _extract_bind_sources(model: object) -> subprocess.CompletedProcess[str]:
    command = (
        f'python3() {{ {shlex.quote(Path(sys.executable).as_posix())} "$@"; }}; '
        "export -f python3; "
        f"source {shlex.quote(CAPACITY_CHECK.as_posix())}; "
        "extract_compose_bind_sources"
    )
    return subprocess.run(
        [BASH_BIN, "-c", command],
        cwd=ROOT,
        input=json.dumps(model),
        capture_output=True,
        text=True,
        timeout=10,
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


def test_host_capacity_gate_accepts_supported_floor() -> None:
    result = _validate_capacity(
        cpus=8,
        memory_gib=24,
        disk_total_gib=160,
        disk_free_gib=31,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Host capacity passed" in result.stdout
    assert "DISK_MAX_PERCENT=85" in result.stdout
    assert "5.0 GiB build headroom" in result.stdout

    profile_floor = _validate_capacity(
        cpus=8,
        memory_gib=24,
        disk_total_gib=160,
        disk_free_gib=25,
        disk_max_percent=95,
    )
    assert profile_floor.returncode == 0, profile_floor.stdout + profile_floor.stderr
    assert "required 25.0 GiB" in profile_floor.stdout


def test_host_capacity_gate_rejects_16_gib_and_low_disk_headroom() -> None:
    result = _validate_capacity(
        cpus=4,
        memory_gib=16,
        disk_total_gib=120,
        disk_free_gib=12,
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "4 online CPU(s); at least 8 are required" in output
    assert "16.0 GiB RAM; at least 24.0 GiB is required" in output
    assert "120.0 GiB total" in output
    assert "12.0 GiB free" in output


def test_host_capacity_gate_uses_realistic_hypervisor_disk_profile() -> None:
    result = _validate_capacity(
        profile="hypervisor",
        cpus=16,
        memory_gib=62,
        disk_total_gib=98,
        disk_used_gib=77,
        disk_free_gib=21,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Host capacity passed (hypervisor)" in result.stdout

    low_headroom = _validate_capacity(
        profile="hypervisor",
        cpus=16,
        memory_gib=62,
        disk_total_gib=98,
        disk_used_gib=79,
        disk_free_gib=19,
    )
    assert low_headroom.returncode != 0
    output = low_headroom.stdout + low_headroom.stderr
    assert "at least 20.7 GiB is required" in output
    assert "profile floor 15.0 GiB" in output
    assert "DISK_MAX_PERCENT=85 reserve plus 5.0 GiB build headroom" in output


def test_host_capacity_gate_uses_configured_runtime_disk_threshold() -> None:
    default_threshold = _validate_capacity(
        profile="hypervisor",
        cpus=16,
        memory_gib=62,
        disk_total_gib=98,
        disk_used_gib=78,
        disk_free_gib=20,
    )
    assert default_threshold.returncode != 0

    relaxed_threshold = _validate_capacity(
        profile="hypervisor",
        cpus=16,
        memory_gib=62,
        disk_total_gib=98,
        disk_used_gib=78,
        disk_free_gib=20,
        disk_max_percent=90,
    )
    assert relaxed_threshold.returncode == 0, (
        relaxed_threshold.stdout + relaxed_threshold.stderr
    )
    assert "DISK_MAX_PERCENT=90" in relaxed_threshold.stdout


def test_production_preflight_rejects_invalid_disk_gate_configuration(
    tmp_path: Path,
) -> None:
    invalid_threshold = _run_preflight(
        tmp_path,
        _production_env(DISK_MAX_PERCENT="101"),
    )
    assert invalid_threshold.returncode != 0
    assert "DISK_MAX_PERCENT must be an integer from 1 to 100" in (
        invalid_threshold.stdout + invalid_threshold.stderr
    )

    relative_path = _run_preflight(
        tmp_path,
        _production_env(DISK_PATH="relative/path"),
    )
    assert relative_path.returncode != 0
    assert "DISK_PATH must be an absolute single-line host path" in (
        relative_path.stdout + relative_path.stderr
    )


def test_production_preflight_selects_only_known_capacity_profiles(
    tmp_path: Path,
) -> None:
    base_prod = " ".join(
        (
            (ROOT / "docker-compose.yml").as_posix(),
            (ROOT / "docker-compose.prod.yml").as_posix(),
        )
    )
    vps = _run_preflight(tmp_path, _production_env(), compose_files=base_prod)
    assert vps.returncode == 0, vps.stdout + vps.stderr
    assert "overridden for the vps profile" in vps.stderr

    hypervisor_file = (ROOT / "docker-compose.hypervisor.yml").as_posix()
    mixed = _run_preflight(
        tmp_path,
        _production_env(),
        compose_files=f"{hypervisor_file} {base_prod}",
    )
    assert mixed.returncode != 0
    assert "COMPOSE_FILES must be exactly" in mixed.stderr

    dev_override = (ROOT / "docker-compose.override.yml").as_posix()
    extra_override = _run_preflight(
        tmp_path,
        _production_env(),
        compose_files=f"{base_prod} {dev_override}",
    )
    assert extra_override.returncode != 0
    assert "extra, reversed, mixed, and unknown overrides are prohibited" in (
        extra_override.stderr
    )

    reversed_vps = _run_preflight(
        tmp_path,
        _production_env(),
        compose_files=" ".join(reversed(base_prod.split())),
    )
    assert reversed_vps.returncode != 0
    assert "COMPOSE_FILES must be exactly" in reversed_vps.stderr


def test_capacity_gate_checks_all_resolved_bind_and_docker_filesystems() -> None:
    preflight = PREFLIGHT.read_text(encoding="utf-8")
    capacity_check = CAPACITY_CHECK.read_text(encoding="utf-8")

    assert "docker info --format '{{.DockerRootDir}}'" in preflight
    assert (
        'capacity_paths=("$monitor_disk_path" "$uploads_host_dir" "$host_status_dir" '
        '"$ROOT_DIR/backups" "$docker_root_dir")' in preflight
    )
    assert 'capacity_paths+=("${compose_bind_sources[@]}")' in preflight
    assert "/data/legalapp/postgres" in preflight
    assert "/data/legalapp/litellm-postgres" in preflight
    assert "config --format json" in preflight
    assert "extract_compose_bind_sources" in capacity_check
    assert 'for requested_path in "$@"' in capacity_check
    assert "checked_devices" in capacity_check
    assert "DEPLOY_BUILD_HEADROOM_KIB" in capacity_check
    assert "runtime_free_percent=$((101 - disk_max_percent))" in capacity_check


def test_compose_bind_extractor_covers_future_absolute_sources() -> None:
    result = _extract_bind_sources(
        {
            "services": {
                "postgres": {
                    "volumes": [
                        {
                            "type": "bind",
                            "source": "/data/legalapp/postgres",
                            "target": "/var/lib/postgresql/data",
                        },
                        {
                            "type": "volume",
                            "source": "named-data",
                            "target": "/ignored",
                        },
                    ]
                },
                "future-worker": {
                    "volumes": [
                        {
                            "type": "bind",
                            "source": "/mnt/dedicated storage/future-data",
                            "target": "/srv/data",
                        }
                    ]
                },
            }
        }
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "/data/legalapp/postgres",
        "/mnt/dedicated storage/future-data",
    ]


def test_compose_bind_extractor_fails_closed_on_invalid_source() -> None:
    result = _extract_bind_sources(
        {
            "services": {
                "worker": {
                    "volumes": [
                        {"type": "bind", "source": "relative/data", "target": "/data"}
                    ]
                }
            }
        }
    )

    assert result.returncode != 0
    assert "absolute non-root single-line path" in result.stderr


def test_vps_preflight_requires_both_reviewed_database_binds(tmp_path: Path) -> None:
    base_prod = " ".join(
        (
            (ROOT / "docker-compose.yml").as_posix(),
            (ROOT / "docker-compose.prod.yml").as_posix(),
        )
    )
    missing_litellm_bind = json.dumps(
        {
            "services": {
                "postgres": {
                    "volumes": [
                        {
                            "type": "bind",
                            "source": "/data/legalapp/postgres",
                            "target": "/var/lib/postgresql/data",
                        }
                    ]
                }
            }
        }
    )
    result = _run_preflight(
        tmp_path,
        _production_env(),
        compose_files=base_prod,
        process_overrides={
            "FAKE_COMPOSE_CONFIG_JSON": missing_litellm_bind,
            "FAKE_BIND_SOURCES": "/data/legalapp/postgres\n",
        },
    )

    assert result.returncode != 0
    assert (
        "must retain reviewed database bind /data/legalapp/litellm-postgres"
        in result.stderr
    )


def test_documented_compose_limits_are_ceilings_not_reservations() -> None:
    services = yaml.safe_load(
        (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )["services"]
    memory_gib = 0.0
    cpu_limit = 0.0
    for service in services.values():
        resources = service.get("deploy", {}).get("resources", {})
        assert "reservations" not in resources
        limits = resources.get("limits", {})
        memory = limits.get("memory")
        if memory:
            match = re.fullmatch(r"([0-9.]+)([MG])", memory)
            assert match, memory
            value, unit = match.groups()
            memory_gib += float(value) / 1024 if unit == "M" else float(value)
        if "cpus" in limits:
            cpu_limit += float(limits["cpus"])

    assert memory_gib == 17.5
    assert cpu_limit == 9.0


def test_host_capacity_override_requires_reason_and_cannot_be_persisted(
    tmp_path: Path,
) -> None:
    missing_reason = _run_preflight(
        tmp_path,
        _production_env(),
        process_overrides={
            "HOST_CAPACITY_OVERRIDE": "true",
            "HOST_CAPACITY_OVERRIDE_REASON": "",
        },
    )
    assert missing_reason.returncode != 0
    assert "requires a specific HOST_CAPACITY_OVERRIDE_REASON" in (
        missing_reason.stdout + missing_reason.stderr
    )

    persisted = _run_preflight(
        tmp_path,
        _production_env(HOST_CAPACITY_OVERRIDE="true"),
    )
    assert persisted.returncode != 0
    assert "HOST_CAPACITY_OVERRIDE must never be persisted in .env" in (
        persisted.stdout + persisted.stderr
    )


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


def test_production_preflight_rejects_public_site_domain_mismatch(
    tmp_path: Path,
) -> None:
    result = _run_preflight(
        tmp_path,
        _production_env(VITE_PUBLIC_SITE_URL="https://different-host.invalid"),
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "VITE_PUBLIC_SITE_URL must exactly match https://DOMAIN" in output


def test_production_preflight_rejects_missing_legacy_platform_disable(
    tmp_path: Path,
) -> None:
    env_text = "".join(
        line
        for line in _production_env().splitlines(keepends=True)
        if not line.startswith("PLATFORM_LEGACY_BOOTSTRAP_ENABLED=")
    )
    result = _run_preflight(tmp_path, env_text)
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "PLATFORM_LEGACY_BOOTSTRAP_ENABLED" in output
    assert "explicitly false" in output


def test_production_preflight_rejects_enabled_legacy_platform_bridge(
    tmp_path: Path,
) -> None:
    result = _run_preflight(
        tmp_path, _production_env(PLATFORM_LEGACY_BOOTSTRAP_ENABLED="true")
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "PLATFORM_LEGACY_BOOTSTRAP_ENABLED must be explicitly false" in output


def test_production_check_rejects_missing_or_enabled_legacy_platform_bridge(
    tmp_path: Path,
) -> None:
    for value in (None, "true"):
        result = _run_production_policy_check(tmp_path, legacy_value=value)
        output = result.stdout + result.stderr
        assert result.returncode != 0
        assert "PLATFORM_LEGACY_BOOTSTRAP_ENABLED must be explicitly false" in output


def test_production_check_rejects_mcp_env_file_and_process_drift(
    tmp_path: Path,
) -> None:
    missing = _run_production_policy_check(
        tmp_path,
        legacy_value="false",
        mcp_value=None,
    )
    missing_output = missing.stdout + missing.stderr
    assert missing.returncode != 0
    assert "MCP_PRODUCT_ENABLED must remain false" in missing_output

    conflict = _run_production_policy_check(
        tmp_path,
        legacy_value="false",
        mcp_value="true",
        process_overrides={"MCP_PRODUCT_ENABLED": "false"},
    )
    conflict_output = conflict.stdout + conflict.stderr
    assert conflict.returncode != 0
    assert "inherited MCP_PRODUCT_ENABLED conflicts" in conflict_output


def test_production_check_asserts_disabled_public_mcp_surface() -> None:
    production_check = PRODUCTION_CHECK.read_text(encoding="utf-8")
    assert '"https://${DOMAIN}/api/mcp" 404' in production_check
    assert '"https://${DOMAIN}/api/mcp/manifest" 404' in production_check
    scheduled_health = (
        ROOT / ".github" / "workflows" / "production-health.yml"
    ).read_text(encoding="utf-8")
    assert "for disabled_mcp_path in /api/mcp /api/mcp/manifest" in scheduled_health
    assert '[[ "$disabled_mcp_status" == "404" ]]' in scheduled_health


def test_host_disk_monitor_is_persistent_read_only_and_alertable() -> None:
    hypervisor = yaml.safe_load((ROOT / "docker-compose.hypervisor.yml").read_text())
    production = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())
    for model in (hypervisor, production):
        backend = model["services"]["backend"]
        assert any(
            "/run/legalapp-host-status:ro" in volume
            for volume in backend.get("volumes", [])
        )
        assert backend["environment"]["HOST_DISK_STATUS_FILE"] == (
            "/run/legalapp-host-status/disk-status.json"
        )
        assert not any(
            "/var/run/docker.sock" in volume or ":/var/lib/docker" in volume
            for volume in backend.get("volumes", [])
        )

    deploy = (ROOT / "scripts" / "deploy_prod.sh").read_text(encoding="utf-8")
    assert "install_host_disk_timer.sh" in deploy
    assert "enable-linger" in deploy
    installer = (ROOT / "scripts" / "install_host_disk_timer.sh").read_text(
        encoding="utf-8"
    )
    assert "render_host_disk_units.sh" in installer
    unit_gate = (ROOT / "scripts" / "test_host_disk_systemd_units.sh").read_text(
        encoding="utf-8"
    )
    assert "systemd-analyze verify" in unit_gate
    assert "render_host_disk_units.sh" in unit_gate
    ci_workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "test_host_disk_systemd_units.sh" in ci_workflow
    rehearsal = (ROOT / "scripts" / "rehearse_fresh_host.sh").read_text(
        encoding="utf-8"
    )
    assert "update_host_disk_status.py" in rehearsal
    assert '"$APP_DIR/nginx/webroot"' in rehearsal
    workflow = (ROOT / ".github" / "workflows" / "production-health.yml").read_text(
        encoding="utf-8"
    )
    assert '"host_disks":"ok"' in workflow
    production_check = PRODUCTION_CHECK.read_text(encoding="utf-8")
    assert "https://${DOMAIN}/health/readiness" in production_check
    assert 'components.get("host_disks") == "ok"' in production_check


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
    assert (
        "backend and scheduler runtime SMTP configurations differ" in production_check
    )
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
    # Nullable tenant_id columns are legitimate for global rows. Their count
    # metric must remain named so clean restores can compare it exactly.
    nullable_tenant_metric = "COALESCE(tenant_id::text, ''<null>'')"
    assert data_guard.count(nullable_tenant_metric) == 2
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
    assert nullable_tenant_metric in manual_restore


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
    assert 'compose_files=("${production_compose_files[@]}"' in rehearsal
    assert 'COMPOSE_FILES="$preflight_compose_files_value"' in rehearsal
    assert 'COMPOSE_FILES="$compose_files_value"' not in rehearsal
    assert 'extra_headers={"X-Forwarded-Proto": "https"}' in rehearsal
    assert "plain=301,edge=200,https=200,frontend=200" in rehearsal
    assert 'plain_headers.get_all("Strict-Transport-Security", []) == []' in rehearsal
    assert '"https://rehearsal.invalid/health/readiness"' in rehearsal
