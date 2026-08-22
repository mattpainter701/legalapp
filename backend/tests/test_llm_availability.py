import json

import httpx
import pytest

from app.services.llm_availability import probe_customer_llm_routes


def _completion(model: str) -> dict:
    return {
        "id": "chatcmpl-availability-test",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "LAWHAND_READY"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


@pytest.mark.asyncio
async def test_probe_checks_both_active_customer_aliases_with_visible_content():
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-master-key"
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json=_completion(payload["model"]))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://litellm"
    ) as client:
        result = await probe_customer_llm_routes(
            {
                "standard": "clarity-standard-ractive",
                "premium": "clarity-premium-ractive",
            },
            client=client,
            base_url="http://litellm",
            api_key="test-master-key",
        )

    assert result["ok"] is True
    assert [request["model"] for request in requests] == [
        "clarity-standard-ractive",
        "clarity-premium-ractive",
    ]
    assert all(route["visible_content"] for route in result["routes"].values())


@pytest.mark.asyncio
async def test_probe_reports_sanitized_failure_and_still_checks_premium():
    requested_models: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requested_models.append(payload["model"])
        if payload["model"] == "clarity-standard-rbroken":
            return httpx.Response(
                429,
                json={"error": {"message": "secret upstream quota details"}},
            )
        return httpx.Response(200, json=_completion(payload["model"]))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://litellm"
    ) as client:
        result = await probe_customer_llm_routes(
            {
                "standard": "clarity-standard-rbroken",
                "premium": "clarity-premium-rhealthy",
            },
            client=client,
            base_url="http://litellm",
            api_key="test-master-key",
        )

    assert result["ok"] is False
    assert requested_models == [
        "clarity-standard-rbroken",
        "clarity-premium-rhealthy",
    ]
    assert result["routes"]["standard"] == {
        "alias": "clarity-standard-rbroken",
        "ok": False,
        "status_code": 429,
        "visible_content": False,
        "latency_ms": result["routes"]["standard"]["latency_ms"],
        "error_type": "gateway_http_error",
    }
    assert "secret upstream quota details" not in json.dumps(result)
    assert result["routes"]["premium"]["ok"] is True


@pytest.mark.asyncio
async def test_probe_fails_closed_when_active_alias_is_missing():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("unexpected request")
        ),
        base_url="http://litellm",
    ) as client:
        result = await probe_customer_llm_routes(
            {"standard": "", "premium": ""},
            client=client,
            base_url="http://litellm",
            api_key="test-master-key",
        )

    assert result["ok"] is False
    assert all(
        route["error_type"] == "active_alias_missing"
        for route in result["routes"].values()
    )
