import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.operator_audit import OperatorAuditLog
from app.routers import mcp as mcp_router
from app.services import platform_auth
from app.middleware.platform_request_paths import is_platform_protected_path


def _request(headers=None):
    return SimpleNamespace(
        headers=headers or {}, state=SimpleNamespace(), method="POST"
    )


def test_authority_platform_paths_use_segment_boundaries():
    assert is_platform_protected_path("/api/platform")
    assert is_platform_protected_path("/api/platform/auth/token")
    assert is_platform_protected_path("/api/mcp/authority/promote")
    assert not is_platform_protected_path("/api/platformevil")
    assert not is_platform_protected_path("/api/mcp/authorityfoo")


@pytest.mark.asyncio
async def test_authority_control_forwards_signed_principal_and_ignores_spoofed_actor(
    monkeypatch,
):
    monkeypatch.setattr(mcp_router.settings, "MCP_SERVER_URL", "http://mcp.test")
    principal = SimpleNamespace(actor_id="signed-operator", credential_id="key-1")
    captured = {}

    monkeypatch.setattr(
        mcp_router,
        "require_platform_token",
        lambda request, scopes: principal,
    )

    async def fake_proxy(path, request, payload, *, extra_headers=None):
        captured.update(path=path, payload=payload, headers=extra_headers)
        return {"ok": True}

    monkeypatch.setattr(mcp_router, "_proxy_post", fake_proxy)
    body = mcp_router.AuthorityControlRequest(version="v1", reason="promote fixture")

    result = await mcp_router._authority_control(
        "promote",
        body,
        _request({"X-Operator-Identity": "spoofed-client"}),
    )

    assert result == {"ok": True}
    assert captured["path"] == "/api/mcp/control/promote"
    assert captured["headers"]["X-Operator-Identity"] == "signed-operator"
    assert captured["headers"]["X-Operator-Assertion"]
    assert captured["payload"]["reason"] == "promote fixture"


@pytest.mark.asyncio
async def test_authority_control_denial_does_not_call_upstream(monkeypatch):
    called = False

    def deny(request, scopes):
        raise HTTPException(status_code=403, detail="Platform token scope denied")

    async def unexpected_proxy(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(mcp_router, "require_platform_token", deny)
    monkeypatch.setattr(mcp_router, "_proxy_post", unexpected_proxy)
    body = mcp_router.AuthorityControlRequest(version="v1", reason="denied")

    with pytest.raises(HTTPException) as exc:
        await mcp_router._authority_control("stage", body, _request())

    assert exc.value.status_code == 403
    assert called is False


@pytest.mark.asyncio
async def test_real_authority_route_session_and_denials_are_audited(
    client, db_session, monkeypatch
):
    raw_bootstrap = "authority-bootstrap-xxxxxxxxxxxxxxxxxxxxxxxx"
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    monkeypatch.setattr(
        platform_auth.settings,
        "PLATFORM_BOOTSTRAP_CREDENTIALS_JSON",
        json.dumps(
            [
                {
                    "operator_id": "authority-operator",
                    "key_hash": hashlib.sha256(raw_bootstrap.encode()).hexdigest(),
                    "scopes": ["platform:read", "platform:write"],
                    "expires_at": expires_at.isoformat(),
                }
            ]
        ),
    )
    monkeypatch.setattr(
        platform_auth.settings, "PLATFORM_TOKEN_SIGNING_KEY", "z" * 48
    )
    monkeypatch.setattr(mcp_router.settings, "MCP_SERVER_URL", "http://mcp.test")
    monkeypatch.setattr(
        mcp_router.settings, "MCP_OPERATOR_ASSERTION_SECRET", "s" * 48
    )
    calls = []

    async def fake_proxy(path, request, payload, *, extra_headers=None):
        calls.append((path, payload, extra_headers))
        return {"status": "accepted"}

    monkeypatch.setattr(mcp_router, "_proxy_post", fake_proxy)
    issued = await client.post(
        "/api/platform/auth/token",
        headers={"X-Platform-Key": raw_bootstrap},
        json={"scopes": ["platform:write"]},
    )
    assert issued.status_code == 200, issued.text
    session_headers = {
        "Authorization": f"Bearer {issued.json()['access_token']}",
        "X-Operator-Identity": "spoofed-client",
    }
    accepted = await client.post(
        "/api/mcp/authority/stage",
        headers=session_headers,
        json={"version": "v-test", "reason": "authorized fixture"},
    )
    assert accepted.status_code == 200, accepted.text
    assert calls[0][2]["X-Operator-Identity"] == "authority-operator"

    for headers in ({}, {"Authorization": "Bearer malformed"}):
        denied = await client.post(
            "/api/mcp/authority/stage",
            headers=headers,
            json={"version": "v-test", "reason": "denied fixture"},
        )
        assert denied.status_code == 403
    read_only = await client.post(
        "/api/mcp/authority/stage",
        headers={"Authorization": f"Bearer {platform_auth.issue_platform_token(subject='read-only', scopes=['platform:read'], allowed_scopes=['platform:read'])[0]}"},
        json={"version": "v-test", "reason": "read-only fixture"},
    )
    assert read_only.status_code == 403
    assert len(calls) == 1

    logs = (
        (
            await db_session.execute(
                select(OperatorAuditLog).where(
                    OperatorAuditLog.action == "platform.request"
                )
            )
        )
        .scalars()
        .all()
    )
    operator_logs = [row for row in logs if row.resource_id == "/api/mcp/authority/stage"]
    assert any(
        row.actor_id == "authority-operator"
        and row.metadata_json.get("scope") == "platform:write"
        and row.metadata_json.get("status_code") == 200
        for row in operator_logs
    )
    assert any(
        row.actor_id == "authority-operator"
        and row.metadata_json.get("scope") == "platform:write"
        and row.metadata_json.get("status_code") == 403
        for row in operator_logs
    )
