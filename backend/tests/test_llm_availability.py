import json

import httpx
import pytest

from app.services import llm_availability
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


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"choices": [None]},
        {"choices": [{"message": None}]},
        {"choices": [{"message": {"content": "   "}}]},
    ],
)
def test_visible_content_rejects_missing_or_blank_completion(payload):
    assert llm_availability._visible_content(payload) is False


@pytest.mark.asyncio
async def test_probe_fails_closed_when_gateway_configuration_is_missing():
    result = await probe_customer_llm_routes(
        {"standard": "clarity-standard", "premium": "clarity-premium"},
        base_url="http://litellm",
        api_key="",
    )

    assert result == {
        "ok": False,
        "routes": {
            "standard": {
                "alias": "clarity-standard",
                "ok": False,
                "error_type": "gateway_configuration_missing",
            },
            "premium": {
                "alias": "clarity-premium",
                "ok": False,
                "error_type": "gateway_configuration_missing",
            },
        },
    }


@pytest.mark.asyncio
async def test_probe_sanitizes_transport_and_empty_completion_failures():
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["model"] == "clarity-standard-rtransport":
            raise httpx.ConnectError(
                "credential-bearing upstream detail", request=request
            )
        return httpx.Response(200, content=b"not-json")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://litellm"
    ) as client:
        result = await probe_customer_llm_routes(
            {
                "standard": "clarity-standard-rtransport",
                "premium": "clarity-premium-rempty",
            },
            client=client,
            base_url="http://litellm",
            api_key="test-master-key",
        )

    assert result["ok"] is False
    assert result["routes"]["standard"]["error_type"] == "ConnectError"
    assert result["routes"]["premium"]["error_type"] == "visible_content_missing"
    assert "credential-bearing upstream detail" not in json.dumps(result)


@pytest.mark.asyncio
async def test_active_probe_uses_database_route_aliases(monkeypatch):
    captured: dict = {}
    sentinel_db = object()

    async def fake_get_platform_llm_config(db):
        assert db is sentinel_db
        return {
            "standard_model": "clarity-standard-rdb",
            "premium_model": "clarity-premium-rdb",
        }

    async def fake_probe(aliases):
        captured.update(aliases)
        return {"ok": True, "routes": {}}

    monkeypatch.setattr(
        llm_availability, "get_platform_llm_config", fake_get_platform_llm_config
    )
    monkeypatch.setattr(llm_availability, "probe_customer_llm_routes", fake_probe)

    result = await llm_availability.probe_active_customer_llm_routes(sentinel_db)

    assert result["ok"] is True
    assert captured == {
        "standard": "clarity-standard-rdb",
        "premium": "clarity-premium-rdb",
    }


class _FakeSessionContext:
    def __init__(self, *, error: Exception | None = None):
        self.error = error

    async def __aenter__(self):
        if self.error:
            raise self.error
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.mark.asyncio
async def test_cli_returns_success_and_emits_structured_result(monkeypatch, capsys):
    async def fake_probe(_db):
        return {"ok": True, "routes": {"standard": {}, "premium": {}}}

    monkeypatch.setattr(
        llm_availability, "async_session_maker", lambda: _FakeSessionContext()
    )
    monkeypatch.setattr(
        llm_availability, "probe_active_customer_llm_routes", fake_probe
    )

    assert await llm_availability._run_cli() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["check"] == "active_customer_llm_routes"
    assert output["ok"] is True


@pytest.mark.asyncio
async def test_cli_fails_closed_with_sanitized_exception(monkeypatch, capsys):
    monkeypatch.setattr(
        llm_availability,
        "async_session_maker",
        lambda: _FakeSessionContext(error=RuntimeError("database secret")),
    )

    assert await llm_availability._run_cli() == 1
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "check": "active_customer_llm_routes",
        "ok": False,
        "error_type": "RuntimeError",
    }
    assert "database secret" not in json.dumps(output)


@pytest.mark.asyncio
async def test_probe_closes_internally_owned_http_client(monkeypatch):
    class FakeOwnedClient:
        def __init__(self):
            self.closed = False

        async def post(self, _url, *, headers, json):
            assert headers["Authorization"] == "Bearer test-master-key"
            return httpx.Response(200, json=_completion(json["model"]))

        async def aclose(self):
            self.closed = True

    owned_client = FakeOwnedClient()
    monkeypatch.setattr(
        llm_availability.httpx,
        "AsyncClient",
        lambda *, timeout: owned_client,
    )

    result = await probe_customer_llm_routes(
        {
            "standard": "clarity-standard-owned",
            "premium": "clarity-premium-owned",
        },
        base_url="http://litellm",
        api_key="test-master-key",
    )

    assert result["ok"] is True
    assert owned_client.closed is True
