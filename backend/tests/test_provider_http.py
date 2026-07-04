import httpx
import pytest

from app.services.provider_http import (
    ProviderAuthError,
    ProviderThrottled,
    provider_request,
)


@pytest.mark.asyncio
async def test_provider_request_retries_transient_5xx_with_default_timeout():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": "try again"})
        return httpx.Response(200, json={"ok": True})

    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=30,
    ) as client:
        resp = await provider_request(
            "GET",
            "https://provider.example/resource",
            client=client,
            sleep=fake_sleep,
        )

    assert resp.status_code == 200
    assert calls == 2
    assert delays == [0.5]


@pytest.mark.asyncio
async def test_provider_request_honors_retry_after_then_raises_throttled():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "2"}, text="slow down")

    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=30,
    ) as client:
        with pytest.raises(ProviderThrottled) as excinfo:
            await provider_request(
                "GET",
                "https://provider.example/resource",
                client=client,
                max_retries=1,
                sleep=fake_sleep,
            )

    assert delays == [2.0]
    assert excinfo.value.status_code == 429
    assert excinfo.value.retry_after == 2.0


@pytest.mark.asyncio
async def test_provider_request_maps_401_to_auth_error():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(401)),
        timeout=30,
    ) as client:
        with pytest.raises(ProviderAuthError) as excinfo:
            await provider_request(
                "GET",
                "https://provider.example/resource",
                client=client,
            )

    assert excinfo.value.status_code == 401
