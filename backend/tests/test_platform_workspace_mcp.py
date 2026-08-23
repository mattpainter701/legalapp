import pytest
from httpx import AsyncClient

from app.routers import platform
from tests.platform_auth_helpers import platform_headers


@pytest.mark.asyncio
async def test_platform_workspace_mcp_diagnostics_is_redacted(
    client: AsyncClient, test_tenant, monkeypatch
):
    monkeypatch.setattr(platform.settings, "WORKSPACE_MCP_ENABLED", False)
    monkeypatch.setattr(
        platform.settings,
        "WORKSPACE_MCP_ALLOWED_TENANT_IDS",
        str(test_tenant.id),
    )
    monkeypatch.setattr(
        platform.settings,
        "WORKSPACE_MCP_CANONICAL_RESOURCE",
        "https://mcp.example.test/api/mcp/workspace",
    )
    response = await client.get(
        "/api/platform/mcp/workspace",
        headers=platform_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["policy_checks"]["feature_enabled"]["ok"] is False
    assert payload["pilot_tenants"]["active_count"] == 1
    assert payload["pilot_tenants"]["ids_masked"] == [f"…{str(test_tenant.id)[-6:]}"]
    assert str(test_tenant.id) not in str(payload)
    assert "ready_for_pilot" in payload["policy_checks"]
    assert "oauth" in payload and "recent_audit_events" in payload

