"""Tenant-owned OAuth app credentials."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.tenant_oauth_app import TenantOAuthApp
from app.services.token_vault import decrypt_token, encrypt_token

settings = get_settings()
ZOOM_PHONE_APP_PROVIDER = "zoom_phone"


@dataclass(slots=True)
class OAuthClientConfig:
    client_id: str
    client_secret: str
    # Zoom's OAuth token response may include the opaque API account id, but
    # administrators are not expected to discover or enter it.  This value is
    # therefore an optional webhook binding, not an OAuth prerequisite.
    account_id: str | None
    source: str


def mask_client_id(client_id: str | None) -> str | None:
    if not client_id:
        return None
    if len(client_id) <= 8:
        return "****"
    return f"{client_id[:4]}...{client_id[-4:]}"


async def get_tenant_oauth_app(
    db: AsyncSession,
    *,
    tenant_id: str | uuid.UUID,
    provider: str,
) -> TenantOAuthApp | None:
    tenant_uuid = uuid.UUID(str(tenant_id))
    result = await db.execute(
        select(TenantOAuthApp).where(
            TenantOAuthApp.tenant_id == tenant_uuid,
            TenantOAuthApp.provider == provider,
            TenantOAuthApp.is_active,
        )
    )
    return result.scalar_one_or_none()


async def get_zoom_phone_oauth_client(
    db: AsyncSession,
    *,
    tenant_id: str | uuid.UUID,
) -> OAuthClientConfig | None:
    app = await get_tenant_oauth_app(
        db,
        tenant_id=tenant_id,
        provider=ZOOM_PHONE_APP_PROVIDER,
    )
    if app:
        return OAuthClientConfig(
            client_id=decrypt_token(app.encrypted_client_id),
            client_secret=decrypt_token(app.encrypted_client_secret),
            account_id=app.zoom_account_id,
            source="tenant",
        )
    return None


async def get_zoom_phone_webhook_secret(
    db: AsyncSession,
    *,
    tenant_id: str | uuid.UUID | None = None,
) -> str | None:
    if tenant_id:
        app = await get_tenant_oauth_app(
            db,
            tenant_id=tenant_id,
            provider=ZOOM_PHONE_APP_PROVIDER,
        )
        if app:
            return (
                decrypt_token(app.encrypted_webhook_secret_token)
                if app.encrypted_webhook_secret_token
                else None
            )
    return None


async def upsert_zoom_phone_oauth_app(
    db: AsyncSession,
    *,
    tenant_id: str | uuid.UUID,
    user_id: str | uuid.UUID,
    client_id: str,
    client_secret: str,
    zoom_account_id: str | None,
    webhook_secret_token: str | None = None,
    redirect_uri: str,
    scopes: str,
) -> TenantOAuthApp:
    tenant_uuid = uuid.UUID(str(tenant_id))
    result = await db.execute(
        select(TenantOAuthApp).where(
            TenantOAuthApp.tenant_id == tenant_uuid,
            TenantOAuthApp.provider == ZOOM_PHONE_APP_PROVIDER,
        )
    )
    app = result.scalar_one_or_none()
    encrypted_client_id = encrypt_token(client_id)
    encrypted_client_secret = encrypt_token(client_secret)
    if app:
        app.encrypted_client_id = encrypted_client_id
        app.encrypted_client_secret = encrypted_client_secret
        app.zoom_account_id = zoom_account_id
        if webhook_secret_token:
            app.encrypted_webhook_secret_token = encrypt_token(webhook_secret_token)
        app.redirect_uri = redirect_uri
        app.scopes = scopes
        app.configured_by_user_id = uuid.UUID(str(user_id))
        app.is_active = True
        await db.flush()
        return app

    app = TenantOAuthApp(
        tenant_id=tenant_uuid,
        provider=ZOOM_PHONE_APP_PROVIDER,
        encrypted_client_id=encrypted_client_id,
        encrypted_client_secret=encrypted_client_secret,
        zoom_account_id=zoom_account_id,
        encrypted_webhook_secret_token=(
            encrypt_token(webhook_secret_token) if webhook_secret_token else None
        ),
        redirect_uri=redirect_uri,
        scopes=scopes,
        configured_by_user_id=uuid.UUID(str(user_id)),
        is_active=True,
    )
    db.add(app)
    await db.flush()
    return app
