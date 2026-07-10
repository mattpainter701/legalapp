from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "scripts" / "prod_env_preflight.sh"
NEW_FERNET_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
OLD_FERNET_KEY = "KxzLuxmIM2dFDWQmKJL9LVUK5ouA0c3_-4VqCMrn-jY="


def _production_env(**overrides: str) -> str:
    values = {
        "DOMAIN": "ops-test.invalid",
        "BACKEND_URL": "https://ops-test.invalid",
        "FRONTEND_URL": "https://ops-test.invalid",
        "VITE_CONTACT_URL": "mailto:ops@example.invalid",
        "DEV_MODE": "false",
        "SECRET_KEY": "ops-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz",
        "MCP_PRODUCT_ENABLED": "false",
        "POSTGRES_PASSWORD": "owner-password-0123456789",
        "CLARITY_APP_PASSWORD": "runtime-password-0123456789",
        "REDIS_PASSWORD": "redis-password-0123456789",
        "REDIS_URL": "redis://:redis-password-0123456789@redis:6379/0",
        "MIGRATOR_DATABASE_URL": "postgresql+asyncpg://legalapp:owner-password-0123456789@postgres:5432/legalapp",
        "APP_DATABASE_URL": "postgresql+asyncpg://clarity_app:runtime-password-0123456789@postgres:5432/legalapp",
        "LITELLM_API_KEY": "litellm-api-key-0123456789",
        "LITELLM_DB_PASSWORD": "litellm-password-0123456789",
        "LITELLM_DATABASE_URL": "postgresql://litellm:litellm-password-0123456789@litellm-postgres:5432/litellm",
        "TOKEN_ENCRYPTION_KEY": OLD_FERNET_KEY,
        "TOKEN_ENCRYPTION_KEYS": f"{NEW_FERNET_KEY},{OLD_FERNET_KEY}",
        "MCP_SERVER_URL": "http://courtlistener-mcp:8000",
        "MCP_UPSTREAM_API_KEY": "mcp-upstream-key-0123456789-abcdef",
    }
    values.update(overrides)
    return "".join(f"{key}={value}\n" for key, value in values.items())


def _run_preflight(tmp_path: Path, env_text: str) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / ".env"
    env_file.write_text(env_text, encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ENV_FILE"] = str(env_file)
    env["COMPOSE_FILES"] = str(ROOT / "docker-compose.hypervisor.yml")
    return subprocess.run(
        ["bash", str(PREFLIGHT)],
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
            MCP_UPSTREAM_API_KEY="",
            TOKEN_ENCRYPTION_KEYS=OLD_FERNET_KEY,
        ),
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "MCP_PRODUCT_ENABLED must remain false" in output
    assert "MCP_UPSTREAM_API_KEY must be at least 32 characters" in output
    assert "TOKEN_ENCRYPTION_KEYS must contain at least new_key,old_key" in output
    assert OLD_FERNET_KEY not in output


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
