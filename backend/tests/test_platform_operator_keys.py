"""Minted operator API keys: issuance, use, and the limits on both."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.platform_api_key import PlatformApiKey
from app.services import platform_auth
from tests.platform_auth_helpers import platform_headers


# ── Pure unit coverage (no database needed) ───────────────────────────────────


def test_generated_key_is_prefixed_and_stored_only_as_a_hash():
    plaintext, prefix, key_hash = platform_auth.generate_platform_api_key()

    assert plaintext.startswith(platform_auth.PLATFORM_API_KEY_PREFIX)
    assert prefix == plaintext[: platform_auth.PLATFORM_API_KEY_DISPLAY_CHARS]
    assert key_hash == platform_auth.hash_platform_api_key(plaintext)
    # The stored material must not contain the secret it authenticates.
    assert plaintext not in key_hash
    assert len(key_hash) == 64


def test_two_mints_never_collide():
    first, _, first_hash = platform_auth.generate_platform_api_key()
    second, _, second_hash = platform_auth.generate_platform_api_key()
    assert first != second
    assert first_hash != second_hash


def test_key_scopes_cannot_exceed_the_minting_credential():
    minter = platform_auth.PlatformPrincipal(
        actor_id="ops@example.com",
        scopes=frozenset({"platform:read"}),
        credential_type="bootstrap_session",
    )
    with pytest.raises(Exception) as exc:
        platform_auth.validate_requested_key_scopes(
            ["platform:read", "platform:debug"], granted_by=minter
        )
    assert exc.value.status_code == 403


def test_unknown_scopes_are_refused():
    minter = platform_auth.PlatformPrincipal(
        actor_id="ops@example.com",
        scopes=frozenset(platform_auth.PLATFORM_SCOPES),
        credential_type="bootstrap_session",
    )
    with pytest.raises(Exception) as exc:
        platform_auth.validate_requested_key_scopes(
            ["platform:everything"], granted_by=minter
        )
    assert exc.value.status_code == 400


def test_empty_scope_list_is_refused():
    minter = platform_auth.PlatformPrincipal(
        actor_id="ops@example.com",
        scopes=frozenset(platform_auth.PLATFORM_SCOPES),
        credential_type="bootstrap_session",
    )
    with pytest.raises(Exception) as exc:
        platform_auth.validate_requested_key_scopes([], granted_by=minter)
    assert exc.value.status_code == 400


def test_revoked_and_expired_keys_are_not_usable():
    now = datetime.now(timezone.utc)

    live = PlatformApiKey(label="live", key_prefix="lhpk_x", key_hash="a", scopes=[])
    assert live.is_usable(now) is True

    revoked = PlatformApiKey(
        label="revoked",
        key_prefix="lhpk_x",
        key_hash="b",
        scopes=[],
        revoked_at=now - timedelta(seconds=1),
    )
    assert revoked.is_usable(now) is False

    expired = PlatformApiKey(
        label="expired",
        key_prefix="lhpk_x",
        key_hash="c",
        scopes=[],
        expires_at=now - timedelta(seconds=1),
    )
    assert expired.is_usable(now) is False

    # Expiry is inclusive: a key is dead at its stated moment, not after it.
    at_expiry = PlatformApiKey(
        label="boundary",
        key_prefix="lhpk_x",
        key_hash="d",
        scopes=[],
        expires_at=now,
    )
    assert at_expiry.is_usable(now) is False


# ── End-to-end through the API ────────────────────────────────────────────────


async def _mint(client: AsyncClient, **body) -> dict:
    payload = {"label": "ops-runbook", "scopes": ["platform:read"], **body}
    response = await client.post(
        "/api/platform/api-keys", headers=platform_headers(), json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_mint_returns_plaintext_once_and_persists_only_its_hash(
    client: AsyncClient, db_session
):
    minted = await _mint(client)
    plaintext = minted["key"]

    assert plaintext.startswith("lhpk_")
    assert minted["scopes"] == ["platform:read"]
    assert minted["is_active"] is True

    row = (
        await db_session.execute(
            select(PlatformApiKey).where(PlatformApiKey.id == minted["id"])
        )
    ).scalar_one()
    assert row.key_hash == platform_auth.hash_platform_api_key(plaintext)
    # The secret itself is nowhere in the row.
    assert plaintext not in (row.key_prefix + row.label + row.key_hash)

    listed = await client.get("/api/platform/api-keys", headers=platform_headers())
    assert listed.status_code == 200
    entry = next(k for k in listed.json()["keys"] if k["id"] == minted["id"])
    assert "key" not in entry


@pytest.mark.asyncio
async def test_minted_key_authenticates_a_platform_request(client: AsyncClient):
    minted = await _mint(client)

    response = await client.get(
        "/api/platform/plans",
        headers={"Authorization": f"Bearer {minted['key']}"},
    )
    assert response.status_code == 200
    assert "plans" in response.json()


@pytest.mark.asyncio
async def test_revoked_key_stops_working_immediately(client: AsyncClient):
    minted = await _mint(client)
    headers = {"Authorization": f"Bearer {minted['key']}"}

    assert (await client.get("/api/platform/plans", headers=headers)).status_code == 200

    revoked = await client.delete(
        f"/api/platform/api-keys/{minted['id']}", headers=platform_headers()
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"

    assert (await client.get("/api/platform/plans", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_key_only_carries_the_scopes_it_was_minted_with(client: AsyncClient):
    minted = await _mint(client, scopes=["platform:read"])
    headers = {"Authorization": f"Bearer {minted['key']}"}

    # platform:read is enough to list plans...
    assert (await client.get("/api/platform/plans", headers=headers)).status_code == 200
    # ...and not enough to reach troubleshooting data.
    denied = await client.get(
        "/api/platform/audit",
        headers=headers,
    )
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_a_minted_key_cannot_mint_further_keys(client: AsyncClient):
    """A leaked key must not be convertible into permanent replacements."""

    minted = await _mint(client, scopes=["platform:read", "platform:write"])
    headers = {"Authorization": f"Bearer {minted['key']}"}

    response = await client.post(
        "/api/platform/api-keys",
        headers=headers,
        json={"label": "escalated", "scopes": ["platform:write"]},
    )
    assert response.status_code == 403
    assert "bootstrap" in response.json()["detail"].lower()

    # Nor may it revoke one, which would otherwise be a denial-of-service on
    # the operator's own access.
    assert (
        await client.delete(f"/api/platform/api-keys/{minted['id']}", headers=headers)
    ).status_code == 403


@pytest.mark.asyncio
async def test_mint_rejects_scopes_the_session_does_not_hold(client: AsyncClient):
    response = await client.post(
        "/api/platform/api-keys",
        headers=platform_headers(["platform:read", "platform:write"]),
        json={"label": "too-powerful", "scopes": ["platform:debug"]},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unknown_and_malformed_keys_are_refused(client: AsyncClient):
    for bearer in ("lhpk_not-a-real-key", "lhpk_", "garbage"):
        response = await client.get(
            "/api/platform/plans", headers={"Authorization": f"Bearer {bearer}"}
        )
        assert response.status_code == 403, bearer


@pytest.mark.asyncio
async def test_expired_key_is_refused(client: AsyncClient, db_session):
    minted = await _mint(client, expires_in_days=1)
    row = (
        await db_session.execute(
            select(PlatformApiKey).where(PlatformApiKey.id == minted["id"])
        )
    ).scalar_one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db_session.commit()

    response = await client.get(
        "/api/platform/plans",
        headers={"Authorization": f"Bearer {minted['key']}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_key_use_is_recorded_for_revocation_decisions(
    client: AsyncClient, db_session
):
    minted = await _mint(client)
    await client.get(
        "/api/platform/plans", headers={"Authorization": f"Bearer {minted['key']}"}
    )

    row = (
        await db_session.execute(
            select(PlatformApiKey).where(PlatformApiKey.id == minted["id"])
        )
    ).scalar_one()
    await db_session.refresh(row)
    assert row.last_used_at is not None
