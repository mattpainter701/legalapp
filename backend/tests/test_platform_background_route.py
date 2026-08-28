"""Unit coverage for the platform-global Background Automations route."""

import uuid

import pytest
from pydantic import ValidationError

from app.models.llm_provider_key import LLMProviderKey
from app.models.platform import PlatformSetting
from app.routers import platform_llm as router
from app.routers.platform_assistant import BackgroundQuotaUpdate
from app.services.ai_price_card import PriceCard
from app.services import llm_routing
from app.services.background_ai_quota import BACKGROUND_ROUTE_CONFIG_KEY
from app.services.llm_routing import RouteTier, resolve_llm_route


def _key(provider_id: str) -> LLMProviderKey:
    return LLMProviderKey(
        id=uuid.uuid4(),
        name=f"{provider_id} key",
        provider_id=provider_id,
        encrypted_key="encrypted",
        key_hint="hint",
    )


def test_background_quota_contract_covers_every_published_request_window():
    quota = BackgroundQuotaUpdate(
        account_five_hour=2050,
        account_weekly=5100,
        account_monthly=10250,
        tenant_five_hour=250,
        tenant_weekly=750,
        tenant_monthly=1500,
    )
    assert quota.account_weekly == 5100
    stored = quota.to_stored_quota({"account_five_hour_micros": 500_000})
    assert stored["account_five_hour_micros"] == 500_000
    assert stored["account_weekly_micros"] == 30_000_000
    with pytest.raises(ValidationError):
        BackgroundQuotaUpdate(
            account_five_hour=2050,
            account_monthly=10250,
            tenant_five_hour=250,
            tenant_monthly=1500,
        )
    with pytest.raises(ValidationError):
        BackgroundQuotaUpdate(
            account_five_hour=2050,
            account_weekly=5100,
            account_monthly=10250,
            tenant_five_hour=250,
            tenant_weekly=750,
            tenant_monthly=1500,
            account_five_hour_usd=0.001,
        )


def test_background_alias_is_versioned_only_when_global_route_is_complete():
    config = {
        "standard": {},
        "premium": {},
        "background": {
            "provider_id": "opencode-go",
            "key_id": "key-a",
            "model": "gpt-5.6-luna",
            "alternates": [],
            "fallbacks": [],
        },
    }
    aliases = router._managed_route_aliases(config)
    assert aliases["background"].startswith("clarity-background-r")
    assert (
        router._managed_route_aliases(
            {"standard": {}, "premium": {}, "background": {}}
        ).get("background")
        is None
    )


def test_profile_route_changes_do_not_rotate_global_background_alias():
    background = {
        "provider_id": "opencode-go",
        "key_id": "key-a",
        "model": "gpt-5.6-luna",
        "alternates": [],
        "fallbacks": [],
    }
    first = router._managed_route_aliases(
        {"standard": {"model": "standard-a"}, "premium": {}, "background": background}
    )
    second = router._managed_route_aliases(
        {"standard": {"model": "standard-b"}, "premium": {}, "background": background}
    )
    assert first["background"] == second["background"]
    assert first["standard"] != second["standard"]


def test_background_primary_and_alternate_share_alias_and_keep_fallback_explicit(
    monkeypatch,
):
    primary = _key("opencode-go")
    alternate = _key("opencode-go")
    fallback = _key("opencode-go")
    monkeypatch.setattr(router, "decrypt_token", lambda value: f"plain-{value}")
    alias = "clarity-background-rtest"

    models, fallbacks, errors = router._build_litellm_reload_payload(
        {
            "standard": {},
            "premium": {},
            "background": {
                "key_id": str(primary.id),
                "provider_id": primary.provider_id,
                "model": "gpt-5.6-luna",
                "alternates": [
                    {
                        "key_id": str(alternate.id),
                        "provider_id": alternate.provider_id,
                        "model": "gpt-5.6-luna",
                    }
                ],
                "fallbacks": [
                    {
                        "key_id": str(fallback.id),
                        "provider_id": fallback.provider_id,
                        "model": "deepseek-v4-flash",
                    }
                ],
            },
        },
        {str(item.id): item for item in (primary, alternate, fallback)},
        {"background": alias},
    )

    assert [model["model_name"] for model in models] == [alias, alias, f"{alias}-fb-0"]
    assert fallbacks == [{alias: [f"{alias}-fb-0"]}]
    assert all(model["model_name"] != "clarity-premium" for model in models)
    assert errors == []


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_background_activation_prices_primary_alternates_and_fallbacks(
    monkeypatch,
):
    card = PriceCard(
        version="test",
        rates={
            "provider/primary": {"input": 1, "output": 2},
            "provider/alternate": {"input": 1, "output": 2},
        },
    )
    monkeypatch.setattr(router, "get_price_card", lambda _db: _async_value(card))
    config = {
        "background": {
            "provider_id": "provider",
            "model": "primary",
            "alternates": [{"provider_id": "provider", "model": "alternate"}],
            "fallbacks": [{"provider_id": "provider", "model": "unpriced"}],
        }
    }

    with pytest.raises(router.HTTPException) as raised:
        await router._require_background_route_prices(object(), config, required=True)

    assert raised.value.status_code == 409
    assert "provider/unpriced" in raised.value.detail


@pytest.mark.asyncio
async def test_background_price_guard_ignores_policy_only_unset_route(monkeypatch):
    async def unexpected_price_card(_db):
        raise AssertionError("an unset Background route does not need pricing")

    monkeypatch.setattr(router, "get_price_card", unexpected_price_card)
    config = {"background": {"allow_matter_context": False}}

    await router._require_background_route_prices(object(), config)

    with pytest.raises(router.HTTPException) as raised:
        await router._require_background_route_prices(object(), config, required=True)

    assert raised.value.status_code == 409
    assert "no executable target" in raised.value.detail


def test_route_audit_payload_contains_background_without_secrets():
    payload = router._route_audit_payload(
        {
            "standard": {},
            "premium": {},
            "background": {
                "provider_id": "opencode-go",
                "key_id": "secret-key-id",
                "model": "gpt-5.6-luna",
                "allow_matter_context": False,
            },
        },
        {"litellm_updated": True},
    )
    assert payload["background"]["model"] == "gpt-5.6-luna"
    assert payload["background"]["allow_matter_context"] is False
    assert "api_key" not in str(payload)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _DB:
    def __init__(self, background_row):
        self.background_row = background_row

    async def execute(self, statement):
        key = statement.compile().params.get("key_1")
        if key == llm_routing.LLM_ROUTING_KEY:
            return _ScalarResult(None)
        return _ScalarResult(self.background_row)

    async def scalar(self, statement):
        return self.background_row


@pytest.mark.asyncio
async def test_background_resolution_reads_global_alias_and_ignores_tenant(monkeypatch):
    llm_routing.invalidate_llm_route_cache()
    row = PlatformSetting(
        key=BACKGROUND_ROUTE_CONFIG_KEY,
        value={
            "model": "clarity-background-r9",
            "activation": {"aliases": {"background": "clarity-background-r9"}},
            "quota": {"account_monthly": 17},
        },
    )
    route = await resolve_llm_route(
        _DB(row),
        uuid.uuid4(),
        route_tier=RouteTier.BACKGROUND,
        requested_provider="customer-byoK",
        requested_model="tenant-model",
    )
    assert route.gateway_alias == "clarity-background-r9"
    assert route.customer_api_key is None
    assert route.resolved_route == "background"


@pytest.mark.asyncio
async def test_background_matter_context_is_always_denied():
    async def fail_if_profile_is_read(*_args, **_kwargs):
        raise AssertionError("background policy must not inspect tenant profiles")

    original = llm_routing.get_tenant_routing_profile
    llm_routing.get_tenant_routing_profile = fail_if_profile_is_read
    try:
        allowed = await llm_routing.route_matter_context_allowed(
            object(),
            uuid.uuid4(),
            use_premium=True,
            route_tier=RouteTier.BACKGROUND,
        )
    finally:
        llm_routing.get_tenant_routing_profile = original
    assert allowed is False


@pytest.mark.asyncio
async def test_background_setting_update_preserves_quota_and_forces_public_policy():
    row = PlatformSetting(
        key=BACKGROUND_ROUTE_CONFIG_KEY,
        value={"quota": {"account_monthly": 17}, "operator_note": "keep"},
    )

    class _DB:
        def __init__(self):
            self.added = []
            self.flushed = False

        async def execute(self, _statement):
            return _ScalarResult(row)

        def add(self, value):
            self.added.append(value)

        async def flush(self):
            self.flushed = True

    db = _DB()
    await router._save_background_route_config(
        db,
        {
            "key_id": "key-a",
            "provider_id": "opencode-go",
            "model": "gpt-5.6-luna",
            "allow_matter_context": True,
        },
        {"status": "active", "aliases": {"background": "clarity-background-r3"}},
        "clarity-background-r3",
    )

    assert row.value["quota"] == {"account_monthly": 17}
    assert row.value["operator_note"] == "keep"
    assert row.value["model"] == "clarity-background-r3"
    assert row.value["route"]["allow_matter_context"] is False
    assert db.flushed is True


@pytest.mark.asyncio
async def test_background_probe_uses_responses_transport(monkeypatch):
    class _Response:
        status_code = 200

        def json(self):
            return {"output_text": "OK", "model": "gpt-5.6-luna"}

    class _Client:
        def __init__(self):
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, *, headers, json):
            self.calls.append((url, headers, json))
            return _Response()

    client = _Client()
    monkeypatch.setattr(router.settings, "LITELLM_BASE_URL", "http://gateway")
    monkeypatch.setattr(router.settings, "LITELLM_API_KEY", "master-key")
    monkeypatch.setattr(router.settings, "LITELLM_BACKGROUND_TRANSPORT", "responses")
    monkeypatch.setattr(router.httpx, "AsyncClient", lambda timeout: client)

    valid, results, error = await router._probe_litellm_aliases(
        {"background": "clarity-background-r3"}
    )

    assert valid is True
    assert error is None
    assert results["background"]["ok"] is True
    assert client.calls[0][0] == "http://gateway/v1/responses"
    assert client.calls[0][2]["model"] == "clarity-background-r3"
    assert (
        client.calls[0][2]["max_output_tokens"]
        == router.ROUTE_ACTIVATION_CANARY_MAX_TOKENS
    )
