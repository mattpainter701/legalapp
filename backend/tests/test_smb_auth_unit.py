from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.middleware.smb_auth import _SMB_RATE_LIMIT_MAX, _check_smb_rate_limit


class _RedisCounter:
    def __init__(self):
        self.values = {}
        self.expirations = []

    async def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key, seconds):
        self.expirations.append((key, seconds))


@pytest.mark.asyncio
async def test_smb_auth_failure_limit_counts_atomically_and_expires_once():
    redis = _RedisCounter()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis=redis)),
        client=SimpleNamespace(host="192.0.2.10"),
    )

    for _ in range(_SMB_RATE_LIMIT_MAX):
        await _check_smb_rate_limit(request)

    with pytest.raises(HTTPException) as exc_info:
        await _check_smb_rate_limit(request)

    assert exc_info.value.status_code == 429
    assert redis.expirations == [("rate_smb_auth:192.0.2.10", 60)]


@pytest.mark.asyncio
async def test_smb_auth_rate_limiter_fails_open_when_redis_is_unavailable():
    class _UnavailableRedis:
        async def incr(self, key):
            raise ConnectionError("offline")

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis=_UnavailableRedis())),
        client=SimpleNamespace(host="192.0.2.11"),
    )

    await _check_smb_rate_limit(request)
