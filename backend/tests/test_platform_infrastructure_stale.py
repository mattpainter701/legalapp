import httpx
import pytest

from app.routers import platform_infrastructure as module


@pytest.mark.asyncio
async def test_probe_flags_stale_source_status():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "healthy",
                "checked_at": "2020-01-01T00:00:00Z",
                "writer_enabled": False,
            },
        )

    target = module.InfrastructureTarget(
        id="dr",
        label="Skynet DR",
        role="disaster-recovery",
        url="http://100.108.171.10:19090/status",
        max_age_seconds=93600,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await module._probe(client, target)
    assert result.status == "degraded"
    assert "stale" in result.detail
