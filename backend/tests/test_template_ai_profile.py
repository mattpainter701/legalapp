from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.routers import platform_llm as api
from app.services import template_ai_service
from app.services.template_ai_profile import (
    TEMPLATE_AI_PROFILE_KEY, TEMPLATE_AI_ROUTE, TemplateAiProfile,
    TemplateAiProfileUnavailable, TemplateAiRoute, resolve_template_ai_route,
)


def database(value=None):
    return SimpleNamespace(
        scalar=AsyncMock(return_value=SimpleNamespace(value=value) if value is not None else None),
        get=AsyncMock(return_value=SimpleNamespace(provider_id="openrouter", encrypted_key="encrypted")),
        add=lambda row: None, commit=AsyncMock(),
    )


@pytest.mark.parametrize("value", [None, {}, {"settings": {"model": "cheap-model"}},
    {"settings": {"enabled": True, "key_id": str(uuid4())}, "activation": {"status": "active", "alias": "clarity-premium"}},
    {"settings": {"enabled": False}, "activation": {"status": "active"}},
])
@pytest.mark.asyncio
async def test_missing_disabled_or_stale_profile_never_falls_back(value):
    db = database(value)
    with pytest.raises(TemplateAiProfileUnavailable):
        await resolve_template_ai_route(db)
    assert TEMPLATE_AI_PROFILE_KEY in db.scalar.await_args.args[0].compile().params.values()
    db.get.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"input_usd_per_million": 0}, {"output_usd_per_million": -1},
    {"output_usd_per_million": "NaN"}, {"input_usd_per_million": "Infinity"},
    {"provider_id": "opencode-go"}, {"model": "mimo-v2.5-free"},
    {"fallbacks": [{"model": "clarity-premium"}]}, {"key_id": "secret-key"},
])
def test_profile_rejects_unpriced_and_unscoped_settings(changes):
    with pytest.raises(ValidationError):
        TemplateAiProfile(**changes)


@pytest.mark.asyncio
async def test_profile_resolution_snapshots_rates_and_has_no_customer_credentials():
    profile = TemplateAiProfile(enabled=True, key_id=uuid4(), input_usd_per_million=6)
    route = await resolve_template_ai_route(database({"settings": profile.model_dump(mode="json"),
        "activation": {"status": "active", "alias": profile.alias}}))
    assert route.model == profile.alias
    assert route.requested_route == route.resolved_route == TEMPLATE_AI_ROUTE
    assert route.provider == "litellm"
    assert route.customer_api_key is route.customer_provider is route.customer_endpoint is None
    assert route.cost(1000, 100, "payg") == Decimal("0.085")
    assert route.cost(1000, 100, "flat") == Decimal("0.0085")
    assert profile.alias != profile.model_copy(update={"input_usd_per_million": Decimal(7)}).alias


@pytest.mark.asyncio
async def test_not_configured_is_customer_safe_and_does_not_call_model(monkeypatch):
    monkeypatch.setattr(template_ai_service, "check_token_budget", AsyncMock())
    llm = SimpleNamespace(complete=AsyncMock())
    with pytest.raises(template_ai_service.TemplateAiAssistError, match="not configured"):
        await template_ai_service.assist_template_mapping(db=database(), user=object(), analysis=None,
            file_bytes=b"", consent_to_external_ai=True, llm=llm)
    llm.complete.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_activation_registers_only_opus_template_alias_and_persists_after_validation(monkeypatch, existing):
    profile = TemplateAiProfile(enabled=True, key_id=uuid4())
    old = {"settings": {"enabled": False}}
    db = database(old if existing else None)
    added = []
    db.add = added.append
    monkeypatch.setattr(api, "_require_platform_key", lambda request: None)
    monkeypatch.setattr(api, "record_operator_audit", AsyncMock())
    monkeypatch.setattr(api, "_get_model_catalog", AsyncMock(return_value={"models": [{"provider_id": "openrouter", "id": "anthropic/claude-opus-5", "confidential_data_allowed": True}]}))
    monkeypatch.setattr(api, "decrypt_token", lambda value: "synthetic-secret")
    calls = []
    async def register(models, fallbacks, *, update_router_settings):
        assert update_router_settings is False
        calls.append(models)
        assert len(models) == 1
        assert models[0]["model_name"] == profile.alias
        params = models[0]["litellm_params"]
        assert params["model"] == "openrouter/anthropic/claude-opus-5"
        assert params["api_key"] == "synthetic-secret"
        assert params["extra_body"]["provider"] == {"zdr": True, "data_collection": "deny"}
        assert fallbacks == []
        db.commit.assert_not_called()
        return True, None
    monkeypatch.setattr(api, "_call_litellm_config_update", register)
    probe = AsyncMock(return_value=(True, {}, None))
    monkeypatch.setattr(api, "_probe_litellm_aliases", probe)
    result = await api.save_template_profile(profile, object(), db)
    assert calls
    probe.assert_awaited_once_with({TEMPLATE_AI_ROUTE: profile.alias})
    assert result["activation"]["status"] == "active"
    assert "synthetic-secret" not in str(result)
    assert TEMPLATE_AI_PROFILE_KEY in db.scalar.await_args.args[0].compile().params.values()
    if existing:
        assert db.scalar.return_value.value == result
        assert not added
    else:
        assert added[0].key == TEMPLATE_AI_PROFILE_KEY
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing-key", "wrong-provider", "gateway"])
async def test_failed_activation_preserves_existing_profile(monkeypatch, failure):
    old = {"settings": {"enabled": False}}
    db = database(old)
    profile = TemplateAiProfile(enabled=True, key_id=uuid4())
    if failure == "missing-key":
        db.get.return_value = None
    elif failure == "wrong-provider":
        db.get.return_value.provider_id = "opencode-go"
    monkeypatch.setattr(api, "_require_platform_key", lambda request: None)
    monkeypatch.setattr(api, "_enforce_customer_route_data_policy", AsyncMock())
    reload = AsyncMock(return_value={"litellm_updated": False, "litellm_error": "secret-provider-detail"})
    monkeypatch.setattr(api, "_reload_litellm_routes", reload)
    with pytest.raises(HTTPException) as error:
        await api.save_template_profile(profile, object(), db)
    assert "secret-provider-detail" not in str(error.value.detail)
    assert db.scalar.return_value.value == old
    db.commit.assert_not_called()
    if failure != "gateway":
        reload.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_profile_does_not_touch_gateway_and_get_returns_defaults(monkeypatch):
    monkeypatch.setattr(api, "_require_platform_key", lambda request: None)
    monkeypatch.setattr(api, "record_operator_audit", AsyncMock())
    reload = AsyncMock()
    monkeypatch.setattr(api, "_reload_litellm_routes", reload)
    db = database()
    result = await api.get_template_profile(object(), db)
    assert result["settings"]["model"] == "anthropic/claude-opus-5"
    assert result["activation"]["status"] == "not_configured"
    disabled = await api.save_template_profile(TemplateAiProfile(), object(), db)
    assert disabled["activation"]["status"] == "disabled"
    reload.assert_not_called()
    assert await api.get_template_profile(object(), database(disabled)) == disabled


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get", "put"])
async def test_platform_auth_required_before_read_or_activation(method):
    app = FastAPI()
    app.include_router(api.router)
    db = database()
    app.dependency_overrides[api.get_db] = lambda: db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, "/platform/llm/template-profile", **({"json": {}} if method == "put" else {}))
    assert response.status_code in (401, 403)
    db.scalar.assert_not_called()
    db.get.assert_not_called()


def test_invalid_proposal_still_has_billable_template_usage():
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4(), tenant=SimpleNamespace(billing_tier="payg"))
    route = TemplateAiRoute(requested_route=TEMPLATE_AI_ROUTE, resolved_route=TEMPLATE_AI_ROUTE, gateway_alias="alias")
    row = template_ai_service._usage_record(user, route, 1000, 100, None, "request-id")
    assert row.operation_type == "template_ai_map"
    assert row.cost_usd == Decimal("0.075")
    assert row.gateway_request_id == "request-id"
    assert row.query_text is None


@pytest.mark.asyncio
async def test_unapproved_model_is_blocked_before_gateway_registration(monkeypatch):
    monkeypatch.setattr(api, "_require_platform_key", lambda request: None)
    monkeypatch.setattr(api, "record_operator_audit", AsyncMock())
    monkeypatch.setattr(api, "_get_model_catalog", AsyncMock(return_value={"models": []}))
    reload = AsyncMock()
    monkeypatch.setattr(api, "_reload_litellm_routes", reload)
    with pytest.raises(HTTPException) as error:
        await api.save_template_profile(TemplateAiProfile(enabled=True, key_id=uuid4()), object(), database())
    assert error.value.detail["code"] == "confidential_data_not_allowed"
    reload.assert_not_called()


@pytest.mark.asyncio
async def test_cannot_delete_key_used_by_template_profile(monkeypatch):
    key_id = uuid4()
    db = database({"settings": {"enabled": True, "key_id": str(key_id)}})
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(id=key_id)))
    monkeypatch.setattr(api, "_require_platform_key", lambda request: None)
    monkeypatch.setattr(api, "_get_route_config", AsyncMock(return_value={}))
    with pytest.raises(HTTPException, match="template-premium"):
        await api.delete_provider_key(str(key_id), object(), db)
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_template_deployment_never_writes_global_router_config(monkeypatch):
    import httpx
    from app.services.token_vault import encrypt_token
    profile = TemplateAiProfile(enabled=True, key_id=uuid4())
    monkeypatch.setattr(api.settings, "LITELLM_BASE_URL", "http://gateway/v1")
    monkeypatch.setattr(api.settings, "LITELLM_API_KEY", "synthetic-gateway-key")
    requests = []
    def gateway(request):
        import json
        requests.append((request.method, request.url.path))
        if request.url.path == "/v1/model/info":
            return httpx.Response(200, json={"data": [{"model_name": "clarity-background", "model_info": {"id": "background"}}]})
        if request.url.path == "/v1/model/new":
            data = json.loads(request.content)
            assert data["model_name"] == profile.alias
            assert data["litellm_params"]["model"] == "openrouter/anthropic/claude-opus-5"
            return httpx.Response(201, json={})
        pytest.fail(f"Unexpected shared configuration mutation: {request.method} {request.url.path}")
    real_client = httpx.AsyncClient
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(gateway), **kwargs))
    config = {TEMPLATE_AI_ROUTE: {"provider_id": profile.provider_id, "model": profile.model, "key_id": str(profile.key_id)}}
    key = SimpleNamespace(provider_id="openrouter", encrypted_key=encrypt_token("synthetic-provider-key"))
    result = await api._reload_litellm_routes(config, {str(profile.key_id): key},
        aliases={TEMPLATE_AI_ROUTE: profile.alias}, validate=False, update_router_settings=False)
    assert result["litellm_updated"] is True
    assert requests == [("GET", "/v1/model/info"), ("POST", "/v1/model/new")]
