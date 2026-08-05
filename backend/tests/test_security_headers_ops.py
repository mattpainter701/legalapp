from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_hsts_covers_direct_and_edge_terminated_https() -> None:
    nginx = (ROOT / "nginx" / "nginx.conf").read_text(encoding="utf-8")

    assert "geo $realip_remote_addr $trusted_edge_peer" in nginx
    assert (
        'map "$trusted_edge_peer:$http_x_forwarded_proto" $trusted_edge_https' in nginx
    )
    assert '"1:https" 1;' in nginx
    assert "map $trusted_edge_https $edge_hsts_header" in nginx
    assert '1       "max-age=63072000; includeSubDomains";' in nginx
    assert 'map "$trusted_edge_https:$uri" $redirect_plain_http' in nginx
    assert r"~^0:/\.well-known/acme-challenge/ 0;" in nginx
    assert "if ($redirect_plain_http)" in nginx
    assert "return 301 https://$host$request_uri;" in nginx
    assert 'if ($http_x_forwarded_proto = "http")' not in nginx
    assert 'if ($http_x_forwarded_proto = "")' not in nginx
    assert (
        nginx.count("add_header Strict-Transport-Security $edge_hsts_header always;")
        == 2
    )
    assert (
        nginx.count(
            'add_header Strict-Transport-Security "max-age=63072000; '
            'includeSubDomains" always;'
        )
        == 2
    )


def test_sse_proxy_policy_preserves_server_security_header_inheritance() -> None:
    streaming = (ROOT / "nginx" / "snippets" / "sse_streaming.conf").read_text(
        encoding="utf-8"
    )
    runtime_gate = (ROOT / "scripts" / "test_nginx_webhook_ingress.sh").read_text(
        encoding="utf-8"
    )

    assert "proxy_buffering off;" in streaming
    assert "proxy_cache off;" in streaming
    assert "chunked_transfer_encoding on;" in streaming
    assert not any(
        line.lstrip().startswith("add_header ") for line in streaming.splitlines()
    )
    assert "X-Accel-Buffering" not in streaming

    # The Docker-backed gate exercises the shared /api/ location through the
    # edge-terminated HTTP path, direct TLS, direct/spoofed HTTP, and ACME.
    assert runtime_gate.count('http_request "/api/version"') == 3
    assert (
        'http_request "/api/version" "https" "$MOCK_CONTAINER" '
        '"$PROD_CONTAINER"' in runtime_gate
    )
    assert 'tls_request "/api/version"' in runtime_gate
    assert (
        'assert_plain_redirect "$plain_http" "/api/version" '
        '"plain HTTP /api/version"' in runtime_gate
    )
    assert 'assert_status "$spoofed_edge" "spoofed direct edge header" 301' in (
        runtime_gate
    )
    assert (
        'assert_header_absent "$response" "$label" "Strict-Transport-Security"'
        in runtime_gate
    )
    assert '"https://headers.test/api/version"' in runtime_gate
    assert "acme-header-proof" in runtime_gate
    for header in (
        "Strict-Transport-Security",
        "Content-Security-Policy",
        "X-Frame-Options",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "X-Robots-Tag",
    ):
        assert header in runtime_gate


def test_public_health_gates_require_hsts_and_api_http_redirect() -> None:
    production_check = (ROOT / "scripts" / "production_check.sh").read_text(
        encoding="utf-8"
    )
    scheduled_check = (
        ROOT / ".github" / "workflows" / "production-health.yml"
    ).read_text(encoding="utf-8")

    assert "PRODUCTION_ORIGIN: https://getlawhand.com" in scheduled_check
    assert "PRODUCTION_DOMAIN: getlawhand.com" in scheduled_check
    assert "[production-alert] LawHand readiness failure" in scheduled_check

    for check in (production_check, scheduled_check):
        assert "strict-transport-security:" in check
        assert "max-age=63072000" in check
        assert "includeSubDomains" in check
        assert 'count" == "1"' in check or 'count" != "1"' in check
        assert "END { print count + 0 }" in check
        assert "preload)?[[:space:]]*$" in check
        assert "/api/version" in check
        assert '"301"' in check
        assert "location:" in check
        assert "http://" in check
