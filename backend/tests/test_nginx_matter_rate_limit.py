"""Edge rate-limit buckets must be per caller, not per office.

Two invariants live here.

The matter workspace must not 429 itself on ordinary navigation: a single
matter view fans out dashboard, signatures, messages, documents, intake and
tasks in one paint, so those reads need a bucket that absorbs the burst
without loosening the general API zone for every other route.

And the buckets must be keyed on the caller. ``$binary_remote_addr`` collapses
a whole firm into one bucket — every user at an office shares one public NAT
address — so a 50-seat customer shared the 60r/m general budget between all of
them. Authenticated application traffic is keyed on ``$lh_limit_key`` instead,
which resolves to a stable per-user fragment of the access token and falls back
to the IP only when the caller is anonymous.
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

# Zones serving authenticated application traffic: keyed on the caller.
PER_CALLER_ZONES = ("api", "matter", "qbo")
# Zones whose callers are anonymous (or whose credential is not the firm
# access-token cookie): keyed on the source address.
PER_ADDRESS_ZONES = (
    "auth",
    "oauth",
    "platform",
    "webhook",
    "mcp_workspace",
    "mcp_research",
)


@pytest.fixture(scope="module")
def nginx() -> str:
    return NGINX_CONF.read_text(encoding="utf-8")


def test_matter_routes_use_a_dedicated_burst_zone(nginx: str):
    # Both the :80 and :443 servers route matter and task reads through it.
    assert nginx.count("limit_req zone=matter burst=80 nodelay;") == 4
    assert nginx.count("location ^~ /api/matters {") == 2
    assert nginx.count("location ^~ /api/tasks {") == 2
    # Everything else keeps the tighter general bucket.
    assert nginx.count("limit_req zone=api burst=40 nodelay;") >= 2


@pytest.mark.parametrize("zone", PER_CALLER_ZONES)
def test_application_zones_are_keyed_on_the_caller(nginx: str, zone: str):
    """A firm behind one NAT address must not share one bucket."""
    assert re.search(
        rf"limit_req_zone\s+\$lh_limit_key\s+zone={zone}:\d+m\s+rate=\d+r/m;",
        nginx,
    ), f"zone {zone} should be keyed on $lh_limit_key"


@pytest.mark.parametrize("zone", PER_ADDRESS_ZONES)
def test_anonymous_zones_stay_keyed_on_the_address(nginx: str, zone: str):
    """Brute-force and unauthenticated-ingest limits are only meaningful per IP."""
    assert re.search(
        rf"limit_req_zone\s+\$binary_remote_addr\s+zone={zone}:\d+m\s+rate=\d+r/m;",
        nginx,
    ), f"zone {zone} should stay keyed on $binary_remote_addr"


def test_caller_key_falls_back_to_the_address(nginx: str):
    """An anonymous request must land on the IP key, never on a shared bucket.

    The chain is cookie -> Authorization header -> token payload prefix -> IP.
    Each map needs its empty-value fallback or unauthenticated callers would
    all collapse onto one empty key, which is strictly worse than per-IP.
    """
    assert re.search(
        r'map\s+\$cookie_access_token\s+\$lh_session_token\s*\{[^}]*""\s+\$http_authorization;',
        nginx,
    )
    assert re.search(
        r'map\s+\$lh_session_token\s+\$lh_caller\s*\{[^}]*default\s+"";',
        nginx,
    )
    assert re.search(
        r'map\s+\$lh_caller\s+\$lh_limit_key\s*\{[^}]*""\s+\$binary_remote_addr;',
        nginx,
    )


def test_caller_key_is_bounded_and_signature_free(nginx: str):
    """The key is a fixed-length slice of the payload segment only.

    Bounded so one zone entry stays small, and stopping before the third JWT
    segment so no signature material is held in the shared memory zone.
    """
    match = re.search(
        r'"~\^\(\?:\[Bb\]earer\\s\+\)\?\[A-Za-z0-9_-\]\+\\\.'
        r"\(\?<lh_jwt_head>\[A-Za-z0-9_-\]\{(\d+)\}\)\"",
        nginx,
    )
    assert match, "the caller key must match a bounded payload-segment prefix"
    width = int(match.group(1))
    # 4 base64 characters per 3 bytes. "sub" is the first claim and is a
    # 36-character UUID, so the window has to reach past `{"sub":"<uuid>"`.
    assert (width * 3) // 4 >= 46
    # But not so wide that it reaches iat/jti/exp, which change on every
    # reissue and would let a caller reset its bucket by refreshing.
    assert width <= 160


def test_provider_webhooks_did_not_inherit_the_wider_api_zone(nginx: str):
    """Raising `api` for real users must not raise unauthenticated ingest.

    The webhook locations used to share the `api` zone at 60r/m. `api` is now
    a per-caller zone at a much higher rate, so webhooks moved to their own
    per-IP zone that preserves the original ceiling.
    """
    assert nginx.count("limit_req zone=webhook burst=20 nodelay;") == 8
    assert re.search(
        r"limit_req_zone\s+\$binary_remote_addr\s+zone=webhook:\d+m\s+rate=60r/m;",
        nginx,
    )
