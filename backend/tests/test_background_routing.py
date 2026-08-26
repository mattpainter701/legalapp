import uuid

import pytest

from app.services import llm_routing
from app.services.llm_routing import RouteTier, normalize_route_tier, resolve_llm_route


def test_route_tier_legacy_compatibility_and_validation():
    assert normalize_route_tier(use_premium=False) is RouteTier.STANDARD
    assert normalize_route_tier(use_premium=True) is RouteTier.PREMIUM
    assert normalize_route_tier("background", use_premium=True) is RouteTier.BACKGROUND

    with pytest.raises(ValueError, match="route_tier"):
        normalize_route_tier("customer")


@pytest.mark.asyncio
async def test_background_route_is_global_and_ignores_caller_model(monkeypatch):
    llm_routing.invalidate_llm_route_cache()
    monkeypatch.setattr(
        llm_routing.settings, "LITELLM_BACKGROUND_MODEL", "clarity-background-r7"
    )

    route = await resolve_llm_route(
        object(),
        uuid.uuid4(),
        route_tier=RouteTier.BACKGROUND,
        requested_provider="gemini",
        requested_model="caller-controlled-model",
    )

    assert route.requested_route == "background"
    assert route.resolved_route == "background"
    assert route.gateway_alias == "clarity-background-r7"
    assert route.customer_api_key is None
    assert route.customer_provider is None


@pytest.mark.asyncio
async def test_route_cache_separates_standard_and_background(monkeypatch):
    class _ScalarResult:
        def scalar_one_or_none(self):
            return None

    class _DB:
        async def execute(self, _statement):
            return _ScalarResult()

        async def scalar(self, _statement):
            return None

    llm_routing.invalidate_llm_route_cache()
    monkeypatch.setattr(llm_routing.settings, "LITELLM_STANDARD_MODEL", "standard-r1")
    monkeypatch.setattr(
        llm_routing.settings, "LITELLM_BACKGROUND_MODEL", "background-r1"
    )
    tenant_id = uuid.uuid4()

    standard = await resolve_llm_route(_DB(), tenant_id, use_premium=False)
    background = await resolve_llm_route(
        _DB(), tenant_id, route_tier=RouteTier.BACKGROUND
    )

    assert standard.gateway_alias == "standard-r1"
    assert background.gateway_alias == "background-r1"
