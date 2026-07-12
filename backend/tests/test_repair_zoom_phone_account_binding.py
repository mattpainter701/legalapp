from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from app.models.tenant_credential import TenantCredential
from app.models.tenant_oauth_app import TenantOAuthApp
from app.services.token_vault import decrypt_token, encrypt_token
from scripts import repair_zoom_phone_account_binding as repair_script


async def _seed_legacy_grant(
    db,
    tenant,
    *,
    app_mapping: str | None = None,
    grant_mapping: str | None = None,
):
    original_expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
    app = TenantOAuthApp(
        tenant_id=tenant.id,
        provider="zoom_phone",
        encrypted_client_id=encrypt_token("legacy-client-id"),
        encrypted_client_secret=encrypt_token("legacy-client-secret"),
        zoom_account_id=app_mapping,
        is_active=True,
    )
    grant = TenantCredential(
        tenant_id=tenant.id,
        provider="zoom_phone",
        encrypted_access_token=encrypt_token("legacy-access-token"),
        encrypted_refresh_token=encrypt_token("legacy-refresh-token"),
        token_expires_at=original_expiry,
        scopes="legacy:scope",
        service_account_email=grant_mapping,
        health="degraded",
        last_refresh_error="legacy error",
        is_active=True,
    )
    db.add_all([app, grant])
    await db.commit()
    return app, grant


@pytest.mark.asyncio
async def test_repair_binds_provider_account_and_rotates_grant_atomically(
    db_session,
    test_tenant,
):
    app, grant = await _seed_legacy_grant(db_session, test_tenant)
    old_expiry = grant.token_expires_at
    seen_request = False

    def zoom_handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = True
        assert str(request.url) == repair_script.ZOOM_TOKEN_URL
        assert request.headers["authorization"].startswith("Basic ")
        form = parse_qs(request.content.decode("utf-8"))
        assert form == {
            "grant_type": ["refresh_token"],
            "refresh_token": ["legacy-refresh-token"],
        }
        return httpx.Response(
            200,
            json={
                "account_id": "provider-returned-account",
                "access_token": "rotated-access-token",
                "refresh_token": "rotated-refresh-token",
                "scope": repair_script.settings.ZOOM_PHONE_SCOPES,
                "expires_in": 7200,
            },
        )

    await repair_script.repair_legacy_zoom_phone_account_binding(
        db_session,
        tenant_id=test_tenant.id,
        transport=httpx.MockTransport(zoom_handler),
    )

    assert seen_request
    await db_session.refresh(app)
    await db_session.refresh(grant)
    assert app.zoom_account_id == "provider-returned-account"
    assert grant.service_account_email == "provider-returned-account"
    assert decrypt_token(grant.encrypted_access_token) == "rotated-access-token"
    assert decrypt_token(grant.encrypted_refresh_token) == "rotated-refresh-token"
    assert grant.scopes == repair_script.settings.ZOOM_PHONE_SCOPES
    assert grant.missing_scopes is None
    assert grant.health == "healthy"
    assert grant.last_refresh_at is not None
    assert grant.last_refresh_error is None
    assert grant.token_expires_at > old_expiry + timedelta(hours=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "payload"),
    [
        (
            401,
            {
                "error": "invalid_grant",
                "account_id": "must-not-leak-account",
                "refresh_token": "must-not-leak-token",
            },
        ),
        (
            200,
            {
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            },
        ),
        (
            200,
            {
                "account_id": "new-account",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            },
        ),
        (
            200,
            {
                "account_id": "new-account",
                "access_token": "new-access",
                "expires_in": 0,
            },
        ),
    ],
)
async def test_provider_failure_or_invalid_response_leaves_database_unchanged(
    db_session,
    test_tenant,
    status_code,
    payload,
):
    app, grant = await _seed_legacy_grant(db_session, test_tenant)
    app_id = app.id
    grant_id = grant.id
    original_access = grant.encrypted_access_token
    original_refresh = grant.encrypted_refresh_token
    original_expiry = grant.token_expires_at
    original_scopes = grant.scopes

    def zoom_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    with pytest.raises(repair_script.ZoomPhoneAccountRepairError) as exc_info:
        await repair_script.repair_legacy_zoom_phone_account_binding(
            db_session,
            tenant_id=test_tenant.id,
            transport=httpx.MockTransport(zoom_handler),
        )

    error = str(exc_info.value)
    assert "must-not-leak-account" not in error
    assert "must-not-leak-token" not in error
    saved_app = await db_session.get(TenantOAuthApp, app_id)
    saved_grant = await db_session.get(TenantCredential, grant_id)
    assert saved_app.zoom_account_id is None
    assert saved_grant.service_account_email is None
    assert saved_grant.encrypted_access_token == original_access
    assert saved_grant.encrypted_refresh_token == original_refresh
    assert saved_grant.token_expires_at == original_expiry
    assert saved_grant.scopes == original_scopes
    assert saved_grant.health == "degraded"
    assert saved_grant.last_refresh_error == "legacy error"


@pytest.mark.asyncio
async def test_provider_transport_error_leaves_database_unchanged(
    db_session,
    test_tenant,
):
    app, grant = await _seed_legacy_grant(db_session, test_tenant)
    app_id = app.id
    grant_id = grant.id
    original_access = grant.encrypted_access_token
    original_refresh = grant.encrypted_refresh_token

    def failed_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "provider detail must-not-leak-token",
            request=request,
        )

    with pytest.raises(repair_script.ZoomPhoneAccountRepairError) as exc_info:
        await repair_script.repair_legacy_zoom_phone_account_binding(
            db_session,
            tenant_id=test_tenant.id,
            transport=httpx.MockTransport(failed_transport),
        )

    assert "must-not-leak-token" not in str(exc_info.value)
    saved_app = await db_session.get(TenantOAuthApp, app_id)
    saved_grant = await db_session.get(TenantCredential, grant_id)
    assert saved_app.zoom_account_id is None
    assert saved_grant.service_account_email is None
    assert saved_grant.encrypted_access_token == original_access
    assert saved_grant.encrypted_refresh_token == original_refresh


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("app_mapping", "grant_mapping"),
    [
        ("existing-account", "existing-account"),
        ("existing-account", None),
        (None, "existing-account"),
        ("first-account", "different-account"),
    ],
)
async def test_existing_mapping_is_never_overwritten_or_completed(
    db_session,
    test_tenant,
    app_mapping,
    grant_mapping,
):
    app, grant = await _seed_legacy_grant(
        db_session,
        test_tenant,
        app_mapping=app_mapping,
        grant_mapping=grant_mapping,
    )
    app_id = app.id
    grant_id = grant.id

    def unexpected_request(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("provider must not be called for a mapped grant")

    with pytest.raises(repair_script.ZoomPhoneAccountRepairError):
        await repair_script.repair_legacy_zoom_phone_account_binding(
            db_session,
            tenant_id=test_tenant.id,
            transport=httpx.MockTransport(unexpected_request),
        )

    saved_app = await db_session.get(TenantOAuthApp, app_id)
    saved_grant = await db_session.get(TenantCredential, grant_id)
    assert saved_app.zoom_account_id == app_mapping
    assert saved_grant.service_account_email == grant_mapping
    assert decrypt_token(saved_grant.encrypted_access_token) == "legacy-access-token"
    assert decrypt_token(saved_grant.encrypted_refresh_token) == "legacy-refresh-token"


@pytest.mark.asyncio
async def test_repair_requires_active_tenant_and_exactly_one_active_grant(
    db_session,
    test_tenant,
):
    app, grant = await _seed_legacy_grant(db_session, test_tenant)
    grant.is_active = False
    await db_session.commit()

    def unexpected_request(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("provider must not be called without one active grant")

    with pytest.raises(
        repair_script.ZoomPhoneAccountRepairError,
        match="exactly one active Zoom Phone OAuth grant",
    ):
        await repair_script.repair_legacy_zoom_phone_account_binding(
            db_session,
            tenant_id=test_tenant.id,
            transport=httpx.MockTransport(unexpected_request),
        )

    test_tenant.is_active = False
    grant.is_active = True
    await db_session.commit()
    with pytest.raises(
        repair_script.ZoomPhoneAccountRepairError,
        match="missing or inactive",
    ):
        await repair_script.repair_legacy_zoom_phone_account_binding(
            db_session,
            tenant_id=test_tenant.id,
            transport=httpx.MockTransport(unexpected_request),
        )


def test_cli_does_not_echo_unexpected_exception_details(monkeypatch, capsys):
    async def failed_run(_tenant_id):
        raise RuntimeError("tenant-or-token-material-must-not-leak")

    monkeypatch.setattr(repair_script, "_run", failed_run)
    result = repair_script.main(["--tenant-id", "00000000-0000-0000-0000-000000000001"])

    captured = capsys.readouterr()
    assert result == 1
    assert "tenant-or-token-material-must-not-leak" not in captured.err
    assert "00000000-0000-0000-0000-000000000001" not in captured.err
    assert captured.out == ""
