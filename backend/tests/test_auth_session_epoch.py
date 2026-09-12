"""Ending a session, end to end.

The unit tests in ``test_session_policy.py`` cover the decision; these cover the
wiring — that a password reset actually stamps the epoch, that rotation and
request authorisation both consult it, and that signing out everywhere does not
sign the caller out of the device they asked from.
"""

import time as _time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from app.config import get_settings
from app.main import app
from app.routers import auth
from app.services import session_policy

settings = get_settings()

STRONG_PASSWORD = "correct-horse-battery-staple-92"


def _access_token(user, tenant, *, issued_at: datetime) -> str:
    """An access token minted at a chosen moment, as a real login would mint it."""
    return jwt.encode(
        {
            "sub": str(user.id),
            "tenant_id": str(tenant.id),
            "role": user.role,
            "email": user.email,
            "billing_tier": tenant.billing_tier,
            "iat": int(issued_at.timestamp()),
            "jti": str(uuid.uuid4()),
            "exp": issued_at + timedelta(hours=1),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


async def _seed_chain(request, user, *, age_seconds: float = 2.0) -> str:
    """A live rotation chain whose origin is ``age_seconds`` in the past.

    The default backdates by whole seconds so a chain meant to predate an epoch
    unambiguously does: the epoch is truncated to a whole second, so a chain
    born in that same second is deliberately left alone (see session_policy).
    """
    return await auth._create_refresh_token(
        request, user, family_issued_at=_time.time() - age_seconds
    )


# ── Password reset ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_password_reset_ends_every_session_the_user_holds(
    client, db_session, test_user, test_redis
):
    """The reason this exists: a reset is what people do when compromised.

    Before the epoch, a reset changed the hash and nothing else — an attacker's
    rotating chain kept renewing itself for its full idle window.
    """
    from types import SimpleNamespace

    request = SimpleNamespace(app=app)
    stolen = await _seed_chain(request, test_user)

    token = "reset-token-" + uuid.uuid4().hex
    await test_redis.setex(auth._reset_key(token), 600, test_user.email)

    reset = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": STRONG_PASSWORD},
    )
    assert reset.status_code == 200

    # The endpoint commits through this same session, so the fixture object is
    # the stamped one; re-selecting (or expiring) it here would only risk the
    # sync-refresh footgun in AGENTS.md section 5.
    assert test_user.sessions_valid_after is not None

    # The attacker's chain can no longer be rotated.
    client.cookies.delete("refresh_token")
    replay = await client.post("/api/auth/refresh", json={"refresh_token": stolen})
    assert replay.status_code == 401
    assert replay.json()["detail"] == "Session ended; sign in again"


@pytest.mark.asyncio
async def test_access_token_minted_before_a_reset_stops_authorising_requests(
    client, db_session, test_user, test_tenant
):
    """Revocation must not wait for the access token's own 30-minute expiry."""
    stale = _access_token(
        test_user,
        test_tenant,
        issued_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )

    ok = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {stale}"})
    assert ok.status_code == 200

    test_user.sessions_valid_after = session_policy.session_epoch_now()
    await db_session.commit()

    refused = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {stale}"}
    )
    assert refused.status_code == 401
    assert refused.json()["detail"] == "Session ended; sign in again"


# ── The absolute bound ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chain_past_its_absolute_lifetime_is_refused_and_revoked(
    client, test_user, test_redis
):
    """A chain used every day still has to end eventually."""
    from types import SimpleNamespace

    request = SimpleNamespace(app=app)
    aged = await _seed_chain(
        request, test_user, age_seconds=session_policy.absolute_ttl_seconds() + 60
    )
    family = auth._json.loads(await test_redis.get(auth._refresh_key(aged)))["family"]

    client.cookies.delete("refresh_token")
    refused = await client.post("/api/auth/refresh", json={"refresh_token": aged})

    assert refused.status_code == 401
    assert refused.json()["detail"] == "Session expired; sign in again"
    # Refused *and* revoked: a successor on another device must not survive.
    assert await test_redis.exists(auth._refresh_family_revoked_key(family))


@pytest.mark.asyncio
async def test_chain_within_its_absolute_lifetime_still_rotates(
    client, test_user, test_redis
):
    from types import SimpleNamespace

    request = SimpleNamespace(app=app)
    young = await _seed_chain(
        request, test_user, age_seconds=session_policy.absolute_ttl_seconds() - 3600
    )

    client.cookies.delete("refresh_token")
    rotated = await client.post("/api/auth/refresh", json={"refresh_token": young})
    assert rotated.status_code == 200


@pytest.mark.asyncio
async def test_rotation_does_not_reset_the_absolute_clock(
    client, test_user, test_redis
):
    """Rotating a nearly-expired chain must not buy it another full window."""
    from types import SimpleNamespace

    request = SimpleNamespace(app=app)
    origin_age = session_policy.absolute_ttl_seconds() - 120
    token = await _seed_chain(request, test_user, age_seconds=origin_age)

    client.cookies.delete("refresh_token")
    rotated = await client.post("/api/auth/refresh", json={"refresh_token": token})
    assert rotated.status_code == 200

    successor = rotated.cookies.get("refresh_token")
    payload = auth._json.loads(await test_redis.get(auth._refresh_key(successor)))
    assert _time.time() - payload["family_issued_at"] >= origin_age


@pytest.mark.asyncio
async def test_chain_with_no_recorded_origin_is_refused(
    client, test_user, test_redis
):
    """Chains minted before this policy carry no origin and cannot be bounded."""
    legacy = "legacy-" + uuid.uuid4().hex
    family = str(uuid.uuid4())
    await test_redis.setex(
        auth._refresh_key(legacy),
        auth._REFRESH_TTL,
        auth._json.dumps({"user_id": str(test_user.id), "family": family}),
    )

    client.cookies.delete("refresh_token")
    refused = await client.post("/api/auth/refresh", json={"refresh_token": legacy})
    assert refused.status_code == 401
    assert refused.json()["detail"] == "Session expired; sign in again"


# ── Sign out everywhere ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_all_ends_other_sessions_but_not_the_calling_one(
    client, db_session, test_user, test_tenant, test_redis
):
    from types import SimpleNamespace

    request = SimpleNamespace(app=app)
    other_device = await _seed_chain(request, test_user)

    caller = _access_token(
        test_user, test_tenant, issued_at=datetime.now(timezone.utc)
    )
    response = await client.post(
        "/api/auth/sessions/revoke-all",
        headers={"Authorization": f"Bearer {caller}"},
    )

    assert response.status_code == 200
    assert response.json()["user_id"] == str(test_user.id)
    # The caller is re-established rather than signed out with everyone else.
    assert response.cookies.get("refresh_token")
    assert response.cookies.get("access_token")

    assert test_user.sessions_valid_after is not None

    client.cookies.delete("refresh_token")
    stale = await client.post("/api/auth/refresh", json={"refresh_token": other_device})
    assert stale.status_code == 401


@pytest.mark.asyncio
async def test_the_session_revoke_all_issues_survives_its_own_epoch(
    client, db_session, test_user, test_tenant, test_redis
):
    """The whole-second epoch exists so the new chain is not voided at birth."""
    caller = _access_token(
        test_user, test_tenant, issued_at=datetime.now(timezone.utc)
    )
    response = await client.post(
        "/api/auth/sessions/revoke-all",
        headers={"Authorization": f"Bearer {caller}"},
    )
    assert response.status_code == 200
    issued = response.cookies.get("refresh_token")

    client.cookies.delete("refresh_token")
    rotated = await client.post("/api/auth/refresh", json={"refresh_token": issued})
    assert rotated.status_code == 200


@pytest.mark.asyncio
async def test_revoke_all_requires_authentication(client):
    client.cookies.clear()
    response = await client.post(
        "/api/auth/sessions/revoke-all", headers={"Authorization": "Bearer not-a-token"}
    )
    assert response.status_code == 401
