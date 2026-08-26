import json
import uuid
from types import SimpleNamespace

import httpx
import pytest

from app.services import ai_request_broker as broker_module
from app.services.ai_request_broker import (
    AIDataClass,
    AIRequest,
    AIRequestBroker,
    AIRequestDenied,
    AITransport,
)
from app.services.llm_routing import LLMRoute, RouteTier
from app.services.background_ai_quota import BackgroundReservation


SCHEMA = {
    "type": "object",
    "properties": {"brief": {"type": "string"}},
    "required": ["brief"],
    "additionalProperties": False,
}


def _request(**overrides):
    values = {
        "tenant_id": uuid.uuid4(),
        "surface": "after_call_prepare",
        "data_class": AIDataClass.PROSPECT_CONFIDENTIAL,
        "messages": [{"role": "user", "content": "Saved note"}],
        "system_prompt": "Return the bounded preparation object.",
        "schema_name": "after_call_preparation",
        "schema": SCHEMA,
        "idempotency_key": "lead:1:note:v1",
        "route_tier": RouteTier.PREMIUM,
    }
    values.update(overrides)
    return AIRequest(**values)


@pytest.mark.asyncio
async def test_broker_fails_closed_when_surface_is_disabled(monkeypatch):
    monkeypatch.setattr(broker_module.settings, "VIRTUAL_ASSISTANT_ENABLED", False)
    broker = AIRequestBroker(llm_service=object())

    with pytest.raises(AIRequestDenied, match="disabled"):
        await broker.execute(object(), _request())


@pytest.mark.asyncio
async def test_chat_transport_is_single_shot_and_schema_validated(monkeypatch):
    captured = {}

    class _LLM:
        async def complete(self, **kwargs):
            captured.update(kwargs)
            return json.dumps({"brief": "Attorney-ready handoff"}), 11, 7

    async def fake_route(*_args, **_kwargs):
        return LLMRoute(
            requested_route="premium",
            resolved_route="premium",
            gateway_alias="premium-test",
        )

    async def allow_context(*_args, **_kwargs):
        return True

    monkeypatch.setattr(broker_module.settings, "VIRTUAL_ASSISTANT_ENABLED", True)
    monkeypatch.setattr(broker_module.settings, "AFTER_CALL_CONCIERGE_ENABLED", True)
    monkeypatch.setattr(broker_module, "resolve_llm_route", fake_route)
    monkeypatch.setattr(broker_module, "route_matter_context_allowed", allow_context)

    result = await AIRequestBroker(llm_service=_LLM()).execute(object(), _request())

    assert result.value == {"brief": "Attorney-ready handoff"}
    assert result.tokens_in == 11
    assert result.tokens_out == 7
    assert result.transport is AITransport.CHAT_COMPLETIONS
    assert captured["model"] == "premium-test"
    assert captured["disable_retries"] is True
    assert captured["max_output_tokens"] == 900
    assert captured["temperature"] == 0.0


@pytest.mark.asyncio
async def test_responses_transport_uses_global_background_alias(monkeypatch):
    seen = {}

    class _DB:
        async def scalar(self, _query):
            return SimpleNamespace(custom_config={"background_assistant_enabled": True})

    class _Quota:
        async def reserve(self, **kwargs):
            seen["reservation"] = kwargs
            return BackgroundReservation(
                id=uuid.uuid4(),
                tenant_id=kwargs["tenant_id"],
                request_id=kwargs["request_id"],
                pool="test",
            )

        async def settle(self, reservation, **kwargs):
            seen["settled"] = {"reservation": reservation, **kwargs}

        async def mark_unknown(self, *_args, **_kwargs):
            raise AssertionError("successful request must not be marked unknown")

        async def release(self, *_args, **_kwargs):
            raise AssertionError("successful request must not be released")

    async def fake_route(*_args, **_kwargs):
        return LLMRoute(
            requested_route="background",
            resolved_route="background",
            gateway_alias="clarity-background-r2",
        )

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        seen["idempotency"] = request.headers.get("Idempotency-Key")
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "model": "luna-test",
                "output_text": json.dumps({"brief": "Follow-up draft"}),
                "usage": {"input_tokens": 8, "output_tokens": 5},
            },
        )

    monkeypatch.setattr(broker_module.settings, "VIRTUAL_ASSISTANT_ENABLED", True)
    monkeypatch.setattr(broker_module.settings, "BACKGROUND_ASSISTANT_ENABLED", True)
    monkeypatch.setattr(
        broker_module.settings, "BACKGROUND_PROSPECT_CONFIDENTIAL_ENABLED", True
    )
    monkeypatch.setattr(
        broker_module.settings, "LITELLM_BACKGROUND_TRANSPORT", "responses"
    )
    monkeypatch.setattr(broker_module.settings, "LITELLM_BASE_URL", "http://gateway")
    monkeypatch.setattr(broker_module, "resolve_llm_route", fake_route)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await AIRequestBroker(
            http_client=client, quota_ledger=_Quota()
        ).execute(
            _DB(),
            _request(
                surface="background_prospect_follow_up",
                route_tier=RouteTier.BACKGROUND,
                metadata={"route_tier": "premium", "tenant_id": "spoofed"},
            ),
        )
    finally:
        await client.aclose()

    assert seen["path"] == "/v1/responses"
    assert seen["body"]["model"] == "clarity-background-r2"
    assert seen["body"]["text"]["format"]["schema"] == SCHEMA
    assert seen["body"]["litellm_metadata"]["route_tier"] == "background"
    assert seen["body"]["litellm_metadata"]["tenant_id"] != "spoofed"
    assert seen["idempotency"] == result.request_id
    assert result.provider_request_id == "resp_test"
    assert result.value == {"brief": "Follow-up draft"}
    assert (result.tokens_in, result.tokens_out) == (8, 5)
    assert seen["reservation"]["route_alias"] == "clarity-background-r2"
    assert len(seen["reservation"]["idempotency_key"]) == 64
    assert seen["reservation"]["idempotency_key"] != "lead:1:note:v1"
    assert seen["settled"]["provider_request_id"] == "resp_test"
