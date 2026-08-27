from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_research_tailnet_proxy_is_ionos_only_and_localhost_backed() -> None:
    socket = (
        ROOT
        / "ops"
        / "systemd"
        / "lawhand-research-tailnet-proxy@.socket"
    ).read_text(encoding="utf-8")
    service = (
        ROOT
        / "ops"
        / "systemd"
        / "lawhand-research-tailnet-proxy@.service"
    ).read_text(encoding="utf-8")
    firewall = (
        ROOT / "ops" / "systemd" / "lawhand-research-tailnet.nft.in"
    ).read_text(encoding="utf-8")
    installer = (
        ROOT / "scripts" / "install_research_tailnet_proxy.sh"
    ).read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "IONOS_TAILSCALE_CONNECTIVITY.md").read_text(
        encoding="utf-8"
    )

    assert "ListenStream=%i:8021" in socket
    assert "127.0.0.1:8021" in service
    assert 'iifname != "tailscale0"' in firewall
    assert "ip saddr != @IONOS_TAILSCALE_IP@" in firewall
    assert "100.64.0.0/10" in installer
    assert "law-hand-research-tailnet-firewall.service" in installer
    assert "MCP_UPSTREAM_API_KEY" in runbook
    assert "different tailnet node" in runbook
