from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import mcp as mcp_router
from app.middleware.platform_request_paths import is_platform_protected_path


def _request(headers=None):
    return SimpleNamespace(headers=headers or {}, state=SimpleNamespace())


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
    assert captured["headers"] == {"X-Operator-Identity": "signed-operator"}
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
