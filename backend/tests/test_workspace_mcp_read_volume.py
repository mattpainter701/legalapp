import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from redis.exceptions import RedisError
from starlette.requests import Request

from app.services import workspace_mcp_read_volume as volume
from app.services.automation_capabilities import CapabilityError


def request(redis=None):
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "app": SimpleNamespace(state=SimpleNamespace(redis=redis)),
        }
    )


def identity():
    return SimpleNamespace(
        tenant_id=uuid.uuid4(), grant_id=uuid.uuid4(), origin_channel="workspace_mcp"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [(0, None), (1, 429), (2, 429), (5, 503)])
async def test_shared_volume_and_retry(monkeypatch, status, expected):
    redis = SimpleNamespace(eval=AsyncMock(return_value=status))
    actor = identity()
    monkeypatch.setattr(volume, "_minute_window", lambda: (12, 17))
    if expected:
        with pytest.raises(HTTPException) as caught:
            await volume.enforce_read_volume(request(redis), actor, 512)
        assert caught.value.status_code == expected
        if expected == 429:
            assert caught.value.headers["Retry-After"] == "17"
    else:
        await volume.enforce_read_volume(request(redis), actor, 512)
    args = redis.eval.call_args.args
    assert str(actor.grant_id) in args[2]
    assert str(actor.tenant_id) in args[3]
    assert args[4] == 512 and args[-1] == 17


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "redis", [None, SimpleNamespace(eval=AsyncMock(side_effect=RedisError()))]
)
async def test_unavailable_fails_closed(monkeypatch, redis):
    monkeypatch.setattr(volume.settings, "DEV_MODE", False)
    with pytest.raises(HTTPException) as caught:
        await volume.enforce_read_volume(request(redis), identity(), 12)
    assert caught.value.status_code == 503


@pytest.mark.asyncio
async def test_database_free_development_only(monkeypatch):
    monkeypatch.setattr(volume.settings, "DEV_MODE", True)
    await volume.enforce_read_volume(request(), identity(), 12)
    with pytest.raises(HTTPException):
        await volume.enforce_read_volume(
            request(SimpleNamespace(eval=AsyncMock(side_effect=RedisError()))),
            identity(),
            12,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [-1, True, "1", None])
async def test_invalid_byte_counts(value):
    with pytest.raises(ValueError):
        await volume.enforce_read_volume(request(), identity(), value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [0, 1, 2, 99])
async def test_runtime_uses_same_budget(monkeypatch, status):
    redis = AsyncMock()
    redis.__aenter__.return_value = redis
    redis.eval.return_value = status
    monkeypatch.setattr(volume.Redis, "from_url", lambda *a, **kw: redis)
    actor = identity()
    if status:
        with pytest.raises(CapabilityError) as caught:
            await volume.enforce_runtime_read_volume(actor, 10)
        assert caught.value.code == (
            "workflow_budget_unavailable" if status == 99 else "workflow_rate_limited"
        )
    else:
        await volume.enforce_runtime_read_volume(actor, 10)
    redis.eval.assert_awaited_once()
    redis.eval.reset_mock()
    actor.origin_channel = "matter_chat"
    await volume.enforce_runtime_read_volume(actor, 10)
    redis.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_atomic_volume_enforces_both_limits_without_debiting_refusals(
    monkeypatch, test_redis
):
    monkeypatch.setattr(
        volume.settings, "WORKSPACE_MCP_GRANT_READ_BYTES_PER_MINUTE", 30
    )
    monkeypatch.setattr(
        volume.settings, "WORKSPACE_MCP_TENANT_READ_BYTES_PER_MINUTE", 50
    )
    monkeypatch.setattr(volume, "_minute_window", lambda: (123, 30))
    actor = identity()
    results = await asyncio.gather(
        *(
            volume.enforce_read_volume(request(test_redis), actor, 10)
            for _ in range(20)
        ),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 3
    other = identity()
    other.tenant_id = actor.tenant_id
    await volume.enforce_read_volume(request(test_redis), other, 20)
    with pytest.raises(HTTPException, match="tenant"):
        await volume.enforce_read_volume(request(test_redis), other, 1)
    key = f"volume:mcp:workspace:tenant:{actor.tenant_id}:123"
    assert int(await test_redis.get(key)) == 50
    assert 0 < await test_redis.ttl(key) <= 30
    await volume.enforce_read_volume(request(test_redis), identity(), 10)
