"""Regression gates for shipping the frontend baked into its container image."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    not (ROOT / "docker-compose.yml").is_file(),
    reason="frontend delivery gates require a full repository checkout",
)
DIST_PATH = PurePosixPath("/app/dist")
LEGACY_NGINX_DIST_PATH = PurePosixPath("/usr/share/nginx/html/dist")
COMPOSE_VARIANTS = {
    "base": ("docker-compose.yml",),
    "dev": ("docker-compose.yml", "docker-compose.override.yml"),
    "local": ("docker-compose.yml", "docker-compose.local.yml"),
    "hypervisor": ("docker-compose.hypervisor.yml",),
    "production": ("docker-compose.yml", "docker-compose.prod.yml"),
}


def _volume_target(volume: object) -> str | None:
    if isinstance(volume, dict):
        target = volume.get("target")
        return str(target) if target else None
    if not isinstance(volume, str):
        return None
    # Compose short syntax is source:target[:mode]. Windows drive letters only
    # occur on the source side; the final absolute POSIX segment is the target.
    parts = volume.split(":")
    return next((part for part in parts[1:] if part.startswith("/")), None)


def _volume_source(volume: object) -> str | None:
    if isinstance(volume, dict):
        source = volume.get("source")
        return str(source) if source else None
    if not isinstance(volume, str) or ":" not in volume:
        return None
    return volume.split(":", 1)[0]


def _masks(target: str | None, protected: PurePosixPath) -> bool:
    if not target:
        return False
    mounted = PurePosixPath(target)
    return mounted == protected or mounted in protected.parents


def _assert_no_stale_frontend_mounts(
    config: dict, label: str, *, require_frontend_build: bool = True
) -> None:
    services = config.get("services") or {}
    frontend = services.get("frontend") or {}
    if require_frontend_build:
        assert frontend.get(
            "build"
        ), f"{label}: frontend must be built from this checkout"

    for volume in frontend.get("volumes") or []:
        target = _volume_target(volume)
        assert not _masks(
            target, DIST_PATH
        ), f"{label}: frontend volume target {target!r} masks image-baked /app/dist"
        assert (
            _volume_source(volume) != "frontend_dist"
        ), f"{label}: legacy frontend_dist volume reintroduced"

    for service_name, service in services.items():
        if "nginx" not in service_name:
            continue
        for volume in (service or {}).get("volumes") or []:
            target = _volume_target(volume)
            assert not _masks(
                target, LEGACY_NGINX_DIST_PATH
            ), f"{label}: {service_name} still mounts the legacy shared dist path"
            assert (
                _volume_source(volume) != "frontend_dist"
            ), f"{label}: {service_name} still consumes legacy frontend_dist"

    assert "frontend_dist" not in (
        config.get("volumes") or {}
    ), f"{label}: obsolete frontend_dist volume remains declared"


def _resolved_compose(files: tuple[str, ...]) -> dict:
    command = ["docker", "compose"]
    for filename in files:
        command.extend(("-f", str(ROOT / filename)))
    command.append("config")
    help_result = subprocess.run(
        ["docker", "compose", "config", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if "--no-env-resolution" in help_result.stdout:
        command.append("--no-env-resolution")
    command.extend(("--format", "json"))
    env = os.environ.copy()
    # Compose service ``env_file`` entries must resolve on a pristine checkout;
    # production still defaults to the untracked ``.env`` when this is unset.
    env["APP_ENV_FILE"] = ".env.prod.example"
    env.setdefault("DOMAIN", "compose-test.invalid")
    env.setdefault("POSTGRES_PASSWORD", "compose-owner-password-0123456789")
    env.setdefault("CLARITY_APP_PASSWORD", "compose-runtime-password-0123456789")
    env.setdefault("REDIS_PASSWORD", "compose-redis-password-0123456789")
    env.setdefault("LITELLM_DB_PASSWORD", "compose-litellm-password-0123456789")
    env.setdefault("LITELLM_API_KEY", "compose-litellm-api-key-0123456789")
    env.setdefault("LITELLM_SALT_KEY", "compose-permanent-litellm-salt-0123456789")
    env.setdefault(
        "LITELLM_DATABASE_URL",
        "postgresql://litellm:compose-litellm-password-0123456789@litellm-postgres:5432/litellm",
    )
    env.setdefault(
        "DATABASE_URL",
        "postgresql+asyncpg://legalapp:compose-owner-password-0123456789@postgres:5432/legalapp",
    )
    env.setdefault(
        "MIGRATOR_DATABASE_URL",
        "postgresql+asyncpg://legalapp:compose-owner-password-0123456789@postgres:5432/legalapp",
    )
    env.setdefault(
        "APP_DATABASE_URL",
        "postgresql+asyncpg://clarity_app:compose-runtime-password-0123456789@postgres:5432/legalapp",
    )
    env.setdefault("UPLOADS_HOST_DIR", "/tmp/legalapp-compose-uploads")
    env.setdefault("HOST_STATUS_HOST_DIR", "/tmp/legalapp-compose-host-status")
    env.setdefault("EMAIL_ENABLED", "false")
    env.setdefault("EMAIL_HOST", "smtp.compose-test.invalid")
    env.setdefault("EMAIL_PORT", "587")
    env.setdefault("EMAIL_USER", "")
    env.setdefault("EMAIL_PASS", "")
    env.setdefault("EMAIL_FROM", "noreply@compose-test.invalid")
    env.setdefault("VITE_PUBLIC_SITE_URL", "https://compose-test.invalid")
    env.setdefault("VITE_CONTACT_URL", "mailto:compose-test@example.invalid")
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert (
        result.returncode == 0
    ), f"Compose resolution failed for {files}:\n{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout)


def test_compose_sources_never_mask_image_baked_frontend_dist() -> None:
    for path in sorted(ROOT.glob("docker-compose*.yml")):
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        _assert_no_stale_frontend_mounts(
            config, path.name, require_frontend_build=False
        )


def test_production_topologies_require_explicit_public_site_origin() -> None:
    for filename in ("docker-compose.prod.yml", "docker-compose.hypervisor.yml"):
        compose_text = (ROOT / filename).read_text(encoding="utf-8")
        assert (
            "VITE_PUBLIC_SITE_URL: ${VITE_PUBLIC_SITE_URL:?" in compose_text
        ), f"{filename} must not default SEO canonicals to a deployment-specific host"


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI unavailable")
def test_resolved_compose_variants_ship_image_baked_frontend() -> None:
    for label, files in COMPOSE_VARIANTS.items():
        config = _resolved_compose(files)
        _assert_no_stale_frontend_mounts(config, label)
        if label in {"production", "hypervisor"}:
            assert (
                config["services"]["frontend"]["build"]["args"]["VITE_PUBLIC_SITE_URL"]
                == "https://compose-test.invalid"
            ), f"{label} SEO canonical must match the configured public site URL"
        if label in {"production", "hypervisor"}:
            services = config["services"]
            assert not services["backend"].get("ports")
            assert not services["frontend"].get("ports")
            nginx_targets = {
                int(port["target"]) for port in services["nginx"].get("ports", [])
            }
            assert nginx_targets == {80, 443}


def test_frontend_runtime_verifier_is_part_of_production_deploy() -> None:
    ignored = {
        line.strip()
        for line in (ROOT / "frontend" / ".dockerignore")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "node_modules/" in ignored
    assert "dist/" in ignored

    dockerfile = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY --from=build /app/dist ./dist" in dockerfile
    assert "COPY serve.json ./serve.json" in dockerfile
    assert (
        'CMD ["serve", "--no-port-switching", "-s", "dist", "-l", "3000", '
        '"--config", "/app/serve.json"]' in dockerfile
    )
    serve_config = json.loads(
        (ROOT / "frontend" / "serve.json").read_text(encoding="utf-8")
    )
    assert serve_config == {"cleanUrls": False}

    verifier = ROOT / "scripts" / "verify_frontend_runtime.sh"
    assert verifier.is_file()
    image_gate = ROOT / "scripts" / "test_frontend_image_delivery.sh"
    assert image_gate.is_file()
    deploy_script = (ROOT / "scripts" / "deploy_prod.sh").read_text(encoding="utf-8")
    assert "verify_frontend_runtime.sh" in deploy_script


def test_nginx_operator_routes_and_pdf_csp_are_consistent() -> None:
    nginx = (ROOT / "nginx" / "nginx.conf").read_text(encoding="utf-8")

    # Edge-terminated HTTP and direct TLS must both select the dedicated
    # operator rate/timeout policy rather than falling through to /api/.
    assert nginx.count("location /api/platform/ {") == 2
    assert nginx.count("limit_req zone=platform burst=30 nodelay;") == 2
    assert not any(
        "location /api/platform/" in line and line.lstrip().startswith("#")
        for line in nginx.splitlines()
    )

    csp_lines = [
        line.strip()
        for line in nginx.splitlines()
        if "add_header Content-Security-Policy" in line
    ]
    assert len(csp_lines) == 2
    assert csp_lines[0] == csp_lines[1]
    assert "object-src 'self' blob:" in csp_lines[0]
    assert "script-src 'self';" in csp_lines[0]
    assert "'unsafe-eval'" not in csp_lines[0]
    assert nginx.count("location ~ ^/(privacy|terms)/?$ {") == 2
    assert nginx.count("rewrite ^/(privacy|terms)/?$ /$1/index.html break;") == 2


def test_production_nginx_keeps_platform_routes_active_in_http_and_tls() -> None:
    nginx = (ROOT / "nginx" / "nginx.conf").read_text(encoding="utf-8")
    assert nginx.count("location /api/platform/ {") == 2
    assert not any(
        "location /api/platform/" in line and line.lstrip().startswith("#")
        for line in nginx.splitlines()
    )
