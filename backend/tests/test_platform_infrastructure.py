import json

import httpx
import pytest

from app.routers import platform_infrastructure as module


def test_targets_reject_public_plain_http(monkeypatch):
    monkeypatch.setenv(
        "PLATFORM_INFRASTRUCTURE_TARGETS_JSON",
        json.dumps(
            [
                {
                    "id": "bad",
                    "label": "Bad",
                    "role": "development",
                    "url": "http://example.com/health",
                }
            ]
        ),
    )
    assert module._load_targets() == []


def test_targets_allow_https_and_tailscale(monkeypatch):
    monkeypatch.setenv(
        "PLATFORM_INFRASTRUCTURE_TARGETS_JSON",
        json.dumps(
            [
                {
                    "id": "dev1",
                    "label": "Dev 1",
                    "role": "development",
                    "url": "https://dev1.getlawhand.com/health/readiness",
                },
                {
                    "id": "dr",
                    "label": "Skynet DR",
                    "role": "disaster-recovery",
                    "url": "http://100.108.171.10:19090/status",
                },
            ]
        ),
    )
    assert [target.id for target in module._load_targets()] == ["dev1", "dr"]


@pytest.mark.asyncio
async def test_probe_flags_enabled_dr_writer():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "healthy", "writer_enabled": True})

    target = module.InfrastructureTarget(
        id="dr",
        label="Skynet DR",
        role="disaster-recovery",
        url="http://100.108.171.10:19090/status",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await module._probe(client, target)
    assert result.status == "degraded"
    assert result.writer_enabled is True


@pytest.mark.asyncio
async def test_probe_hides_transport_error_details():
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret internal detail")

    target = module.InfrastructureTarget(
        id="dev1",
        label="Dev 1",
        role="development",
        url="https://dev1.getlawhand.com/health/readiness",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await module._probe(client, target)
    assert result.status == "unavailable"
    assert "secret" not in result.detail
