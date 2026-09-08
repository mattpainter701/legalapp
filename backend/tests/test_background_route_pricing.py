"""Regression for DeepSeek + free Zen background profile activation."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.routers import platform_llm
from app.services import ai_request_broker as broker
from app.services.ai_price_card import (
    _coerce_rates, default_price_card, get_price_card, UnknownModelPrice,
)
from app.services.background_ai_quota import background_pricing_models
from app.services.llm_routing import LLMRoute, RouteTier


def test_native_deepseek_prices_are_provider_qualified_and_peak_safe():
    card = default_price_card()
    assert card.rate_for("deepseek/deepseek-v4-flash") == {"input": 0.44, "output": 1.32, "cached_read": 0.014}
    assert card.rate_for("deepseek/deepseek-v4-pro")["output"] == 3.96
    assert card.rate_for("deepseek/deepseek-v4-flash-vision-exp")["input"] == 0.44
    assert card.estimate_max_micros(model="deepseek/deepseek-v4-flash", input_tokens=1000, max_output_tokens=100) == 572
    with pytest.raises(UnknownModelPrice):
        card.rate_for("other-provider/deepseek-v4-flash")


@pytest.mark.parametrize("model", ["big-pickle", "mimo-v2.5-free", "ling-3.0-flash-fin-free", "nemotron-3-ultra-free", "nemotron-3.5-lightning-free", "muse-spark-1.3-contributor-free"])
def test_verified_free_endpoints_hold_capacity_but_settle_zero(model):
    card = default_price_card()
    qualified = f"opencode-zen/{model}"
    assert card.estimate_max_micros(model=qualified, input_tokens=1000, max_output_tokens=900) == 1
    assert card.estimate_max_micros(model=qualified, input_tokens=1000, max_output_tokens=900, minimum_micros=0) == 0
    assert card.actual_micros(model=qualified, tokens_in=1000, tokens_out=900) == 0


@pytest.mark.parametrize("model", ["opencode-zen/unknown-free", "other/mimo-v2.5-free", "openrouter/mimo-v2.5-free"])
def test_free_name_alone_never_bypasses_unknown_price_guard(model):
    with pytest.raises(UnknownModelPrice):
        default_price_card().estimate_max_micros(model=model, input_tokens=10, max_output_tokens=10)


def test_zero_override_requires_explicit_free_attestation_and_both_rates():
    rates = _coerce_rates({
        "provider/approved-free": {"input": 0, "output": 0, "free": True},
        "provider/no-attestation": {"input": 0, "output": 0},
        "provider/partial": {"input": 0, "output": 2, "free": True},
        "provider/negative": {"input": -1, "output": 0, "free": True},
        "provider/nonfinite": {"input": "NaN", "output": 0, "free": True},
        "unqualified": {"input": 0, "output": 0, "free": True},
    })
    assert rates == {"provider/approved-free": {"input": 0.0, "output": 0.0}}


@pytest.mark.asyncio
async def test_exact_reported_background_configuration_passes_real_price_guard():
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    config = {"background": {"key_id": str(uuid4()), "provider_id": "deepseek", "model": "deepseek-v4-flash",
        "fallbacks": [{"key_id": str(uuid4()), "provider_id": "opencode-zen", "model": "mimo-v2.5-free"}]}}
    await platform_llm._require_background_route_prices(db, config, required=True)
    card = await get_price_card(db)
    value, model = card.estimate_max_for_models(models=background_pricing_models(config["background"]), input_tokens=1000, max_output_tokens=100)
    assert value == 572
    assert model == "deepseek/deepseek-v4-flash"
    config["background"]["fallbacks"].append({"provider_id": "unknown", "model": "new-free"})
    with pytest.raises(platform_llm.HTTPException, match="unknown/new-free"):
        await platform_llm._require_background_route_prices(db, config, required=True)


@pytest.mark.asyncio
async def test_operator_override_preserves_other_provider_rates():
    db = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(value={"version": "operator-1", "rates": {
        "deepseek/deepseek-v4-flash": {"input": 0.5, "output": 1.5},
        "provider/approved-free": {"input": 0, "output": 0, "free": True},
    }})))
    card = await get_price_card(db)
    assert card.version == "operator-1"
    assert card.rate_for("deepseek/deepseek-v4-flash")["input"] == 0.5
    assert card.rate_for("opencode-go/gpt-5.6-luna")["input"] == 0.2
    assert card.has_rate("opencode-zen/mimo-v2.5-free")
    assert card.rate_for("provider/approved-free")["output"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("model,expected_cost", [("mimo-v2.5-free", 0), ("deepseek-v4-flash", 11), ("unknown", None)])
async def test_gateway_usage_settles_verified_free_without_weakening_unknown_cost_handling(monkeypatch, model, expected_cost):
    route = LLMRoute(requested_route="background", resolved_route="background", gateway_alias="clarity-background-test")
    monkeypatch.setattr(broker, "resolve_llm_route", AsyncMock(return_value=route))
    monkeypatch.setattr(broker, "get_active_background_pricing_models", AsyncMock(return_value=["deepseek/deepseek-v4-flash", "opencode-zen/mimo-v2.5-free"]))
    monkeypatch.setattr(broker.settings, "LITELLM_BASE_URL", "http://gateway/v1")
    ledger = SimpleNamespace(reserve=AsyncMock(return_value=object()), settle=AsyncMock(), mark_unknown=AsyncMock(), release=AsyncMock())
    def gateway(request):
        payload = json.loads(request.content)
        assert request.url.path == "/v1/responses"
        assert payload["model"] == route.gateway_alias
        return httpx.Response(200, json={"id": "synthetic-result", "model": model, "output_text": '{"ok":true}', "usage": {"input_tokens": 10, "output_tokens": 5}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(gateway)) as client:
        service = broker.AIRequestBroker(http_client=client, quota_ledger=ledger)
        # Policy has independent coverage; exercise real pricing/admission/HTTP/settlement.
        monkeypatch.setattr(service, "_enforce_policy", AsyncMock())
        await service.execute(SimpleNamespace(scalar=AsyncMock(return_value=None)), broker.AIRequest(
            tenant_id=uuid4(), surface="background_test", data_class=broker.AIDataClass.SYNTHETIC_TEST,
            messages=[{"role": "user", "content": "Return ok"}], system_prompt="JSON only", schema_name="test",
            schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
            idempotency_key="synthetic-once", route_tier=RouteTier.BACKGROUND, transport=broker.AITransport.RESPONSES))
    assert ledger.reserve.await_args.kwargs["estimated_micros"] > 1
    if expected_cost is None:
        ledger.mark_unknown.assert_awaited_once()
        ledger.settle.assert_not_called()
    else:
        assert ledger.settle.await_args.kwargs["actual_micros"] == expected_cost
        ledger.mark_unknown.assert_not_called()
