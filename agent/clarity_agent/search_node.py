"""Lifecycle wrapper for the default-off production Search Node."""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlparse

from clarity_agent.config import AgentConfig
from clarity_agent.opensearch_engine import OpenSearchEngine, OpenSearchLimits
from clarity_agent.search_engine import EngineHealth
from clarity_agent.search_control import SqliteControlState
from clarity_agent.search_gateway import LocalQueryGateway


def preflight_reasons(health: EngineHealth) -> list[str]:
    """Name every reason a health report is not servable.

    Only operational signals: never a path, query, snippet, ACL token, or
    credential.
    """
    details = health.details
    reasons: list[str] = []
    if health.status == "unavailable":
        reasons.append(f"OpenSearch is unreachable ({details.get('error', 'unknown error')})")
        return reasons
    cluster_status = str(details.get("cluster_status", "unknown"))
    if cluster_status not in {"green", "yellow"}:
        reasons.append(f"cluster status is {cluster_status}")
    if details.get("timed_out"):
        reasons.append("the cluster health request timed out")
    if not health.active_index:
        reasons.append("no active read/write index")
    expected = details.get("expected_disk_watermarks") or {}
    actual = details.get("disk_watermarks") or {}
    if isinstance(expected, dict) and isinstance(actual, dict) and actual != expected:
        drift = ", ".join(
            f"{name}={actual.get(name)!r} (expected {value!r})"
            for name, value in expected.items()
            if actual.get(name) != value
        )
        reasons.append(
            f"disk watermarks do not match the packaged opensearch.yml: {drift}"
        )
    if details.get("disk_threshold_enabled") not in {True, "true"}:
        reasons.append("cluster.routing.allocation.disk.threshold_enabled is not enabled")
    if details.get("active_index_write_blocked"):
        reasons.append("the active index is write-blocked")
    if details.get("rebuild_lease_active"):
        reasons.append(
            "a rebuild lease is present; follow the rebuild quarantine recovery runbook"
        )
    return reasons or [f"engine reported {health.status}"]


class SearchNode:
    def __init__(
        self,
        engine: OpenSearchEngine,
        control: SqliteControlState,
        gateway: LocalQueryGateway,
    ):
        self.engine = engine
        self.control = control
        self.gateway = gateway

    @classmethod
    def from_config(cls, config: AgentConfig) -> SearchNode:
        if not config.search_node_enabled:
            raise ValueError("Search Node is not enabled")
        if not config.native_authz_enabled or not config.search_identity_public_key:
            raise ValueError("OpenSearch serving requires native authorization and identity verification")
        if config.local_index_enabled:
            raise ValueError(
                "legacy SQLite FTS and production Search Node cannot both serve"
            )
        if not config.search_gateway_token:
            raise ValueError("Search Node requires a local gateway token")
        if len(config.search_gateway_token.encode()) < 32:
            raise ValueError("Search Node gateway token must contain at least 32 bytes")
        if config.search_gateway_host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("Search Node gateway must bind to loopback")
        if not 0 <= int(config.search_gateway_port) <= 65535:
            raise ValueError("Search Node gateway port is invalid")
        parsed_opensearch = urlparse(config.opensearch_url)
        if parsed_opensearch.scheme != "https":
            raise ValueError("enabled Search Nodes require OpenSearch HTTPS")
        if (
            parsed_opensearch.username is not None
            or parsed_opensearch.password is not None
        ):
            raise ValueError("OpenSearch credentials must not be embedded in the URL")
        if not config.opensearch_username or not config.opensearch_password:
            raise ValueError("enabled Search Nodes require OpenSearch authentication")
        control_path = config.search_control_path or str(
            Path(config.ledger_path)
            .expanduser()
            .resolve()
            .with_name("search-control.db")
        )
        limits = OpenSearchLimits(
            max_results=max(1, min(config.search_max_results, 500)),
            max_bulk_documents=max(1, min(config.search_max_bulk_documents, 2_000)),
            max_bulk_bytes=max(1, min(config.search_max_bulk_mb, 64)) * 1024 * 1024,
        )
        control = SqliteControlState(control_path)
        engine = OpenSearchEngine(
            config.opensearch_url,
            index_prefix=config.opensearch_index_prefix,
            username=config.opensearch_username or None,
            password=config.opensearch_password or None,
            ca_path=config.opensearch_ca_path or None,
            limits=limits,
        )
        gateway = LocalQueryGateway(
            engine,
            config.search_gateway_token,
            host=config.search_gateway_host,
            port=config.search_gateway_port,
        )
        return cls(engine, control, gateway)

    async def start(self) -> None:
        try:
            await self.control.init()
            await self.engine.ensure_index()
            health = await self.engine.health()
            if health.status != "healthy":
                # A failed preflight stops the whole agent, so the operator has
                # to be able to read the cause off the service log. The most
                # common one by far is a node that never received the packaged
                # opensearch.yml and still has the stock 85% low watermark.
                raise RuntimeError(
                    "Search Node OpenSearch preflight is not healthy: "
                    + "; ".join(preflight_reasons(health))
                )
            await self.gateway.start()
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        await asyncio.gather(
            self.gateway.close(),
            self.control.close(),
            self.engine.close(),
            return_exceptions=True,
        )
