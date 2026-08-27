from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_skynet_query_tunnel_binds_only_to_docker_gateway():
    unit = (
        ROOT
        / "deploy"
        / "skynet"
        / "lawhand-query-embedding-tunnel.service.in"
    ).read_text()
    installer = (ROOT / "scripts" / "install_query_embedding_tunnel.sh").read_text()

    assert "-L @DOCKER_GATEWAY@:18031:127.0.0.1:8031" in unit
    assert "ExitOnForwardFailure=yes" in unit
    assert "ServerAliveCountMax=3" in unit
    assert "systemctl --user enable --now" in installer
