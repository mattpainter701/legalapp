"""Focused tests for QBO OAuth discovery and authentication resilience."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

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
    response = await qbo._request_with_auth_retry(client, "POST", str(request.url))

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

    response = await qbo._request_with_auth_retry(client, "POST", str(request.url))

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


@pytest.mark.asyncio
async def test_discovery_uses_cache_and_falls_back_for_untrusted_document(monkeypatch):
    cached = qbo._fallback_oauth_endpoints()
    qbo._oauth_endpoints_cache = (float("inf"), cached)
    assert await qbo._get_qbo_oauth_endpoints() == cached

    qbo._oauth_endpoints_cache = None
    request = httpx.Request("GET", qbo.QBO_DISCOVERY_URL)
    response = httpx.Response(
        200,
        request=request,
        json={
            "authorization_endpoint": "https://attacker.example/connect",
            "token_endpoint": None,
            "revocation_endpoint": "http://developer.api.intuit.com/revoke",
        },
    )
    client = _FakeClient([response])

    class _ClientContext:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(qbo.httpx, "AsyncClient", lambda **_kwargs: _ClientContext())

    assert await qbo._get_qbo_oauth_endpoints() == qbo._fallback_oauth_endpoints()
    assert qbo._trusted_intuit_endpoint(123) is None
    assert qbo._trusted_intuit_endpoint("https://notintuit.com/token") is None


@pytest.mark.asyncio
async def test_auth_request_exhausts_network_retries(monkeypatch):
    request = httpx.Request("POST", qbo.QBO_TOKEN_URL)
    client = _FakeClient(
        [
            httpx.ConnectError("offline", request=request),
            httpx.ConnectError("offline", request=request),
            httpx.ConnectError("offline", request=request),
        ]
    )

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(qbo.asyncio, "sleep", no_sleep)
    with pytest.raises(httpx.ConnectError):
        await qbo._request_with_auth_retry(client, "POST", str(request.url))
    assert client.calls == 3


@pytest.mark.asyncio
async def test_list_ar_accounts_returns_connected_company_accounts(monkeypatch):
    tenant_id = "tenant-1"

    async def current_user(_request, _db):
        return SimpleNamespace(role="admin", tenant_id=tenant_id)

    async def no_context(_db, _tenant_id):
        return None

    async def token(_db, _tenant_id):
        return "access-token"

    async def integration(_db, _tenant_id):
        return SimpleNamespace(qbo_realm_id="realm-1", sandbox_mode=False)

    request = httpx.Request(
        "GET", "https://quickbooks.api.intuit.com/v3/company/realm-1/query"
    )
    response = httpx.Response(
        200,
        request=request,
        json={
            "QueryResponse": {"Account": [{"Id": "12", "Name": "Accounts Receivable"}]}
        },
    )

    class _ClientContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            assert url == str(request.url)
            assert kwargs["headers"]["Authorization"] == "Bearer access-token"
            assert "Accounts Receivable" in kwargs["params"]["query"]
            return response

    monkeypatch.setattr(qbo, "get_current_user", current_user)
    monkeypatch.setattr(qbo, "set_tenant_context", no_context)
    monkeypatch.setattr(qbo, "_get_fresh_qbo_token", token)
    monkeypatch.setattr(qbo, "_get_qbo_integration", integration)
    monkeypatch.setattr(qbo.httpx, "AsyncClient", lambda **_kwargs: _ClientContext())

    accounts = await qbo.qbo_list_ar_accounts(object(), object())

    assert [(account.id, account.name) for account in accounts] == [
        ("12", "Accounts Receivable")
    ]


@pytest.mark.asyncio
async def test_successful_callback_returns_to_qbo_admin(monkeypatch):
    tenant_id = uuid4()
    request = httpx.Request("POST", qbo.QBO_TOKEN_URL)
    token_response = httpx.Response(
        200,
        request=request,
        json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600},
    )

    class _ClientContext:
        async def __aenter__(self):
            return _FakeClient([token_response])

        async def __aexit__(self, *_args):
            return None

    class _Db:
        def __init__(self):
            self.added = None
            self.committed = False

        def add(self, value):
            self.added = value

        async def commit(self):
            self.committed = True

    async def consume_state(_request, _state):
        return True, {"tenant_id": str(tenant_id)}

    async def no_context(_db, _tenant_id):
        return None

    async def no_existing(_db, _tenant_id):
        return None

    async def endpoints():
        return qbo._fallback_oauth_endpoints()

    monkeypatch.setattr(qbo, "_consume_state", consume_state)
    monkeypatch.setattr(qbo, "set_tenant_context", no_context)
    monkeypatch.setattr(qbo, "_get_qbo_integration", no_existing)
    monkeypatch.setattr(qbo, "_get_qbo_oauth_endpoints", endpoints)
    monkeypatch.setattr(qbo, "encrypt_token", lambda value: f"encrypted-{value}")
    monkeypatch.setattr(qbo.httpx, "AsyncClient", lambda **_kwargs: _ClientContext())
    monkeypatch.setattr(qbo.settings, "FRONTEND_URL", "https://getlawhand.com")

    db = _Db()
    response = await qbo.qbo_callback(
        code="code",
        state="state",
        realmId="realm-1",
        request=object(),
        db=db,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "https://getlawhand.com/admin?tab=qbo&qbo=connected"
    )
    assert db.committed is True
    assert db.added.qbo_realm_id == "realm-1"


def test_integration_response_serializes_database_uuids():
    now = datetime.now(timezone.utc)
    value = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        qbo_realm_id="realm-1",
        is_active=True,
        sandbox_mode=False,
        scopes="accounting",
        token_expires_at=now,
        sync_frequency_minutes=15,
        last_sync_at=None,
        last_sync_status=None,
        last_sync_error=None,
        qbo_ar_account_id="84",
        qbo_ar_account_name="Accounts Receivable",
        created_at=now,
        updated_at=now,
    )

    response = qbo.QBOIntegrationResponse.model_validate(value)

    assert response.id == value.id
    assert response.tenant_id == value.tenant_id
