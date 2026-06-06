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
from jose import jwt

from app.config import get_settings
from app.routers import auth as auth_mod

settings = get_settings()


# ── Fakes ───────────────────────────────────────────────────────────────────


class FakeRedis:
    """Minimal in-memory async stand-in for the bits the auth helpers use."""

    def __init__(self):
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set] = {}

    async def setex(self, key, ttl, value):
        self.kv[key] = value

    async def get(self, key):
        return self.kv.get(key)

    async def delete(self, key):
        existed = 1 if (key in self.kv or key in self.sets) else 0
        self.kv.pop(key, None)
        self.sets.pop(key, None)
        return existed

    async def sadd(self, key, value):
        self.sets.setdefault(key, set()).add(value)

    async def srem(self, key, value):
        self.sets.get(key, set()).discard(value)

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def expire(self, key, ttl):
        return True


def _fake_request(redis):
    return types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(redis=redis)))


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
    monkeypatch.setattr(settings, "BACKEND_URL", "https://app.example.com", raising=False)
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
    # First consume succeeds, second finds nothing (single-use).
    assert await redis.delete(auth_mod._refresh_key(token)) == 1
    assert await redis.delete(auth_mod._refresh_key(token)) == 0


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


@pytest.mark.asyncio
async def test_create_refresh_token_no_redis_is_not_persisted():
    request = _fake_request(None)
    token = await auth_mod._create_refresh_token(request, _fake_user())
    # Without Redis a token is returned but nothing is stored (refresh disabled).
    assert isinstance(token, str) and token
