from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from starlette.requests import Request

from app.models.workspace_mcp_audit import WorkspaceMCPAuditEvent
from app.models.workspace_mcp_client import WorkspaceMCPClient
from app.routers import platform
from tests.platform_auth_helpers import platform_headers


@pytest.mark.asyncio
async def test_platform_workspace_mcp_diagnostics_is_redacted(
    client: AsyncClient, test_tenant, monkeypatch
):
    monkeypatch.setattr(platform.settings, "WORKSPACE_MCP_ENABLED", False)
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
    assert payload["tenant_access"] == {
        "mode": "native",
        "tenant_count": 1,
        "policy": "tenant_and_user_administered",
    }
    assert str(test_tenant.id) not in str(payload)
    assert "ready" in payload["policy_checks"]
    assert "oauth" in payload and "recent_audit_events" in payload


@pytest.mark.asyncio
async def test_platform_workspace_mcp_diagnostics_reports_user_policy_and_oauth_evidence(
    client: AsyncClient, db_session, test_tenant, test_user, monkeypatch
):
    now = datetime.now(timezone.utc)
    test_user.license_active = True
    test_user.privacy_mode = False
    oauth_client = WorkspaceMCPClient(
        client_id="desktop-diagnostics-client",
        client_name="Desktop diagnostics",
        redirect_uris=["https://desktop.example.test/callback"],
        grant_types=["authorization_code"],
        response_types=["code"],
        expires_at=now + timedelta(days=1),
        last_used_at=now,
    )
    audit_event = WorkspaceMCPAuditEvent(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        client_id=oauth_client.client_id,
        event_type="token_issued",
        outcome="success",
        request_id="request-diagnostics",
        chain_position=1,
        event_hash="a" * 64,
    )
    db_session.add_all([oauth_client, audit_event])
    await db_session.commit()

    monkeypatch.setattr(platform.settings, "WORKSPACE_MCP_ENABLED", True)
    monkeypatch.setattr(
        platform.settings,
        "WORKSPACE_MCP_CANONICAL_RESOURCE",
        "https://mcp.example.test/api/mcp/workspace",
    )
    monkeypatch.setattr(
        platform.settings, "WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED", True
    )

    response = await client.get(
        "/api/platform/mcp/workspace",
        params={"email": test_user.email.upper()},
        headers=platform_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/platform/mcp/workspace",
            "headers": [
                (key.lower().encode(), value.encode())
                for key, value in platform_headers().items()
            ],
        }
    )
    direct_payload = await platform.platform_workspace_mcp_diagnostics(
        request,
        db_session,
        email=test_user.email.upper(),
    )
    assert payload["tenant_access"]["mode"] == "native"
    assert payload["tenant_access"]["tenant_count"] == 1
    assert payload["user_policy"]["found"] is True
    assert payload["user_policy"]["license_active"] is True
    assert payload["oauth"]["clients"]["total"] == 1
    assert payload["oauth"]["clients"]["active"] == 1
    assert payload["oauth"]["audit"]["outcomes"] == {
        "success": 1,
        "denied": 0,
        "error": 0,
    }
    assert payload["recent_audit_events"][0]["event_type"] == "token_issued"
    assert payload["recent_audit_events"][0]["actor_name"] == "Test Attorney"
    assert payload["audit_pagination"] == {"page_size": 5, "next_before": None}
    assert "desktop-diagnostics-client" not in str(payload)
    assert "request-diagnostics" not in str(payload)
    assert direct_payload["user_policy"] == payload["user_policy"]
    assert direct_payload["oauth"]["audit"] == payload["oauth"]["audit"]
    paged_payload = await platform.platform_workspace_mcp_diagnostics(
        request,
        db_session,
        email=test_user.email.upper(),
        audit_before=now + timedelta(seconds=1),
    )
    assert paged_payload["recent_audit_events"][0]["event_type"] == "token_issued"
