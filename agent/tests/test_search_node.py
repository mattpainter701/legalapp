from __future__ import annotations

from pathlib import Path

import pytest

from clarity_agent import config as config_module
from clarity_agent.config import AgentConfig
from clarity_agent.search_node import SearchNode

ROOT = Path(__file__).resolve().parents[2]


def test_search_node_is_default_off():
    config = AgentConfig()
    assert config.search_node_enabled is False
    with pytest.raises(ValueError, match="not enabled"):
        SearchNode.from_config(config)


def test_search_node_requires_gateway_secret_and_excludes_legacy_fts():
    config = AgentConfig(search_node_enabled=True)
    with pytest.raises(ValueError, match="gateway token"):
        SearchNode.from_config(config)
    config.search_gateway_token = "a" * 32
    config.local_index_enabled = True
    with pytest.raises(ValueError, match="cannot both serve"):
        SearchNode.from_config(config)


def test_search_node_rejects_nonlocal_opensearch():
    config = AgentConfig(
        search_node_enabled=True,
        search_gateway_token="a" * 32,
        opensearch_url="https://192.0.2.20:9200",
        opensearch_username="lawhand",
        opensearch_password="secret",
    )
    with pytest.raises(ValueError, match="loopback"):
        SearchNode.from_config(config)


def test_enabled_search_node_requires_https_and_complete_opensearch_auth():
    config = AgentConfig(
        search_node_enabled=True,
        search_gateway_token="a" * 32,
        opensearch_url="http://127.0.0.1:9200",
        opensearch_username="lawhand",
        opensearch_password="secret",
    )
    with pytest.raises(ValueError, match="HTTPS"):
        SearchNode.from_config(config)
    config.opensearch_url = "https://127.0.0.1:9200"
    config.opensearch_password = ""
    with pytest.raises(ValueError, match="authentication"):
        SearchNode.from_config(config)


def test_search_node_validates_gateway_and_control_path_before_client_creation():
    config = AgentConfig(
        search_node_enabled=True,
        search_gateway_token="short",
        opensearch_url="https://127.0.0.1:9200",
        opensearch_username="lawhand",
        opensearch_password="secret",
    )
    with pytest.raises(ValueError, match="32 bytes"):
        SearchNode.from_config(config)
    config.search_gateway_token = "a" * 32
    config.search_control_path = "relative/control.db"
    with pytest.raises(ValueError, match="absolute"):
        SearchNode.from_config(config)


def test_search_node_secrets_are_encrypted_in_saved_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr(config_module, "KEY_FILE", tmp_path / ".key")
    config = AgentConfig(
        search_gateway_token="gateway-secret-which-is-at-least-32-bytes",
        opensearch_password="opensearch-secret",
    )
    config.save_config()
    raw = (tmp_path / "config.toml").read_text()
    assert "gateway-secret" not in raw
    assert "opensearch-secret" not in raw
    loaded = AgentConfig.load()
    assert loaded.search_gateway_token == config.search_gateway_token
    assert loaded.opensearch_password == config.opensearch_password


def test_required_real_node_ci_uses_authenticated_tls():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = workflow.split("search-node-opensearch-contract:", 1)[1].split(
        "# ─── Frontend", 1
    )[0]
    assert "plugins.security.disabled" not in job
    assert "DISABLE_INSTALL_DEMO_CONFIG" not in job
    assert "LAWHAND_TEST_OPENSEARCH_URL: https://127.0.0.1:9200" in job
    assert "LAWHAND_TEST_OPENSEARCH_CA_PATH:" in job
    assert "LAWHAND_TEST_OPENSEARCH_USERNAME:" in job
    assert "LAWHAND_TEST_OPENSEARCH_PASSWORD:" in job


@pytest.mark.asyncio
async def test_search_node_close_attempts_all_resources_after_close_failure():
    closed = []

    class _Resource:
        def __init__(self, name, fail=False):
            self.name = name
            self.fail = fail

        async def close(self):
            closed.append(self.name)
            if self.fail:
                raise RuntimeError(f"{self.name} close failed")

    node = SearchNode(
        _Resource("engine"), _Resource("control"), _Resource("gateway", fail=True)
    )
    await node.close()
    assert set(closed) == {"engine", "control", "gateway"}


@pytest.mark.asyncio
async def test_search_node_start_preserves_primary_error_when_cleanup_fails():
    closed = []

    class _Control:
        async def init(self):
            pass

        async def close(self):
            closed.append("control")

    class _Engine:
        async def ensure_index(self):
            raise ValueError("primary startup error")

        async def close(self):
            closed.append("engine")

    class _Gateway:
        async def close(self):
            closed.append("gateway")
            raise RuntimeError("cleanup error")

    node = SearchNode(_Engine(), _Control(), _Gateway())
    with pytest.raises(ValueError, match="primary startup error"):
        await node.start()
    assert set(closed) == {"engine", "control", "gateway"}
