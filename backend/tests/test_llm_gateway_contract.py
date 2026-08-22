"""Provider-independent checks for the customer LLM gateway contract.

The normal test fixture patches ``LLMService.complete`` so API tests stay
deterministic.  This module deliberately does not use that fixture: it sends
requests through the real ``LLMService`` and OpenAI-compatible client against
an in-process transport.  That keeps CI able to detect alias/route wiring
regressions without requiring provider credentials or network access.
"""

import json

import httpx
import pytest
from openai import AsyncOpenAI

from app.services import llm as llm_module


@pytest.mark.asyncio
async def test_real_llm_service_routes_standard_and_premium_aliases_without_global_mock(
    monkeypatch,
):
    """Both customer tiers must reach the gateway with their configured alias."""

    requested_models: list[str] = []

    async def fake_gateway(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        requested_models.append(payload["model"])
        assert "metadata" not in payload
        assert payload["litellm_metadata"] == {
            "tenant_id": "ci-tenant",
            "operation_type": "chat",
        }
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-ci-contract",
                "object": "chat.completion",
                "created": 1,
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "deterministic gateway response",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 4,
                    "total_tokens": 7,
                },
            },
        )

    standard_alias = "clarity-standard"
    premium_alias = "clarity-premium"
    monkeypatch.setattr(llm_module.settings, "LITELLM_STANDARD_MODEL", standard_alias)
    monkeypatch.setattr(llm_module.settings, "LITELLM_PREMIUM_MODEL", premium_alias)

    transport = httpx.MockTransport(fake_gateway)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://gateway/v1")
    service = llm_module.LLMService()
    service.client = AsyncOpenAI(
        api_key="sk-ci-contract",
        base_url="http://gateway/v1",
        http_client=http_client,
    )

    try:
        common_kwargs = {
            "messages": [{"role": "user", "content": "health check"}],
            "tenant_name": "CI tenant",
            "context": "",
            "system_prompt_override": "Return only the health-check response.",
            "gateway_metadata": {
                "tenant_id": "ci-tenant",
                "operation_type": "chat",
                "prompt": "must never reach gateway metadata",
            },
        }
        standard = await service.complete(**common_kwargs, use_premium=False)
        premium = await service.complete(**common_kwargs, use_premium=True)
    finally:
        await service.client.close()

    assert standard == ("deterministic gateway response", 3, 4)
    assert premium == ("deterministic gateway response", 3, 4)
    assert requested_models == [standard_alias, premium_alias]
