"""Regression gates for shipping the frontend baked into its container image."""

from __future__ import annotations

import json
import os
import re
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


class _ComposeSourceLoader(yaml.SafeLoader):
    """Parse source Compose files while preserving Compose merge-tag values."""


def _construct_compose_merge_tag(
    loader: _ComposeSourceLoader, node: yaml.Node
) -> object:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


for _compose_tag in ("!override", "!reset"):
    _ComposeSourceLoader.add_constructor(_compose_tag, _construct_compose_merge_tag)


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
        config = (
            yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeSourceLoader)
            or {}
        )
        _assert_no_stale_frontend_mounts(
            config, path.name, require_frontend_build=False
        )


def test_production_topologies_require_explicit_public_site_origin() -> None:
    for filename in ("docker-compose.prod.yml", "docker-compose.hypervisor.yml"):
        compose_text = (ROOT / filename).read_text(encoding="utf-8")
        assert (
            "VITE_PUBLIC_SITE_URL: ${VITE_PUBLIC_SITE_URL:?" in compose_text
        ), f"{filename} must not default SEO canonicals to a deployment-specific host"


def test_hypervisor_origin_ports_are_loopback_only() -> None:
    config = yaml.safe_load(
        (ROOT / "docker-compose.hypervisor.yml").read_text(encoding="utf-8")
    )

    assert set(config["services"]["nginx"]["ports"]) == {
        "127.0.0.1:80:80",
        "127.0.0.1:443:443",
    }


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
    assert "frame-src 'self' blob:" in csp_lines[0]
    assert "'unsafe-eval'" not in csp_lines[0]
    # Google Analytics runs only on the public marketing pages, so its hosts
    # enter the policy through a request-scoped variable rather than being
    # granted to every response. An inline snippet is never allowed.
    assert "script-src 'self'$csp_analytics_script;" in csp_lines[0]
    assert "'unsafe-inline'" not in csp_lines[0].split("style-src")[0]
    public_routes = "privacy|terms|pricing|request-demo|product(?:/(?:chat|mcp))?"
    assert nginx.count(f"location ~ ^/({public_routes})/?$ {{") == 2
    assert nginx.count(f"rewrite ^/({public_routes})/?$ /$1/index.html break;") == 2


def test_nginx_allows_analytics_hosts_only_on_public_marketing_pages() -> None:
    """A signed-in URL can carry a matter, client, or portal identifier.

    The analytics tag must therefore never be permitted to load on a workspace
    response, where a page_view would send that path to Google. The CSP grants
    Google's hosts through request-scoped variables that resolve to an empty
    string for every path outside the public marketing routes.
    """
    nginx = (ROOT / "nginx" / "nginx.conf").read_text(encoding="utf-8")

    for name in ("$csp_analytics_script", "$csp_analytics_endpoints"):
        start = nginx.index(f"map $request_uri {name} {{")
        block = nginx[start : nginx.index("}", start)]
        # Anything not matched by an explicit public-route pattern gets nothing.
        assert re.search(
            r'default\s+"";', block
        ), f"{name} must deny the analytics hosts by default"
        for route in ("privacy|terms|pricing|request-demo", "product"):
            assert route in block, f"{name} must cover the {route} pages"

    # Every route the SEO config publishes must be able to load the tag, or the
    # property silently under-reports the pages that matter most.
    start = nginx.index("map $request_uri $csp_analytics_script {")
    script_map = nginx[start : nginx.index("}", start)]
    patterns = [
        re.compile(match.group(1)) for match in re.finditer(r"~(\S+)\s+\"", script_map)
    ]
    for path in _indexable_public_paths():
        assert any(
            pattern.search(path) for pattern in patterns
        ), f"{path} is indexable but nginx blocks the analytics tag there"


def _indexable_public_paths() -> list[str]:
    """Canonical paths that frontend/src/seo/config.js publishes as indexable."""
    config = (ROOT / "frontend" / "src" / "seo" / "config.js").read_text(
        encoding="utf-8"
    )
    start = config.index("export const PUBLIC_ROUTE_META")
    end = config.index("const WORKSPACE_ROUTE_TITLES", start)
    block = config[start:end]
    paths = [
        match.group(1) for match in re.finditer(r"canonicalPath: '([^']+)'", block)
    ]
    assert paths, "no canonical paths parsed from the SEO config"
    return paths


def test_nginx_never_noindexes_a_route_the_sitemap_publishes() -> None:
    """An X-Robots-Tag header silently overrides a page's own robots meta tag.

    Every route the SEO config marks indexable (and therefore lists in
    sitemap.xml) must match one of the allowlist patterns in the
    ``$x_robots_tag`` map, or search engines are told to ignore a page the site
    is actively asking them to crawl.
    """
    nginx = (ROOT / "nginx" / "nginx.conf").read_text(encoding="utf-8")
    start = nginx.index("map $request_uri $x_robots_tag {")
    allowlist = nginx[start : nginx.index("}", start)]

    patterns = [
        re.compile(match.group(1)) for match in re.finditer(r'~(\S+)\s+"";', allowlist)
    ]
    assert patterns, "no allowlist entries parsed from the x_robots_tag map"

    for path in _indexable_public_paths():
        assert any(pattern.search(path) for pattern in patterns), (
            f"{path} is indexable in the SEO config but nginx serves it with "
            "a noindex X-Robots-Tag header"
        )


def test_nginx_never_advertises_the_mcp_hostnames_as_indexable() -> None:
    """The MCP hostnames publish protocol endpoints, not pages.

    ``$x_robots_tag`` keys on the request path, and "/" is indexable there
    because the marketing home page lives at "/". The dedicated MCP hostnames
    answer their root with a JSON protocol error, so the header must be chosen
    per host or a crawler is invited to index that error.
    """
    nginx = (ROOT / "nginx" / "nginx.conf").read_text(encoding="utf-8")
    start = nginx.index("map $host $robots_tag {")
    host_map = nginx[start : nginx.index("}", start)]

    assert (
        "default" in host_map and "$x_robots_tag" in host_map
    ), "the host-keyed robots map must fall back to the path-keyed value"
    for host in ("mcp.getlawhand.com", "research.getlawhand.com"):
        assert re.search(
            rf'"{re.escape(host)}"\s+"noindex, nofollow, noarchive";', host_map
        ), f"{host} must be served with a noindex X-Robots-Tag header"

    # Both server blocks must emit the host-aware variable; a block left on
    # $x_robots_tag would publish the endpoint as indexable on that listener.
    assert nginx.count("add_header X-Robots-Tag $robots_tag always;") == 2
    assert "add_header X-Robots-Tag $x_robots_tag always;" not in nginx


def test_nginx_collapses_the_www_hostname_onto_the_apex() -> None:
    """Serving both hostnames publishes every page at two addresses.

    The redirect must run in both the plain-HTTP and TLS servers, must use 308
    so a POST that reaches www keeps its method and body, and must never catch
    the ACME challenge path or certificate renewal breaks.
    """
    nginx = (ROOT / "nginx" / "nginx.conf").read_text(encoding="utf-8")

    assert nginx.count("if ($redirect_to_apex) {") == 2
    assert nginx.count("return 308 https://$redirect_to_apex$request_uri;") == 2
    assert "return 301 https://$redirect_to_apex" not in nginx

    start = nginx.index("map $host$uri $redirect_to_apex {")
    apex_map = nginx[start : nginx.index("}", start)]
    acme = apex_map.index("acme-challenge")
    www = apex_map.index("$apex;")
    # nginx checks map regexes in definition order, so the exemption only holds
    # while the ACME entry precedes the catch-all www entry.
    assert acme < www, "the ACME exemption must be defined before the www catch-all"


def test_production_nginx_keeps_platform_routes_active_in_http_and_tls() -> None:
    nginx = (ROOT / "nginx" / "nginx.conf").read_text(encoding="utf-8")
    assert nginx.count("location /api/platform/ {") == 2
    assert not any(
        "location /api/platform/" in line and line.lstrip().startswith("#")
        for line in nginx.splitlines()
    )
