"""Unit tests for auth session hardening (Workstream C).

These cover the pure token/cookie/refresh helpers without a database:
  * access-token shape + lifetime
  * cookie security-flag derivation
  * refresh-token persistence, single-use consumption, and family revocation

The full ``/auth/refresh`` endpoint additionally does a DB user lookup, which
needs Postgres (unavailable here); its Redis-rotation invariants are covered at
the helper level below.
"""

import types
import uuid

import pytest
from fastapi import HTTPException
from jose import jwt

from app.config import get_settings
from app.routers import auth as auth_mod
from app.schemas.auth import TokenResponse

settings = get_settings()


def test_cookie_auth_response_never_serializes_access_token():
    response = TokenResponse(
        user_id=str(uuid.uuid4()),
        tenant_id=str(uuid.uuid4()),
        role="attorney",
        email="a@firm.com",
    )
    assert "access_token" not in response.model_dump()


# ── Fakes ───────────────────────────────────────────────────────────────────


class FakeRedis:
    """Minimal in-memory async stand-in for the bits the auth helpers use."""

    def __init__(self):
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set] = {}
        self.ttls: dict[str, int] = {}

    async def setex(self, key, ttl, value):
        self.kv[key] = value
        self.ttls[key] = int(ttl)

    async def get(self, key):
        return self.kv.get(key)

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            removed += 1 if (key in self.kv or key in self.sets) else 0
            self.kv.pop(key, None)
            self.sets.pop(key, None)
            self.ttls.pop(key, None)
        return removed

    async def sadd(self, key, value):
        self.sets.setdefault(key, set()).add(value)

    async def srem(self, key, value):
        self.sets.get(key, set()).discard(value)

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def expire(self, key, ttl):
        self.ttls[key] = int(ttl)
        return True

    async def eval(self, script, numkeys, *args):
        keys = args[:numkeys]
        argv = args[numkeys:]
        if script == auth_mod._ISSUE_REFRESH_SCRIPT:
            live_key, family_key, revoked_key = keys
            ttl, payload, token = argv
            if revoked_key in self.kv:
                return 0
            await self.setex(live_key, ttl, payload)
            await self.sadd(family_key, token)
            await self.expire(family_key, ttl)
            return 1
        if script == auth_mod._CONSUME_REFRESH_SCRIPT:
            live_key, used_key = keys
            raw = self.kv.get(live_key)
            if raw is not None:
                ttl = self.ttls.get(live_key, int(argv[0]))
                family = auth_mod._json.loads(raw).get("family")
                await self.delete(live_key)
                if family:
                    await self.setex(used_key, ttl, family)
                return ["consumed", raw]
            family = self.kv.get(used_key)
            return ["replay", family] if family else ["missing", ""]
        if script == auth_mod._REVOKE_REFRESH_FAMILY_SCRIPT:
            family_key, revoked_key = keys
            ttl, live_prefix = argv
            await self.setex(revoked_key, ttl, "1")
            members = await self.smembers(family_key)
            for token in members:
                await self.delete(f"{live_prefix}{token}")
            await self.delete(family_key)
            return len(members)
        raise AssertionError("Unexpected Redis script")


def _fake_request(redis):
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(redis=redis))
    )


def _fake_user():
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        role="attorney",
        email="a@firm.com",
        is_active=True,
    )


# ── Access token ──────────────────────────────────────────────────────────────


def test_access_token_has_jti_and_expected_lifetime():
    user = _fake_user()
    tenant = types.SimpleNamespace(billing_tier="payg")
    token = auth_mod._create_access_token(user, tenant)
    claims = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    assert claims["sub"] == str(user.id)
    assert claims["jti"]
    lifetime = claims["exp"] - claims["iat"]
    assert lifetime == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60


# ── Cookie flags ────────────────────────────────────────────────────────────────


def test_cookie_flags_derive_secure_from_backend_url(monkeypatch):
    monkeypatch.setattr(settings, "COOKIE_SECURE", None, raising=False)
    monkeypatch.setattr(
        settings, "BACKEND_URL", "https://app.example.com", raising=False
    )
    monkeypatch.setattr(settings, "COOKIE_SAMESITE", "lax", raising=False)
    flags = auth_mod._cookie_flags()
    assert flags["secure"] is True
    assert flags["samesite"] == "Lax"


def test_cookie_flags_http_backend_is_insecure(monkeypatch):
    monkeypatch.setattr(settings, "COOKIE_SECURE", None, raising=False)
    monkeypatch.setattr(settings, "BACKEND_URL", "http://localhost:8000", raising=False)
    monkeypatch.setattr(settings, "COOKIE_SAMESITE", "lax", raising=False)
    assert auth_mod._cookie_flags()["secure"] is False


def test_cookie_flags_samesite_none_forces_secure(monkeypatch):
    monkeypatch.setattr(settings, "COOKIE_SECURE", False, raising=False)
    monkeypatch.setattr(settings, "BACKEND_URL", "http://localhost:8000", raising=False)
    monkeypatch.setattr(settings, "COOKIE_SAMESITE", "none", raising=False)
    flags = auth_mod._cookie_flags()
    assert flags["samesite"] == "None"
    assert flags["secure"] is True  # SameSite=None is only valid with Secure


# ── Refresh tokens ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_token_persisted_and_tracked_by_family():
    redis = FakeRedis()
    request = _fake_request(redis)
    user = _fake_user()
    token = await auth_mod._create_refresh_token(request, user)
    assert auth_mod._refresh_key(token) in redis.kv
    # exactly one family set exists and contains the token
    family_keys = [k for k in redis.sets if k.startswith("refresh_family:")]
    assert len(family_keys) == 1
    assert token in redis.sets[family_keys[0]]


@pytest.mark.asyncio
async def test_refresh_token_single_use_consumption():
    redis = FakeRedis()
    request = _fake_request(redis)
    token = await auth_mod._create_refresh_token(request, _fake_user())
    live_payload = auth_mod._json.loads(redis.kv[auth_mod._refresh_key(token)])

    status, raw = await auth_mod._consume_refresh_token(request, token)
    assert status == "consumed"
    assert auth_mod._json.loads(raw) == live_payload
    assert auth_mod._refresh_key(token) not in redis.kv
    assert redis.kv[auth_mod._refresh_used_key(token)] == live_payload["family"]
    assert redis.ttls[auth_mod._refresh_used_key(token)] == auth_mod._REFRESH_TTL

    status, family = await auth_mod._consume_refresh_token(request, token)
    assert status == "replay"
    assert family == live_payload["family"]


@pytest.mark.asyncio
async def test_revoke_family_nukes_whole_chain():
    redis = FakeRedis()
    request = _fake_request(redis)
    user = _fake_user()
    # Two tokens rotated within the same family.
    t1 = await auth_mod._create_refresh_token(request, user)
    family = auth_mod._json.loads(redis.kv[auth_mod._refresh_key(t1)])["family"]
    t2 = await auth_mod._create_refresh_token(request, user, family=family)
    assert auth_mod._refresh_key(t2) in redis.kv

    await auth_mod._revoke_refresh_family(request, family)
    assert auth_mod._refresh_key(t1) not in redis.kv
    assert auth_mod._refresh_key(t2) not in redis.kv
    assert auth_mod._refresh_family_key(family) not in redis.sets
    revoked_key = auth_mod._refresh_family_revoked_key(family)
    assert redis.kv[revoked_key] == "1"
    assert redis.ttls[revoked_key] == auth_mod._REFRESH_TTL

    with pytest.raises(HTTPException) as exc_info:
        await auth_mod._create_refresh_token(request, user, family=family)
    assert getattr(exc_info.value, "status_code", None) == 401


@pytest.mark.asyncio
async def test_replay_revokes_successor_and_tombstone_remains_bounded():
    redis = FakeRedis()
    request = _fake_request(redis)
    user = _fake_user()
    old_token = await auth_mod._create_refresh_token(request, user)
    old_payload = auth_mod._json.loads(redis.kv[auth_mod._refresh_key(old_token)])
    family = old_payload["family"]

    status, _ = await auth_mod._consume_refresh_token(request, old_token)
    assert status == "consumed"
    successor = await auth_mod._create_refresh_token(request, user, family=family)

    status, replay_family = await auth_mod._consume_refresh_token(request, old_token)
    assert status == "replay"
    await auth_mod._revoke_refresh_family(request, replay_family)

    assert auth_mod._refresh_key(successor) not in redis.kv
    tombstone = auth_mod._refresh_used_key(old_token)
    assert redis.kv[tombstone] == family
    assert 0 < redis.ttls[tombstone] <= auth_mod._REFRESH_TTL
    assert redis.ttls[auth_mod._refresh_family_revoked_key(family)] == (
        auth_mod._REFRESH_TTL
    )


@pytest.mark.asyncio
async def test_create_refresh_token_no_redis_is_not_persisted():
    request = _fake_request(None)
    token = await auth_mod._create_refresh_token(request, _fake_user())
    # Without Redis a token is returned but nothing is stored (refresh disabled).
    assert isinstance(token, str) and token
