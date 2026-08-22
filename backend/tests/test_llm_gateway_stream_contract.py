"""Provider-independent streaming checks for both customer gateway aliases."""

import json

import httpx
import pytest
from openai import AsyncOpenAI

from app.services import llm as llm_module


@pytest.mark.asyncio
async def test_streaming_routes_both_aliases_and_uses_private_litellm_metadata(
    monkeypatch,
):
    requests: list[dict] = []

    async def fake_gateway(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.url.path == "/v1/chat/completions"
        assert payload["stream"] is True
        assert payload.get("metadata") is None
        assert payload["litellm_metadata"] == {
            "tenant_id": "tenant-ci",
            "matter_id": "matter-ci",
        }
        events = [
            {
                "id": "ci",
                "object": "chat.completion.chunk",
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "streamed"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "ci",
                "object": "chat.completion.chunk",
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": " response"},
                        "finish_reason": "stop",
                    }
                ],
            },
        ]
        body = (
            "".join(f"data: {json.dumps(event)}\n\n" for event in events)
            + "data: [DONE]\n\n"
        )
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body.encode()
        )

    standard_alias = "clarity-standard"
    premium_alias = "clarity-premium"
    monkeypatch.setattr(llm_module.settings, "LITELLM_STANDARD_MODEL", standard_alias)
    monkeypatch.setattr(llm_module.settings, "LITELLM_PREMIUM_MODEL", premium_alias)
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(fake_gateway), base_url="http://gateway/v1"
    )
    service = llm_module.LLMService()
    service.client = AsyncOpenAI(
        api_key="sk-ci-contract", base_url="http://gateway/v1", http_client=http_client
    )
    try:
        kwargs = {
            "messages": [{"role": "user", "content": "health check"}],
            "tenant_name": "CI tenant",
            "context": "",
            "gateway_metadata": {"tenant_id": "tenant-ci", "matter_id": "matter-ci"},
            "system_prompt_override": "Return only the health-check response.",
        }
        standard = "".join([chunk async for chunk in service.stream_complete(**kwargs)])
        premium = "".join(
            [
                chunk
                async for chunk in service.stream_complete(**kwargs, use_premium=True)
            ]
        )
    finally:
        await service.client.close()
    assert standard == "streamed response"
    assert premium == "streamed response"
    assert [payload["model"] for payload in requests] == [standard_alias, premium_alias]
