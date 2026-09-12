"""The matter workspace must not 429 itself on ordinary navigation.

A single matter view fans out dashboard, signatures, messages, documents,
intake and tasks from one browser IP. Those reads need a bucket that absorbs
the burst without loosening the general API zone for every other route.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NGINX_CONF = ROOT / "nginx" / "nginx.conf"
pytestmark = pytest.mark.skipif(
    not NGINX_CONF.is_file(),
    reason="nginx rate-limit gate requires a full repository checkout",
)


def test_matter_routes_use_a_dedicated_burst_zone():
    nginx = NGINX_CONF.read_text(encoding="utf-8")

    assert re.search(
        r"limit_req_zone\s+\$binary_remote_addr\s+zone=matter:10m\s+rate=\d+r/m;",
        nginx,
    )
    # Both the :80 and :443 servers route matter and task reads through it.
    assert nginx.count("limit_req zone=matter burst=80 nodelay;") == 4
    assert nginx.count("location ^~ /api/matters {") == 2
    assert nginx.count("location ^~ /api/tasks {") == 2
    # Everything else keeps the tighter general bucket.
    assert nginx.count("limit_req zone=api burst=20 nodelay;") >= 2
