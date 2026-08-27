"""Focused tests for QBO OAuth discovery and authentication resilience."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.routers import qbo


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def request(self, _method, _url, **_kwargs):
        self.calls += 1
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.asyncio
async def test_auth_request_retries_transient_response(monkeypatch):
    request = httpx.Request("POST", "https://oauth.platform.intuit.com/token")
    client = _FakeClient(
        [
            httpx.Response(503, request=request),
            httpx.Response(200, request=request, json={"access_token": "ok"}),
        ]
    )

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(qbo.asyncio, "sleep", no_sleep)
    response = await qbo._request_with_auth_retry(
        client, "POST", str(request.url)
    )

    assert response.status_code == 200
    assert client.calls == 2


@pytest.mark.asyncio
async def test_auth_request_does_not_retry_invalid_grant():
    request = httpx.Request("POST", "https://oauth.platform.intuit.com/token")
    client = _FakeClient(
        [
            httpx.Response(
                400,
                request=request,
                json={"error": "invalid_grant"},
            )
        ]
    )

    response = await qbo._request_with_auth_retry(
        client, "POST", str(request.url)
    )

    assert response.status_code == 400
    assert client.calls == 1


@pytest.mark.asyncio
async def test_discovery_document_supplies_oauth_endpoints(monkeypatch):
    qbo._oauth_endpoints_cache = None
    request = httpx.Request("GET", qbo.QBO_DISCOVERY_URL)
    response = httpx.Response(
        200,
        request=request,
        json={
            "authorization_endpoint": "https://appcenter.intuit.com/connect/oauth2",
            "token_endpoint": "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
            "revocation_endpoint": "https://developer.api.intuit.com/v2/oauth2/tokens/revoke",
        },
    )
    client = _FakeClient([response])

    class _ClientContext:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(qbo.httpx, "AsyncClient", lambda **_kwargs: _ClientContext())

    endpoints = await qbo._get_qbo_oauth_endpoints()

    assert endpoints["token_endpoint"].endswith("/tokens/bearer")
    assert endpoints["revocation_endpoint"].endswith("/tokens/revoke")
    assert client.calls == 1


@pytest.mark.asyncio
async def test_invalid_grant_deactivates_connection(monkeypatch):
    integration = SimpleNamespace(
        encrypted_access_token="encrypted-access",
        encrypted_refresh_token="encrypted-refresh",
        is_active=True,
        token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        last_sync_status=None,
        last_sync_error=None,
    )

    class _Db:
        committed = False

        async def commit(self):
            self.committed = True

    class _ClientContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return None

    async def no_context(_db, _tenant_id):
        return None

    async def get_integration(_db, _tenant_id):
        return integration

    async def endpoints():
        return qbo._fallback_oauth_endpoints()

    async def invalid_grant(*_args, **_kwargs):
        request = httpx.Request("POST", qbo.QBO_TOKEN_URL)
        return httpx.Response(
            400,
            request=request,
            json={"error": "invalid_grant"},
        )

    db = _Db()
    monkeypatch.setattr(qbo, "set_tenant_context", no_context)
    monkeypatch.setattr(qbo, "_get_qbo_integration", get_integration)
    monkeypatch.setattr(qbo, "_get_qbo_oauth_endpoints", endpoints)
    monkeypatch.setattr(qbo, "_request_with_auth_retry", invalid_grant)
    monkeypatch.setattr(qbo, "decrypt_token", lambda _value: "plain")
    monkeypatch.setattr(qbo.httpx, "AsyncClient", lambda **_kwargs: _ClientContext())

    result = await qbo._get_fresh_qbo_token(db, "tenant")

    assert result is None
    assert integration.is_active is False
    assert integration.last_sync_status == "failed"
    assert "invalid_grant" in integration.last_sync_error
    assert db.committed is True