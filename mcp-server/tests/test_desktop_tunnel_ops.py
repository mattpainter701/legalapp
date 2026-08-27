from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_desktop_database_tunnel_is_loopback_only_and_supervised():
    supervisor = (ROOT / "scripts" / "run-local-skynet-db-tunnel.ps1").read_text()

    assert '"127.0.0.1:${LocalPort}:127.0.0.1:${RemotePort}"' in supervisor
    assert '"ExitOnForwardFailure=yes"' in supervisor
    assert '"ServerAliveInterval=30"' in supervisor
    assert '"ServerAliveCountMax=3"' in supervisor
    assert "Get-NetTCPConnection" in supervisor
    assert "while ($true)" in supervisor
    assert "--auth" not in supervisor
