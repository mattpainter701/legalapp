from __future__ import annotations

import base64
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

import yaml
import pytest


ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "scripts" / "prod_env_preflight.sh"
CAPACITY_CHECK = ROOT / "scripts" / "check_host_capacity.sh"
PRODUCTION_CHECK = ROOT / "scripts" / "production_check.sh"
BASH_BIN = os.environ.get("BASH", "bash")
NEW_FERNET_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
OLD_FERNET_KEY = "KxzLuxmIM2dFDWQmKJL9LVUK5ouA0c3_-4VqCMrn-jY="


def _workspace_rsa_pair() -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return (
        base64.b64encode(private_pem).decode("ascii"),
        base64.b64encode(public_pem).decode("ascii"),
    )


def _origin_tls_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Create the pinned origin material used by shell preflight tests."""
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Origin CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "origin.getlawhand.internal")]
    )
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("origin.getlawhand.internal")]),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_file = tmp_path / "origin-ca.pem"
    cert_file = tmp_path / "origin-fullchain.pem"
    key_file = tmp_path / "origin-key.pem"
    config_file = tmp_path / "cloudflared.yml"
    ca_file.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_file.write_bytes(
        leaf_cert.public_bytes(serialization.Encoding.PEM) + ca_file.read_bytes()
    )
    key_file.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    key_file.chmod(0o600)
    (tmp_path / ".private-origin-managed").write_text(
        "managed by test fixture\n", encoding="utf-8"
    )
    ca_path = ca_file.as_posix()
    config_file.write_text(
        "ingress:\n"
        "  - hostname: getlawhand.com\n"
        "    service: https://127.0.0.1:443\n"
        "    originRequest:\n"
        "      originServerName: origin.getlawhand.internal\n"
        f"      caPool: {ca_path}\n"
        "      http2Origin: true\n"
        "  - hostname: www.getlawhand.com\n"
        "    service: https://127.0.0.1:443\n"
        "    originRequest:\n"
        "      originServerName: origin.getlawhand.internal\n"
        f"      caPool: {ca_path}\n"
        "      http2Origin: true\n"
        "  - hostname: mcp.getlawhand.com\n"
        "    service: https://127.0.0.1:443\n"
        "    originRequest:\n"
        "      originServerName: origin.getlawhand.internal\n"
        f"      caPool: {ca_path}\n"
        "      http2Origin: true\n"
        "  - hostname: research.getlawhand.com\n"
        "    service: https://127.0.0.1:443\n"
        "    originRequest:\n"
        "      originServerName: origin.getlawhand.internal\n"
        f"      caPool: {ca_path}\n"
        "      http2Origin: true\n"
        "  - service: http_status:404\n",
        encoding="utf-8",
    )
    return ca_file, cert_file, key_file, config_file


def _run_origin_tls_validator(
    ca_file: Path,
    cert_file: Path,
    key_file: Path,
    config_file: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "ORIGIN_TLS_SERVER_NAME": "origin.getlawhand.internal",
            "ORIGIN_TLS_CA_FILE": str(ca_file),
            "ORIGIN_TLS_CERT_FILE": str(cert_file),
            "ORIGIN_TLS_KEY_FILE": str(key_file),
            "CLOUDFLARED_CONFIG_FILE": str(config_file),
        }
    )
    env.update(extra_env or {})
    return subprocess.run(
        [BASH_BIN, str(ROOT / "scripts" / "validate_private_origin_tls.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics required")
def test_private_origin_validator_accepts_pinned_https_routes(tmp_path: Path) -> None:
    material = _origin_tls_fixture(tmp_path)

    result = _run_origin_tls_validator(*material)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics required")
def test_private_origin_validator_rejects_disabled_verification(
    tmp_path: Path,
) -> None:
    ca_file, cert_file, key_file, config_file = _origin_tls_fixture(tmp_path)
    config = config_file.read_text(encoding="utf-8")
    config_file.write_text(
        config.replace(
            "      http2Origin: true\n",
            "      noTLSVerify: true\n      http2Origin: true\n",
            1,
        ),
        encoding="utf-8",
    )

    result = _run_origin_tls_validator(ca_file, cert_file, key_file, config_file)

    assert result.returncode != 0
    assert "TLS verification is disabled" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership semantics required")
def test_private_origin_validator_enforces_production_ownership(
    tmp_path: Path,
) -> None:
    material = _origin_tls_fixture(tmp_path)

    result = _run_origin_tls_validator(
        *material,
        extra_env={
            "ORIGIN_TLS_REQUIRE_PRODUCTION_OWNERSHIP": "true",
            "CLOUDFLARED_BIN": "/bin/true",
        },
    )

    assert result.returncode != 0
    assert "must be root-owned" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell semantics required")
def test_private_origin_validator_requires_pinned_binary_in_production(
    tmp_path: Path,
) -> None:
    material = _origin_tls_fixture(tmp_path)

    result = _run_origin_tls_validator(
        *material,
        extra_env={"ORIGIN_TLS_REQUIRE_PRODUCTION_OWNERSHIP": "true"},
    )

    assert result.returncode == 2
    assert "CLOUDFLARED_BIN is required" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell semantics required")
def test_private_origin_validator_rejects_noncanonical_ingress_yaml(
    tmp_path: Path,
) -> None:
    ca_file, cert_file, key_file, config_file = _origin_tls_fixture(tmp_path)
    config_file.write_text(
        config_file.read_text(encoding="utf-8").replace(
            "    originRequest:\n",
            "    originRequest:  # an alias/comment cannot replace the reviewed contract\n",
            1,
        ),
        encoding="utf-8",
    )

    result = _run_origin_tls_validator(ca_file, cert_file, key_file, config_file)

    assert result.returncode != 0
    assert "canonical pinned HTTPS route contract" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell semantics required")
def test_private_origin_validator_bounds_validity_window(tmp_path: Path) -> None:
    material = _origin_tls_fixture(tmp_path)

    result = _run_origin_tls_validator(
        *material,
        extra_env={"TLS_MIN_VALID_DAYS": "9999"},
    )

    assert result.returncode == 2
    assert "between 1 and 3650" in result.stderr


def _production_env(**overrides: str) -> str:
    values = {
        "DOMAIN": "ops-test.invalid",
        "BACKEND_URL": "https://ops-test.invalid",
        "FRONTEND_URL": "https://ops-test.invalid",
        "VITE_PUBLIC_SITE_URL": "https://ops-test.invalid",
        "VITE_CONTACT_URL": "mailto:support@getlawhand.com",
        "DEV_MODE": "false",
        "PUBLIC_SIGNUP_ENABLED": "false",
        "VITE_PUBLIC_SIGNUP_ENABLED": "false",
        "SECRET_KEY": "ops-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz",
        "MCP_PRODUCT_ENABLED": "false",
        "PLATFORM_LEGACY_BOOTSTRAP_ENABLED": "false",
        "WORKSPACE_MCP_ENABLED": "false",
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
        "DEEPSEEK_API_KEY": "deepseek-provider-key-0123456789",
        "OPENCODE_ZEN_API_KEY": "opencode-zen-provider-key-0123456789",
        "TOKEN_ENCRYPTION_KEY": OLD_FERNET_KEY,
        "TOKEN_ENCRYPTION_KEYS": f"{NEW_FERNET_KEY},{OLD_FERNET_KEY}",
        "MCP_SERVER_URL": "http://courtlistener-mcp:8000",
        "MCP_UPSTREAM_API_KEY": "mcp-upstream-key-0123456789-abcdef",
        "MCP_OPERATOR_ASSERTION_SECRET": "mcp-operator-signer-0123456789-abcdef",
        "MCP_OPERATOR_ASSERTION_SECRET": "mcp-operator-signer-0123456789-abcdef",
        "UPLOADS_HOST_DIR": "/srv/legalapp/uploads",
        "HOST_STATUS_HOST_DIR": "/srv/legalapp/host-status",
        "HOST_DISK_STATUS_FILE": "/run/legalapp-host-status/disk-status.json",
        "HEALTH_HOST_DISK_MAX_AGE_SECONDS": "180",
        "BACKUP_STATUS_FILE": "/run/legalapp-host-status/backup-status.json",
        "HEALTH_BACKUP_MAX_AGE_SECONDS": "7200",
        "DISK_PATH": "/",
        "DISK_MAX_PERCENT": "85",
        "OFFSITE_BACKUP_REQUIRED": "true",
        "OFFSITE_RESTORE_PUBLIC_KEY_FILE": "__TEST_OFFSITE_PUBLIC_KEY__",
        "EMAIL_ENABLED": "false",
        "EMAIL_HOST": "",
        "EMAIL_PORT": "587",
        "EMAIL_USER": "",
        "EMAIL_PASS": "",
        "EMAIL_FROM": "support@getlawhand.com",
        "ORIGIN_TLS_SERVER_NAME": "origin.getlawhand.internal",
        "ORIGIN_TLS_CA_FILE": "__TEST_ORIGIN_CA__",
        "CLOUDFLARED_CONFIG_FILE": "__TEST_CLOUDFLARED_CONFIG__",
        "CLOUDFLARED_BIN": "/bin/true",
        "ZOOM_REQUIRED_TENANT_ID": "00000000-0000-4000-8000-000000000111",
        "QBO_CLIENT_ID": "qbo-client-id-0123456789",
        "QBO_CLIENT_SECRET": "qbo-client-secret-0123456789",
        "QBO_REDIRECT_URI": "https://ops-test.invalid/api/integrations/qbo/callback",
        "QBO_ENVIRONMENT": "production",
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
    ca_file, cert_file, key_file, config_file = _origin_tls_fixture(tmp_path)
    env_text = env_text.replace("__TEST_ORIGIN_CA__", str(ca_file))
    env_text = env_text.replace("__TEST_CLOUDFLARED_CONFIG__", str(config_file))
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
        "#!/bin/sh\n"
        'case "${2:-}" in\n'
        f'  *"base64,json,re,sys"*) exec {shlex.quote(Path(sys.executable).as_posix())} "$@" ;;\n'
        "esac\n"
        "printf '%s' \"$FAKE_BIND_SOURCES\"\n",
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
    env["ORIGIN_TLS_CERT_FILE"] = str(cert_file)
    env["ORIGIN_TLS_KEY_FILE"] = str(key_file)
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


def test_production_preflight_does_not_gate_zoom_on_commercial_plan(
    tmp_path: Path,
) -> None:
    result = _run_preflight(
        tmp_path,
        _production_env(ZOOM_REQUIRED_TENANT_PLAN="full-platform"),
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "Production preflight passed" in output


def test_production_preflight_rejects_nonproduction_qbo_oauth(
    tmp_path: Path,
) -> None:
    playground = _run_preflight(
        tmp_path,
        _production_env(
            QBO_REDIRECT_URI="https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl"
        ),
    )
    playground_output = playground.stdout + playground.stderr
    assert playground.returncode != 0
    assert "QBO_REDIRECT_URI must exactly match" in playground_output

    sandbox = _run_preflight(
        tmp_path,
        _production_env(QBO_ENVIRONMENT="sandbox"),
    )
    sandbox_output = sandbox.stdout + sandbox.stderr
    assert sandbox.returncode != 0
    assert "QBO_ENVIRONMENT must be production" in sandbox_output


def test_production_preflight_accepts_native_workspace_mcp_oauth(
    tmp_path: Path,
) -> None:
    private_key, public_key = _workspace_rsa_pair()
    workspace_settings = {
        "WORKSPACE_MCP_ENABLED": "true",
        "WORKSPACE_MCP_RESOURCE": "https://ops-test.invalid/api/mcp/workspace",
        "WORKSPACE_MCP_AUDIENCE": "lawhand-workspace-mcp",
        "WORKSPACE_MCP_ISSUER": "https://ops-test.invalid",
        "WORKSPACE_MCP_TOKEN_SIGNING_KEY": "",
        "WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64": private_key,
        "WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64": public_key,
        "WORKSPACE_MCP_SIGNING_KEY_ID": "workspace-test-2026-08",
        "WORKSPACE_MCP_PREVIOUS_PUBLIC_KEYS_JSON": "[]",
        "WORKSPACE_MCP_ACCESS_TOKEN_MAX_MINUTES": "15",
        "WORKSPACE_MCP_AUTH_CODE_TTL_SECONDS": "300",
        "WORKSPACE_MCP_REFRESH_TOKEN_DAYS": "30",
        "WORKSPACE_MCP_GRANT_DAYS": "90",
        "WORKSPACE_MCP_CLIENT_REGISTRATION_DAYS": "30",
        "WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED": "true",
    }

    result = _run_preflight(tmp_path, _production_env(**workspace_settings))
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert private_key[:48] not in output
    assert public_key[:48] not in output


def test_production_preflight_accepts_enabled_research_mcp_oauth(
    tmp_path: Path,
) -> None:
    private_key, public_key = _workspace_rsa_pair()
    research_settings = {
        "MCP_PRODUCT_ENABLED": "true",
        "RESEARCH_MCP_PUBLIC_URL": "https://research.ops-test.invalid/api/mcp",
        "RESEARCH_MCP_OAUTH_ENABLED": "true",
        "RESEARCH_MCP_AUDIENCE": "lawhand-research-mcp",
        "RESEARCH_MCP_ISSUER": "https://research.ops-test.invalid",
        "RESEARCH_MCP_ACCESS_TOKEN_MAX_MINUTES": "15",
        "RESEARCH_MCP_AUTH_CODE_TTL_SECONDS": "300",
        "RESEARCH_MCP_REFRESH_TOKEN_DAYS": "30",
        "RESEARCH_MCP_GRANT_DAYS": "90",
        "RESEARCH_MCP_CLIENT_REGISTRATION_DAYS": "30",
        "RESEARCH_MCP_DYNAMIC_REGISTRATION_ENABLED": "true",
        "WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64": private_key,
        "WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64": public_key,
        "WORKSPACE_MCP_SIGNING_KEY_ID": "research-test-2026-08",
        "WORKSPACE_MCP_PREVIOUS_PUBLIC_KEYS_JSON": "[]",
    }

    result = _run_preflight(tmp_path, _production_env(**research_settings))
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert private_key[:48] not in output
    assert public_key[:48] not in output


def test_production_preflight_rejects_legacy_workspace_signing_secret(
    tmp_path: Path,
) -> None:
    legacy_secret = "legacy-workspace-signing-secret-do-not-print"
    result = _run_preflight(
        tmp_path,
        _production_env(WORKSPACE_MCP_TOKEN_SIGNING_KEY=legacy_secret),
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "WORKSPACE_MCP_TOKEN_SIGNING_KEY must remain empty" in output
    assert legacy_secret not in output


def test_workspace_mcp_production_wiring_is_complete_and_fail_closed() -> None:
    keys = {
        "WORKSPACE_MCP_ENABLED",
        "WORKSPACE_MCP_RESOURCE",
        "WORKSPACE_MCP_CANONICAL_RESOURCE",
        "WORKSPACE_MCP_RESOURCE_ALIASES",
        "RESEARCH_MCP_PUBLIC_URL",
        "RESEARCH_MCP_OAUTH_ENABLED",
        "RESEARCH_MCP_AUDIENCE",
        "RESEARCH_MCP_ISSUER",
        "RESEARCH_MCP_ACCESS_TOKEN_MAX_MINUTES",
        "RESEARCH_MCP_AUTH_CODE_TTL_SECONDS",
        "RESEARCH_MCP_REFRESH_TOKEN_DAYS",
        "RESEARCH_MCP_GRANT_DAYS",
        "RESEARCH_MCP_CLIENT_REGISTRATION_DAYS",
        "RESEARCH_MCP_DYNAMIC_REGISTRATION_ENABLED",
        "WORKSPACE_MCP_AUDIENCE",
        "WORKSPACE_MCP_ISSUER",
        "WORKSPACE_MCP_TOKEN_SIGNING_KEY",
        "WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64",
        "WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64",
        "WORKSPACE_MCP_SIGNING_KEY_ID",
        "WORKSPACE_MCP_PREVIOUS_PUBLIC_KEYS_JSON",
        "WORKSPACE_MCP_ACCESS_TOKEN_MAX_MINUTES",
        "WORKSPACE_MCP_AUTH_CODE_TTL_SECONDS",
        "WORKSPACE_MCP_REFRESH_TOKEN_DAYS",
        "WORKSPACE_MCP_GRANT_DAYS",
        "WORKSPACE_MCP_CLIENT_REGISTRATION_DAYS",
        "WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED",
    }
    for compose_name in ("docker-compose.hypervisor.yml", "docker-compose.prod.yml"):
        services = yaml.safe_load((ROOT / compose_name).read_text(encoding="utf-8"))[
            "services"
        ]
        for service_name in ("backend", "scheduler"):
            environment = services[service_name]["environment"]
            assert keys <= environment.keys()
            assert environment["WORKSPACE_MCP_ENABLED"] == (
                "${WORKSPACE_MCP_ENABLED:-false}"
            )
            assert environment["WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED"] == (
                "${WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED:-false}"
            )
            assert environment["RESEARCH_MCP_OAUTH_ENABLED"] == (
                "${RESEARCH_MCP_OAUTH_ENABLED:-true}"
            )
            assert environment["RESEARCH_MCP_DYNAMIC_REGISTRATION_ENABLED"] == (
                "${RESEARCH_MCP_DYNAMIC_REGISTRATION_ENABLED:-true}"
            )

    production_check = PRODUCTION_CHECK.read_text(encoding="utf-8")
    assert "/.well-known/oauth-protected-resource/api/mcp/workspace" in production_check
    assert "/.well-known/oauth-authorization-server" in production_check
    assert "/api/workspace-mcp/oauth/jwks" in production_check
    assert "require_workspace_bearer_challenge" in production_check


def test_dedicated_mcp_hosts_are_isolated_and_streamed() -> None:
    nginx = (ROOT / "nginx" / "nginx.conf").read_text(encoding="utf-8")
    nginx_gate = (ROOT / "scripts" / "test_nginx_webhook_ingress.sh").read_text(
        encoding="utf-8"
    )
    transports = (ROOT / "nginx" / "snippets" / "mcp_transports.conf").read_text(
        encoding="utf-8"
    )
    proxy = (ROOT / "nginx" / "snippets" / "mcp_transport_proxy.conf").read_text(
        encoding="utf-8"
    )

    assert '"mcp.getlawhand.com:/" 0;' in nginx
    assert '"mcp.getlawhand.com:/api/mcp/workspace" 0;' in nginx
    assert (
        '"mcp.getlawhand.com:/.well-known/oauth-protected-resource'
        '/api/mcp/workspace" 0;'
    ) in nginx
    assert '"research.getlawhand.com:/" 0;' in nginx
    assert '"research.getlawhand.com:/api/mcp" 0;' in nginx
    assert '"research.getlawhand.com:/api/mcp/manifest" 0;' in nginx
    assert '"research.getlawhand.com:/api/mcp/tools/call" 0;' in nginx
    assert (
        '"research.getlawhand.com:/.well-known/oauth-protected-resource/api/mcp" 0;'
        in nginx
    )
    assert (
        '"research.getlawhand.com:/.well-known/oauth-authorization-server" 0;' in nginx
    )
    for oauth_path in ("authorize", "token", "revoke", "register", "jwks"):
        assert (
            f'"research.getlawhand.com:/api/research-mcp/oauth/{oauth_path}" 0;'
            in nginx
        )
    assert "~^research\\.getlawhand\\.com:/api/research-mcp/oauth/ 0;" not in nginx
    assert "~^mcp\\.getlawhand\\.com: 1;" in nginx
    assert "~^research\\.getlawhand\\.com: 1;" in nginx
    assert "default 0;" in nginx
    assert 'map "$host:$uri" $dedicated_mcp_root_rewrite' in nginx
    assert '"mcp.getlawhand.com:/" "/api/mcp/workspace";' in nginx
    assert '"research.getlawhand.com:/" "/api/mcp";' in nginx
    assert nginx.count("if ($dedicated_mcp_root_rewrite)") == 2
    assert nginx.count("rewrite ^/$ $dedicated_mcp_root_rewrite last;") == 2
    assert "map_hash_bucket_size 128;" in nginx
    assert nginx.count("if ($dedicated_mcp_surface_denied)") == 2
    assert nginx.count("include /etc/nginx/snippets/mcp_transports.conf;") == 2
    assert "courtlistener-mcp" not in nginx

    assert "location = /api/mcp {" in transports
    assert "location = /api/mcp/workspace {" in transports
    assert "proxy_buffering off;" in proxy
    assert "proxy_request_buffering off;" in proxy
    assert "proxy_cache off;" in proxy
    assert 'proxy_set_header Connection        "";' in proxy
    assert '"mcp.getlawhand.com")' in nginx_gate
    assert '"research.getlawhand.com")' in nginx_gate
    assert "platform MCP cross-product isolation" in nginx_gate
    assert "research MCP cross-product isolation" in nginx_gate
    for selector in (
        "location = /api/mcp {",
        "location = /api/mcp/workspace {",
        "location = /api/mcp/manifest {",
        "location = /api/mcp/tools/call {",
    ):
        assert selector in transports

    assert transports.count("client_max_body_size 256k;") == 4
    assert transports.count("client_body_timeout 15s;") == 4
    assert transports.count("limit_conn mcp_connections 10;") == 4
    assert transports.count("limit_req zone=mcp_research burst=20 nodelay;") == 3
    assert transports.count("limit_req zone=mcp_workspace burst=20 nodelay;") == 1
    assert transports.count("if ($request_method !~ ^(GET|POST|DELETE)$)") == 2
    assert "if ($request_method != GET)" in transports
    assert "if ($request_method != POST)" in transports

    assert "zone=mcp_workspace:10m rate=60r/m;" in nginx
    assert "zone=mcp_research:10m rate=60r/m;" in nginx
    assert "zone=qbo:10m rate=120r/m;" in nginx
    assert nginx.count("location ^~ /api/integrations/qbo/ {") == 2
    assert nginx.count("limit_req zone=qbo burst=30 nodelay;") == 2
    assert "limit_conn_zone $binary_remote_addr zone=mcp_connections:10m;" in nginx
    assert "limit_conn_status 429;" in nginx
    assert "map $uri $mcp_allow_header" in nginx
    assert "map $uri $hidden_path_denied" in nginx
    assert "~(?:^|/)\\. 1;" in nginx
    assert nginx.count("if ($hidden_path_denied)") == 2
    assert nginx.count("add_header Allow $mcp_allow_header always;") == 2

    hypervisor = (ROOT / "docker-compose.hypervisor.yml").read_text(encoding="utf-8")
    assert (
        "WORKSPACE_MCP_CANONICAL_RESOURCE: "
        "${WORKSPACE_MCP_CANONICAL_RESOURCE:-"
        "https://mcp.getlawhand.com/api/mcp/workspace}"
    ) in hypervisor
    assert (
        "RESEARCH_MCP_PUBLIC_URL: "
        "${RESEARCH_MCP_PUBLIC_URL:-https://research.getlawhand.com/api/mcp}"
    ) in hypervisor


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


def test_cube_m_capacity_profile_accepts_the_ionos_host_and_rejects_undersizing() -> (
    None
):
    accepted = _validate_capacity(
        profile="cube-m",
        cpus=4,
        memory_gib=15,
        disk_total_gib=232,
        disk_free_gib=220,
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "Host capacity passed (cube-m)" in accepted.stdout

    rejected = _validate_capacity(
        profile="cube-m",
        cpus=2,
        memory_gib=12,
        disk_total_gib=180,
        disk_free_gib=25,
    )
    output = rejected.stdout + rejected.stderr
    assert rejected.returncode != 0
    assert "2 online CPU(s); at least 4 are required" in output
    assert "12.0 GiB RAM; at least 14.0 GiB is required" in output
    assert "180.0 GiB total" in output
    assert "25.0 GiB free" in output


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

    cube_m = " ".join(
        (
            (ROOT / "docker-compose.hypervisor.yml").as_posix(),
            (ROOT / "docker-compose.cube-m.yml").as_posix(),
        )
    )
    cube = _run_preflight(tmp_path, _production_env(), compose_files=cube_m)
    assert cube.returncode == 0, cube.stdout + cube.stderr
    assert "overridden for the cube-m profile" in cube.stderr

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
    assert (
        "RESEARCH_MCP_PUBLIC_URL is required when the Research MCP product is enabled"
        in output
    )
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
    assert "MCP_PRODUCT_ENABLED must be explicitly true or false" in missing_output

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
    assert 'resource_origin="https://mcp.${DOMAIN}"' in production_check
    assert 'research_origin="https://research.${DOMAIN}"' in production_check
    assert "platform MCP hostname isolation" in production_check
    assert "research MCP hostname isolation" in production_check
    scheduled_health = (
        ROOT / ".github" / "workflows" / "production-health.yml"
    ).read_text(encoding="utf-8")
    scheduled_config = yaml.safe_load(scheduled_health)
    scheduled_env = scheduled_config["jobs"]["public-health"]["env"]
    assert "for disabled_mcp_path in /api/mcp /api/mcp/manifest" in scheduled_health
    assert '[[ "$disabled_mcp_status" == "404" ]]' in scheduled_health
    assert scheduled_env["WORKSPACE_MCP_ORIGIN"] == "https://mcp.getlawhand.com"
    assert scheduled_env["RESEARCH_MCP_ORIGIN"] == "https://research.getlawhand.com"
    assert ".authorization_servers == [$issuer]" in scheduled_health
    assert "for isolated_url in" in scheduled_health


def test_production_check_exercises_customer_llm_routes() -> None:
    production_check = PRODUCTION_CHECK.read_text(encoding="utf-8")

    assert "LiteLLM customer-route completion probe failed" in production_check
    assert "python -m app.services.llm_availability" in production_check
    assert '"${compose[@]}" exec -T backend' in production_check
    assert "timeout --kill-after=10s 140s" in production_check
    assert (
        'for model in ("clarity-standard", "clarity-premium"):' not in production_check
    )


def test_production_check_fails_closed_on_document_automation_integrity_gaps() -> None:
    production_check = PRODUCTION_CHECK.read_text(encoding="utf-8")

    assert "document_template_previews" in production_check
    assert "reconciliation_required_at IS NOT NULL" in production_check
    assert "reconciliation_resolved_at IS NULL" in production_check
    assert "unresolved staged-file reconciliation" in production_check
    assert "document_templates WHERE is_active" in production_check
    assert "source_storage_path" in production_check
    assert "source_sha256" in production_check
    assert "source_file_size" in production_check
    assert "active binary template(s) without complete source integrity metadata" in (
        production_check
    )


def test_skynet_deploy_recreates_litellm_and_bounds_litellm_diagnostics() -> None:
    deploy = (ROOT / "scripts" / "deploy_skynet_runner.sh").read_text(encoding="utf-8")

    assert "up -d --build --force-recreate" in deploy
    assert "litellm backend scheduler frontend office-addin nginx" in deploy
    assert '"${compose[@]}" ps -q litellm' in deploy
    assert '"$litellm_health" == healthy' in deploy
    assert "logs --tail=120 litellm-migrator litellm-schema-migrator litellm" in deploy


def test_skynet_deploy_gates_active_customer_routes_before_public_readiness() -> None:
    deploy = (ROOT / "scripts" / "deploy_skynet_runner.sh").read_text(encoding="utf-8")
    availability = (
        ROOT / "backend" / "app" / "services" / "llm_availability.py"
    ).read_text(encoding="utf-8")

    post_guard = deploy.index("prod_data_guard.sh post")
    gate_start = deploy.index("if ! timeout --kill-after=10s 140s", post_guard)
    probe = deploy.index("python -m app.services.llm_availability", gate_start)
    gate_end = deploy.index("\nfi", probe)
    public_readiness = deploy.index(
        'echo "==> Verifying public readiness and exact release metadata"'
    )

    assert post_guard < gate_start < probe < gate_end < public_readiness
    assert '"${compose[@]}" exec -T backend' in deploy[gate_start:probe]
    assert "exit 5" in deploy[probe:gate_end]
    assert "active customer LiteLLM availability gate failed" in deploy[probe:gate_end]
    assert 'CUSTOMER_LLM_TIERS = ("standard", "premium")' in availability
    assert 'return 0 if result["ok"] else 1' in availability


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


def test_offsite_backup_freshness_is_a_public_production_gate() -> None:
    hypervisor = yaml.safe_load((ROOT / "docker-compose.hypervisor.yml").read_text())
    production = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())
    for model in (hypervisor, production):
        backend = model["services"]["backend"]
        assert backend["environment"]["BACKUP_STATUS_FILE"] == (
            "${BACKUP_STATUS_FILE:-}"
        )
        assert backend["environment"]["HEALTH_BACKUP_MAX_AGE_SECONDS"] == (
            "${HEALTH_BACKUP_MAX_AGE_SECONDS:-7200}"
        )

    timer = (ROOT / "ops" / "systemd" / "legalapp-backup.timer").read_text(
        encoding="utf-8"
    )
    assert "OnCalendar=hourly" in timer
    assert "RandomizedDelaySec=10m" in timer

    scheduled_health = (
        ROOT / ".github" / "workflows" / "production-health.yml"
    ).read_text(encoding="utf-8")
    assert '"backups":"ok"' in scheduled_health
    production_check = PRODUCTION_CHECK.read_text(encoding="utf-8")
    assert 'components.get("backups") == "ok"' in production_check


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


@pytest.mark.parametrize(
    "provider_key", ["", "change-me-provider-key", "provider-key@example.com"]
)
def test_production_preflight_rejects_missing_or_placeholder_opencode_go_key(
    tmp_path: Path, provider_key: str
) -> None:
    result = _run_preflight(tmp_path, _production_env(DEEPSEEK_API_KEY=provider_key))
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert (
        "OPENCODE_GO_API_KEY (or legacy DEEPSEEK_API_KEY) must be configured" in output
    )
    if provider_key:
        assert provider_key not in output


def test_production_preflight_accepts_canonical_opencode_go_key(
    tmp_path: Path,
) -> None:
    result = _run_preflight(
        tmp_path,
        _production_env(
            DEEPSEEK_API_KEY="",
            OPENCODE_GO_API_KEY="opencode-go-provider-key-0123456789",
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_production_preflight_rejects_missing_opencode_zen_key(
    tmp_path: Path,
) -> None:
    result = _run_preflight(
        tmp_path,
        _production_env(
            DEEPSEEK_API_KEY="",
            OPENCODE_GO_API_KEY="opencode-go-provider-key-0123456789",
            OPENCODE_ZEN_API_KEY="",
            OPENCODE_API_KEY="",
            OPENCODE_KEY="",
        ),
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "OPENCODE_ZEN_API_KEY (or a supported legacy OpenCode key)" in output


def test_production_preflight_accepts_legacy_shared_opencode_key(
    tmp_path: Path,
) -> None:
    result = _run_preflight(
        tmp_path,
        _production_env(
            OPENCODE_GO_API_KEY="",
            OPENCODE_ZEN_API_KEY="",
            OPENCODE_API_KEY="",
            OPENCODE_KEY="",
            DEEPSEEK_API_KEY="legacy-shared-opencode-key-0123456789",
        ),
    )

    assert result.returncode == 0, result.stdout + result.stderr


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


def test_production_preflight_accepts_intentionally_disabled_email(
    tmp_path: Path,
) -> None:
    result = _run_preflight(
        tmp_path,
        _production_env(EMAIL_ENABLED="false", EMAIL_USER="", EMAIL_PASS=""),
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "EMAIL_ENABLED=false by design" in output
    assert "GitHub production-health issues" in output


def test_production_preflight_rejects_incomplete_enabled_email(
    tmp_path: Path,
) -> None:
    result = _run_preflight(
        tmp_path,
        _production_env(
            EMAIL_ENABLED="true", EMAIL_HOST="", EMAIL_USER="", EMAIL_PASS=""
        ),
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "EMAIL_HOST must be configured" in output
    assert "EMAIL_USER must be configured" in output
    assert "EMAIL_PASS must be configured" in output


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
        expected_start_period = "240s" if compose_name == "docker-compose.hypervisor.yml" else "90s"
        assert proxy["healthcheck"]["start_period"] == expected_start_period
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


def test_production_feature_flags_are_explicitly_mapped_and_rollback_images_remain() -> (
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

    production_models = [
        yaml.safe_load((ROOT / compose_name).read_text(encoding="utf-8"))["services"]
        for compose_name in ("docker-compose.hypervisor.yml", "docker-compose.prod.yml")
    ]
    for prod_services in production_models:
        for service in ("backend", "scheduler"):
            assert prod_services[service]["environment"]["PUBLIC_SIGNUP_ENABLED"] == (
                "${PUBLIC_SIGNUP_ENABLED:-false}"
            )
            # Production intentionally ignores stale host SMB_ENABLED=false values.
            assert prod_services[service]["environment"]["SMB_ENABLED"] == "true"
    prod_services = production_models[-1]
    assert (
        prod_services["frontend"]["build"]["args"]["VITE_PUBLIC_SIGNUP_ENABLED"]
        == "${VITE_PUBLIC_SIGNUP_ENABLED:-false}"
    )
    assert "SMB_ENABLED=true" in (ROOT / ".env.prod.example").read_text(
        encoding="utf-8"
    )

    deploy = (ROOT / "scripts" / "deploy_prod.sh").read_text(encoding="utf-8")
    assert "docker image prune" not in deploy
    assert "rollback-before-$release_tag" in deploy
    assert "release-$release_tag" in deploy
    assert "rollback_manifest" in deploy
    assert 'APP_COMMIT" == "$git_commit' in deploy
    assert (
        "for service in backend scheduler migrator frontend office-addin nginx litellm"
        in deploy
    )


def test_cube_m_overlay_bounds_runtime_without_weakening_private_ingress() -> None:
    cube = yaml.safe_load((ROOT / "docker-compose.cube-m.yml").read_text())
    services = cube["services"]
    assert cube["name"] == "legalapp"
    assert services["backend"]["command"].endswith("--workers 2")
    assert "BACKEND_WORKERS" not in services["backend"]["command"]
    assert "ports" not in services["nginx"]

    steady_services = (
        "postgres",
        "redis",
        "litellm-postgres",
        "litellm",
        "backend",
        "scheduler",
        "frontend",
        "office-addin",
        "nginx",
    )

    def memory_mib(value: str) -> int:
        if value.endswith("G"):
            return int(value[:-1]) * 1024
        if value.endswith("M"):
            return int(value[:-1])
        raise AssertionError(f"unsupported memory limit {value}")

    total_mib = sum(
        memory_mib(services[name]["deploy"]["resources"]["limits"]["memory"])
        for name in steady_services
    )
    assert total_mib <= 10 * 1024

    # LiteLLM's Prisma deploy and schema-diff steps each exceeded a 768 MiB
    # cgroup during production-data reconciliation even though the 16 GiB host
    # had ample free RAM. They run sequentially, while the application migrator
    # may overlap either step.
    litellm_oneshot_services = ("litellm-migrator", "litellm-schema-migrator")
    for name in litellm_oneshot_services:
        assert (
            memory_mib(services[name]["deploy"]["resources"]["limits"]["memory"])
            >= 1280
        )
    startup_total_mib = (
        total_mib
        + max(
            memory_mib(services[name]["deploy"]["resources"]["limits"]["memory"])
            for name in litellm_oneshot_services
        )
        + memory_mib(services["migrator"]["deploy"]["resources"]["limits"]["memory"])
    )
    assert startup_total_mib <= 12 * 1024


def test_ionos_stage_gate_is_private_exact_and_fail_closed() -> None:
    stage = (ROOT / "scripts" / "ionos_stage_check.sh").read_text(encoding="utf-8")
    assert "https://${origin_server_name}" in stage
    assert '--resolve "${origin_server_name}:443:127.0.0.1"' in stage
    assert '-H "Host: $host"' in stage
    assert "MCP_SERVER_URL" in stage
    assert "from app.config import get_settings" in stage
    assert "settings = get_settings()" in stage
    assert "from app.config import settings" not in stage
    assert 'ipaddress.ip_network("100.64.0.0/10")' in stage
    assert "X-Clarity-Internal-Key" in stage
    assert "MCP_SERVER_URL.rstrip('/')}/api/mcp\"" in stage
    assert 'research_enabled="$(get_env MCP_PRODUCT_ENABLED)"' in stage
    assert '[[ "$research_status" == 401 ]]' in stage
    assert "enabled Research MCP did not require authentication" in stage
    assert "enabled Research MCP did not advertise Bearer authentication" in stage
    assert '[[ "$research_status" == 404 ]]' in stage
    assert "disabled Research MCP did not fail closed" in stage
    assert "MCP_PRODUCT_ENABLED must be true or false" in stage
    assert "IONOS_PUBLIC_CUTOVER=not-yet-approved" in stage


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
    assert "billing_tier <> 'demo'" in production_check
    assert "t.billing_tier <> 'demo'" in production_check
    assert "ZOOM_REQUIRED_TENANT_ID" in production_check


def test_demo_tenants_are_excluded_from_scheduler_health_gates() -> None:
    deploy = (ROOT / "scripts" / "deploy_prod.sh").read_text(encoding="utf-8")
    production_check = (ROOT / "scripts" / "production_check.sh").read_text(
        encoding="utf-8"
    )
    main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")

    # Demo tenants are intentionally excluded by tenant_scoped_job. Every
    # release/readiness population query must use the same customer boundary.
    assert "billing_tier <> 'demo'" in deploy
    assert "billing_tier <> 'demo'" in production_check
    assert "billing_tier <> 'demo' ORDER BY id" in main
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


def test_zoom_production_gate_is_independent_of_commercial_plan() -> None:
    preflight = (ROOT / "scripts" / "prod_env_preflight.sh").read_text(encoding="utf-8")
    production_check = (ROOT / "scripts" / "production_check.sh").read_text(
        encoding="utf-8"
    )
    env_example = (ROOT / ".env.prod.example").read_text(encoding="utf-8")
    acceptance = (ROOT / "scripts" / "production_acceptance.sh").read_text(
        encoding="utf-8"
    )
    deploy = (ROOT / "scripts" / "deploy_prod.sh").read_text(encoding="utf-8")

    for source in (preflight, production_check, env_example):
        assert "ZOOM_REQUIRED_TENANT_PLAN" not in source
    assert "custom_config->>'plan'" not in production_check
    assert "required Zoom tenant is inactive or missing" in production_check
    assert "a.encrypted_webhook_secret_token IS NOT NULL" in production_check
    assert "c.encrypted_refresh_token IS NOT NULL" in production_check
    assert "c.service_account_email = a.zoom_account_id" in production_check
    assert "c.health = 'healthy'" in production_check
    assert "phone:read:list_call_logs:admin" in production_check
    assert "phone:read:call_log:admin" in production_check
    assert '--tenant-id "$ZOOM_REQUIRED_TENANT_ID"' in production_check
    assert 'ZOOM_REQUIRED="${ZOOM_REQUIRED:-false}"' in production_check
    assert "ZOOM_REQUIRED=false" in acceptance
    assert 'ZOOM_REQUIRED="${ZOOM_REQUIRED:-false}"' in deploy
    assert 'zoom_required="$ZOOM_REQUIRED"' in deploy


def test_production_preflight_allows_zoom_selector_to_be_omitted(
    tmp_path: Path,
) -> None:
    env_text = "\n".join(
        line
        for line in _production_env().splitlines()
        if not line.startswith("ZOOM_REQUIRED_TENANT_ID=")
    )
    result = _run_preflight(tmp_path, env_text + "\n")
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "optional Zoom provider gate cannot be requested" in output


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
    assert data_guard.count(nullable_tenant_metric) == 4
    # Expired, never-registered SMB pairing reservations are intentionally
    # cleaned up by the scheduler. Registered active/paused/revoked rows must
    # remain protected by the production count guard.
    assert data_guard.count("table_name = 'smb_agents'") == 4
    assert data_guard.count("api_key_hash IS DISTINCT FROM ''pending''") == 4
    tenant_registered_agent_filter = (
        "WHERE api_key_hash IS DISTINCT FROM ''pending'' GROUP BY tenant_id;"
    )
    assert data_guard.count(tenant_registered_agent_filter) == 2
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
    assert "wait_for_final_postgres" in restore
    assert '[ "$(cat /proc/1/comm)" = postgres ]' in restore
    assert restore.count('wait_for_final_postgres "$') == 2
    assert "pg_isready" in restore
    assert (
        "pg_isready -U postgres -d legalapp_restore >/dev/null 2>&1 && break"
        not in restore
    )
    assert "legalapp-restored.counts.tsv" in manual_restore
    assert "litellm-restored.counts.tsv" in manual_restore
    assert "upload_backup_artifact.py" in manual_restore
    assert "Escrowed TLS certificate and private key do not match" in manual_restore
    assert "MANUAL_RESTORE_PROOF=" in manual_restore
    assert "OFFSITE_RESTORE_SIGNING_KEY_FILE" in manual_restore
    assert "openssl dgst -sha256 -sign" in manual_restore
    assert nullable_tenant_metric in manual_restore


def test_skynet_installers_separate_runner_from_runtime_owner() -> None:
    dev1 = (ROOT / "scripts" / "install_dev1_deploy_entrypoint.sh").read_text(
        encoding="utf-8"
    )
    dr = (ROOT / "scripts" / "install_skynet_dr_services.sh").read_text(
        encoding="utf-8"
    )
    deploy = (ROOT / "scripts" / "deploy_dev1.sh").read_text(encoding="utf-8")
    assert "host_ip: 127.0.0.1" in deploy
    assert 'published: "18443"' in deploy
    assert 'published: "443"' in deploy
    assert "127.0.0.1:18443" not in deploy
    assert "127\\.0\\.0\\.1:443" not in deploy
    assert 'cd "$APP_DIR"' in deploy
    assert 'chmod 0644 -- "$postgres_init_script"' in deploy
    assert '[[ -f "$postgres_init_script" && ! -L "$postgres_init_script" ]]' in deploy

    dev1_compose = (ROOT / "docker-compose.dev1.yml").read_text(encoding="utf-8")
    assert 'profiles: ["office-addin-disabled-on-dev1"]' in dev1_compose
    assert 'extra_hosts:' in dev1_compose
    assert '- "office-addin=127.0.0.1"' in dev1_compose

    for installer in (dev1, dr):
        assert 'deploy_user="${DEPLOY_USER:-varta}"' in installer
        assert 'runner_user="${RUNNER_USER:-lawhand-runner}"' in installer
        assert 'id -u "$runner_user" >/dev/null' in installer
        assert '"$runner_user ALL=(root) NOPASSWD:' in installer
        assert '"$deploy_user ALL=(root) NOPASSWD:' not in installer
    rehearsal = (ROOT / "scripts" / "skynet_dr_rehearsal.sh").read_text(
        encoding="utf-8"
    )
    assert "install -m 0755 -o root -g root" in dr
    assert "/usr/local/libexec/lawhand-dr" in dr
    assert "runuser -u varta" not in dr
    assert "DR_ENV_FILE=/etc/lawhand/skynet-dr.env" in dr
    assert "DR_ENV_FILE=/home/varta/.config/lawhand/dr.env" not in dr
    assert 'install -m 0600 -o root -g root "$password_source"' in dr
    assert 'install -m 0600 -o root -g root "$credential_tmp"' in dr
    assert "RESTIC_PASSWORD_FILE=$password_file" in dr
    assert 'DR_STATE_DIR="$STATE_DIR"' in dr
    assert 'DR_RELEASE_SHA="$RELEASE_SHA"' in dr
    assert 'chown root:varta "$STATE_DIR"' in dr
    assert 'chmod 0750 "$STATE_DIR"' in dr
    assert 'chown root:varta "$STATUS_FILE"' in dr
    assert "systemctl restart lawhand-skynet-status.service" in dr
    assert "DR_RELEASE_SHA" in rehearsal
    assert 'release_sha="$(git -C "$APP_DIR" rev-parse HEAD)"' in rehearsal


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
    assert "PRUNE_OLD_BACKUPS=true" in service
    assert "PRUNE_OLD_BACKUPS_CONFIRM=delete-old-legalapp-backups" in service
    assert "BACKUP_RETENTION_DAYS=2" in service
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
    assert "BACKEND_WORKERS=1" in rehearsal
    assert "proves boot and behavior, not production" in rehearsal
    assert "OFFSITE_RESTORE_PUBLIC_KEY_FILE=" in rehearsal
    assert 'compose_files=("${production_compose_files[@]}"' in rehearsal
    assert 'COMPOSE_FILES="$preflight_compose_files_value"' in rehearsal
    assert 'COMPOSE_FILES="$compose_files_value"' not in rehearsal
    assert 'extra_headers={"X-Forwarded-Proto": "https"}' in rehearsal
    assert "plain=301,edge=200,https=200,frontend=200" in rehearsal
    assert 'plain_headers.get_all("Strict-Transport-Security", []) == []' in rehearsal
    assert '"https://rehearsal.invalid/health/readiness"' in rehearsal
    assert "schema-valid synthetic artifact solely to exercise" in rehearsal
    assert "os.chmod(path, 0o644)" in rehearsal
    assert (
        "for service in postgres redis litellm-postgres litellm migrator backend scheduler frontend nginx; do"
        in rehearsal
    )
    assert '"${compose[@]}" build "$service"' in rehearsal
    assert "docker builder prune --all --force" in rehearsal
    assert "memory: 768M" in rehearsal
    assert "cgroup_memory_current" in rehearsal
    assert "docker stats --no-stream --no-trunc" in rehearsal
    assert "litellm-schema-check" in rehearsal
    assert "run --rm --no-deps litellm-schema-check -c" in rehearsal
    assert "stop litellm" in rehearsal
    assert "up -d --no-deps litellm" in rehearsal
    assert "LITELLM_SCHEMA_CHECK=passed" in rehearsal
    assert "LITELLM_RECOVERY=healthy" in rehearsal
    assert "exec -T litellm sh -c" not in rehearsal
    assert '"${compose[@]}" up -d --build' not in rehearsal


def test_fresh_host_refreshes_host_disk_status_after_image_build() -> None:
    rehearsal = (ROOT / "scripts" / "rehearse_fresh_host.sh").read_text(
        encoding="utf-8"
    )
    probe = 'python3 "$APP_DIR/scripts/update_host_disk_status.py"'

    assert rehearsal.count(probe) == 2
    initial_probe = rehearsal.find(probe)
    image_build = rehearsal.find('COMPOSE_PARALLEL_LIMIT=1 "${compose[@]}" build')
    refreshed_probe = rehearsal.find(probe, initial_probe + len(probe))
    readiness_waiter = rehearsal.find("python -m app.services.readiness_wait")

    assert -1 not in (initial_probe, image_build, refreshed_probe, readiness_waiter)
    assert initial_probe < image_build < refreshed_probe < readiness_waiter
    assert (
        'urllib.request.urlopen("http://127.0.0.1:8000/health/readiness"'
        not in rehearsal
    )


def test_private_origin_tls_contract_is_loopback_pinned() -> None:
    hypervisor = (ROOT / "docker-compose.hypervisor.yml").read_text(encoding="utf-8")
    preflight = PREFLIGHT.read_text(encoding="utf-8")
    production_check = PRODUCTION_CHECK.read_text(encoding="utf-8")
    validator = (ROOT / "scripts" / "validate_private_origin_tls.sh").read_text(
        encoding="utf-8"
    )
    provisioner = (ROOT / "scripts" / "provision_private_origin_tls.sh").read_text(
        encoding="utf-8"
    )
    finalizer = (ROOT / "scripts" / "finalize_private_origin_ca_rotation.sh").read_text(
        encoding="utf-8"
    )
    nginx = (ROOT / "nginx" / "nginx.conf").read_text(encoding="utf-8")
    assert '"127.0.0.1:80:80"' in hypervisor
    assert '"127.0.0.1:443:443"' in hypervisor
    assert "validate_private_origin_tls.sh" in preflight
    assert 'origin_tls_cert_file="$ROOT_DIR/nginx/ssl/fullchain.pem"' in preflight
    assert 'ORIGIN_TLS_CERT_FILE="$origin_tls_cert_file"' in preflight
    assert 'ORIGIN_TLS_KEY_FILE="$ROOT_DIR/nginx/ssl/privkey.pem"' in production_check
    assert '--tlsv"$tls_version" --tls-max "$tls_version"' in production_check
    assert "--tlsv1.1 --tls-max 1.1" in production_check
    assert "--noproxy '*'" in production_check
    assert 'cloudflared_service="cloudflared"' in production_check
    assert 'systemctl is-active --quiet "$cloudflared_service"' in production_check
    assert (
        'systemctl show "$cloudflared_service" --property=MainPID --value'
        in production_check
    )
    assert '"/proc/$cloudflared_pid/cmdline"' in production_check
    assert '"/proc/$cloudflared_pid/exe"' in production_check
    assert (
        'expected_cloudflared_exe="$(readlink -f -- "$CLOUDFLARED_BIN"'
        in production_check
    )
    assert '"$cloudflared_exe" != "$expected_cloudflared_exe"' in production_check
    assert "mapfile -d '' -t cloudflared_cmdline" in production_check
    assert '"--config=$CLOUDFLARED_CONFIG_FILE"' in production_check
    assert (
        '"--config" && "${cloudflared_cmdline[arg_index + 1]:-}" == "$CLOUDFLARED_CONFIG_FILE"'
        in production_check
    )
    assert "cloudflared_config_count" in production_check
    assert "exactly one CLOUDFLARED_CONFIG_FILE argument" in production_check
    assert '"--no-tls-verify"' in production_check
    assert 'fail "cloudflared service is not active"' in production_check
    assert "--require-production-ownership" in production_check
    assert "--require-production-ownership" in preflight
    assert "noTLSVerify" in validator
    assert "https://127.0.0.1:443" in validator
    validator_lines = {line.strip() for line in validator.splitlines()}
    assert "'  - hostname: www.getlawhand.com' \\" in validator_lines
    assert "http2Origin" in validator
    assert "minimum days must be between 1 and 3650" in validator
    assert "private origin trust directory must be root-owned" in validator
    assert "cloudflared config directory must be root-owned" in validator
    assert "cloudflared binary must be root-owned" in validator
    assert "canonical pinned HTTPS route contract" in validator
    assert '"$cloudflared_bin_resolved" --config "$config"' in validator
    assert "managed nginx TLS file owner differs from its directory" in validator
    assert ".private-origin-managed" in provisioner
    assert "if (( rotate_ca ))" in provisioner
    assert "force=1" in provisioner
    assert "transaction_dirty" in provisioner and "rollback_snapshots" in provisioner
    assert "flock -n 9" in provisioner
    assert "dual-ca-bundle.pem" in provisioner
    assert "deployed origin certificate and key do not match" in provisioner
    assert "dual-trust CA export must contain exactly two certificates" in finalizer
    assert "finalized CA export does not contain exactly one certificate" in finalizer
    assert "transaction_dirty" in finalizer and "rollback_failed" in finalizer
    assert "flock -n 9" in finalizer
    for legacy_script in ("init-letsencrypt.sh", "renew-cert.sh"):
        content = (ROOT / "nginx" / legacy_script).read_text(encoding="utf-8")
        assert ".private-origin-managed" in content
        assert (
            '[[ -L "$PRIVATE_ORIGIN_MARKER" || -e "$PRIVATE_ORIGIN_MARKER" ]]'
            in content
        )
        assert (
            '[[ -f "$PRIVATE_ORIGIN_MARKER" && ! -L "$PRIVATE_ORIGIN_MARKER" ]]'
            in content
        )
    renew = (ROOT / "nginx" / "renew-cert.sh").read_text(encoding="utf-8")
    assert '[[ ! -f "$CERT_FILE" || ! -f "$KEY_FILE" ]]' in renew
    assert "CERT_PUBKEY" in renew and "KEY_PUBKEY" in renew
    assert 'STAGED_CERT="$SSL_DIR/.fullchain.pem.new.$$"' in renew
    assert 'STAGED_KEY="$SSL_DIR/.privkey.pem.new.$$"' in renew
    assert "rollback" in renew
    assert 'if ! "${COMPOSE[@]}" exec -T nginx nginx -s reload; then' in renew
    assert "provision_private_origin_tls.sh" in nginx
    runbook = (ROOT / "docs" / "FIRST_CUSTOMER_PRODUCTION_RUNBOOK.md").read_text(
        encoding="utf-8"
    )
    assert "init-letsencrypt.sh" in runbook
