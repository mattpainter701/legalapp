from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_hsts_covers_direct_and_edge_terminated_https() -> None:
    nginx = (ROOT / "nginx" / "nginx.conf").read_text(encoding="utf-8")

    assert "map $http_x_forwarded_proto $edge_hsts_header" in nginx
    assert 'https   "max-age=63072000; includeSubDomains";' in nginx
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


def test_public_health_gates_require_hsts() -> None:
    production_check = (ROOT / "scripts" / "production_check.sh").read_text(
        encoding="utf-8"
    )
    scheduled_check = (
        ROOT / ".github" / "workflows" / "production-health.yml"
    ).read_text(encoding="utf-8")

    for check in (production_check, scheduled_check):
        assert "strict-transport-security:" in check
        assert "max-age=63072000" in check
        assert "includeSubDomains" in check
        assert 'hsts_count" == "1"' in check or 'hsts_count" != "1"' in check
        assert "END { print count + 0 }" in check
        assert "preload)?[[:space:]]*$" in check
