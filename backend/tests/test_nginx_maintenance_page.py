"""Regression gates for workstream C: nginx survives backend restarts.

The production nginx resolves its upstreams at request time through Docker's
embedded DNS and serves a baked-in maintenance page while the app tier
restarts, so a release degrades to a branded "back soon" page instead of a
bare connection failure.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    not (ROOT / "nginx" / "nginx.conf").is_file(),
    reason="nginx maintenance gates require a full repository checkout",
)

NGINX_CONF = ROOT / "nginx" / "nginx.conf"
MAINTENANCE_PAGE = ROOT / "nginx" / "maintenance.html"
DOCKERFILE_LOCAL = ROOT / "nginx" / "Dockerfile.local"
HYPERVISOR_COMPOSE = ROOT / "docker-compose.hypervisor.yml"

# A proxy_pass carrying a URI part (e.g. `http://backend/api/`) changes the
# forwarded path when combined with rewrites; the maintenance-page workstream
# forbids introducing one silently.
_URI_PART_PROXY_PASS = re.compile(r"proxy_pass\s+https?://[^\s;$]+/(?!\s*;)\S*\s*;")


def _compose_text() -> str:
    return HYPERVISOR_COMPOSE.read_text(encoding="utf-8")


def test_nginx_conf_uses_embedded_dns_resolver():
    nginx = NGINX_CONF.read_text(encoding="utf-8")
    assert "resolver 127.0.0.11 valid=10s;" in nginx
    # Bounds negative caching so a cold-boot NXDOMAIN recovers within ~10s.


def test_nginx_conf_has_no_static_upstream_proxy_passes():
    nginx = NGINX_CONF.read_text(encoding="utf-8")
    snippets = "\n".join(
        snippet.read_text(encoding="utf-8")
        for snippet in sorted((ROOT / "nginx" / "snippets").glob("*.conf"))
    )
    for upstream in ("backend", "frontend", "office_addin"):
        assert f"upstream {upstream} " not in nginx
        assert f"proxy_pass http://{upstream};" not in nginx
        assert f"proxy_pass http://{upstream}/" not in nginx + snippets
    # Every former static target is now the runtime-resolved variable form.
    assert "proxy_pass $upstream_backend;" in nginx + snippets
    assert "proxy_pass $upstream_frontend;" in nginx
    assert "proxy_pass $upstream_office_addin;" in snippets
    # No proxy_pass may smuggle in a URI part (rewritten-path semantics).
    assert not _URI_PART_PROXY_PASS.search(nginx + snippets)


def test_nginx_conf_falls_back_to_the_maintenance_page():
    nginx = NGINX_CONF.read_text(encoding="utf-8")
    # Both the :80 and :443 user-facing servers get the fallback.
    assert nginx.count("error_page 502 503 504 /maintenance.html;") == 2
    assert nginx.count("location = /maintenance.html {") == 2
    maintenance_block = nginx[nginx.index("location = /maintenance.html {") :]
    maintenance_block = maintenance_block[: maintenance_block.index("}")]
    assert "internal;" in maintenance_block
    assert "root /usr/share/nginx/html;" in maintenance_block


def test_hypervisor_nginx_service_has_no_depends_on():
    compose = yaml.safe_load(_compose_text())
    nginx = compose["services"]["nginx"]
    assert "depends_on" not in nginx, (
        "nginx must start without waiting for upstream health so a cold boot "
        "serves the maintenance page until the app tier is reachable"
    )
    # Volumes, loopback-only ports, and restart policy are unchanged.
    assert nginx["volumes"]
    assert nginx["ports"] == ["127.0.0.1:80:80", "127.0.0.1:443:443"]
    assert nginx["restart"] == "unless-stopped"


def test_nginx_image_bakes_in_the_maintenance_page():
    dockerfile = DOCKERFILE_LOCAL.read_text(encoding="utf-8")
    assert "COPY maintenance.html /usr/share/nginx/html/maintenance.html" in dockerfile


def test_maintenance_page_exists_is_branded_and_leaks_nothing():
    page = MAINTENANCE_PAGE.read_text(encoding="utf-8")
    assert "LawHand" in page
    assert "maintenance" in page.lower()
    lowered = page.lower()
    for leak_word in ("nginx", "docker", "ionos", "container", "compose"):
        assert leak_word not in lowered, f"maintenance page leaks: {leak_word}"
    # No build/host/version detail: no hex SHA-like tokens or version strings.
    assert not re.search(r"\b[0-9a-f]{7,40}\b", page)
    assert not re.search(r"\bv\d+\.\d+(\.\d+)?\b", page)
    # Self-contained: no external assets.
    assert "http://" not in page and "https://" not in page
