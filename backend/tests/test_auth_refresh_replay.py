"""Refresh-token replay revokes the live rotation family."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.main import app
from app.routers import auth


@pytest.mark.asyncio
async def test_rotated_token_replay_revokes_live_successor(
    client, test_user, test_redis
):
    request = SimpleNamespace(app=app)
    old_token = await auth._create_refresh_token(request, test_user)
    old_payload = auth._json.loads(await test_redis.get(auth._refresh_key(old_token)))
    family = old_payload["family"]

    client.cookies.set("refresh_token", old_token)
    rotated = await client.post("/api/auth/refresh")

    assert rotated.status_code == 200
    successor = rotated.cookies.get("refresh_token")
    assert successor and successor != old_token
    assert await test_redis.exists(auth._refresh_key(successor))
    tombstone_key = auth._refresh_used_key(old_token)
    tombstone_ttl = await test_redis.ttl(tombstone_key)
    assert await test_redis.get(tombstone_key) == family
    assert 0 < tombstone_ttl <= auth._REFRESH_TTL

    client.cookies.delete("refresh_token")
    replay = await client.post("/api/auth/refresh", json={"refresh_token": old_token})

    assert replay.status_code == 401
    assert replay.json()["detail"] == "Refresh token reuse detected"
    assert not await test_redis.exists(auth._refresh_key(successor))
    assert not await test_redis.exists(auth._refresh_family_key(family))
    revoked_ttl = await test_redis.ttl(auth._refresh_family_revoked_key(family))
    assert 0 < revoked_ttl <= auth._REFRESH_TTL

    with pytest.raises(HTTPException, match="Refresh token family revoked"):
        await auth._create_refresh_token(request, test_user, family=family)
