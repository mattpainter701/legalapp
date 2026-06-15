import logging
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import set_tenant_context

logger = logging.getLogger(__name__)

settings = get_settings()


def _get_fernet() -> Fernet:
    key = settings.TOKEN_ENCRYPTION_KEY
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is required for OAuth token storage")
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must be a valid Fernet key")


def _expires_at(expires_in: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=expires_in)


def _is_fresh(expires_at: datetime | None) -> bool:
    if not expires_at:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < expires_at - timedelta(seconds=60)


def encrypt_token(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()


async def refresh_microsoft_token(
    db: AsyncSession,
    tenant_id: str,
    credential_id: str | None = None,
) -> tuple[str, int] | None:
    """Refresh an M365 token. Returns (new_access_token, expires_in) or None."""
    from app.models.tenant_credential import TenantCredential

    await set_tenant_context(db, tenant_id)
    stmt = select(TenantCredential).where(
        TenantCredential.tenant_id == tenant_id,
        TenantCredential.provider == "microsoft",
        TenantCredential.is_active,
    )
    if credential_id:
        stmt = stmt.where(TenantCredential.id == credential_id)
    result = await db.execute(stmt)
    cred = result.scalar_one_or_none()

    if not cred or not cred.encrypted_refresh_token:
        return None

    refresh_token = decrypt_token(cred.encrypted_refresh_token)
    ms_tenant = settings.MICROSOFT_TENANT_ID
    token_url = f"https://login.microsoftonline.com/{ms_tenant}/oauth2/v2.0/token"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            token_url,
            data={
                "client_id": settings.MICROSOFT_CLIENT_ID,
                "client_secret": settings.MICROSOFT_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code != 200:
            logger.error(
                "Microsoft token refresh failed: status=%d body=%s",
                resp.status_code,
                resp.text,
            )
            return None

        data = resp.json()
        new_access_token = data.get("access_token")
        new_refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in", 3600)

        if not new_access_token:
            return None

        cred.encrypted_access_token = encrypt_token(new_access_token)
        if new_refresh_token:
            cred.encrypted_refresh_token = encrypt_token(new_refresh_token)
        cred.token_expires_at = _expires_at(expires_in)
        await db.commit()

        return new_access_token, expires_in


async def refresh_google_token(
    db: AsyncSession,
    tenant_id: str,
    credential_id: str | None = None,
) -> tuple[str, int] | None:
    """Refresh a Google token. Returns (new_access_token, expires_in) or None."""
    from app.models.tenant_credential import TenantCredential

    await set_tenant_context(db, tenant_id)
    stmt = select(TenantCredential).where(
        TenantCredential.tenant_id == tenant_id,
        TenantCredential.provider == "google",
        TenantCredential.is_active,
    )
    if credential_id:
        stmt = stmt.where(TenantCredential.id == credential_id)
    result = await db.execute(stmt)
    cred = result.scalar_one_or_none()

    if not cred or not cred.encrypted_refresh_token:
        return None

    refresh_token = decrypt_token(cred.encrypted_refresh_token)
    token_url = "https://oauth2.googleapis.com/token"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            token_url,
            data={
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code != 200:
            logger.error(
                "Google token refresh failed: status=%d body=%s",
                resp.status_code,
                resp.text,
            )
            return None

        data = resp.json()
        new_access_token = data.get("access_token")
        expires_in = data.get("expires_in", 3600)

        if not new_access_token:
            return None

        cred.encrypted_access_token = encrypt_token(new_access_token)
        cred.token_expires_at = _expires_at(expires_in)
        await db.commit()

        return new_access_token, expires_in


async def refresh_zoom_token(
    db: AsyncSession,
    tenant_id: str,
    credential_id: str | None = None,
) -> tuple[str, int] | None:
    """Refresh a tenant-level Zoom token. Returns (new_access_token, expires_in)."""
    from app.models.tenant_credential import TenantCredential

    await set_tenant_context(db, tenant_id)
    stmt = select(TenantCredential).where(
        TenantCredential.tenant_id == tenant_id,
        TenantCredential.provider == "zoom",
        TenantCredential.is_active,
    )
    if credential_id:
        stmt = stmt.where(TenantCredential.id == credential_id)
    result = await db.execute(stmt)
    cred = result.scalar_one_or_none()

    if not cred or not cred.encrypted_refresh_token:
        return None

    refresh_token = decrypt_token(cred.encrypted_refresh_token)
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://zoom.us/oauth/token",
            auth=(settings.ZOOM_CLIENT_ID, settings.ZOOM_CLIENT_SECRET),
            data={
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code != 200:
            logger.error(
                "Zoom token refresh failed: status=%d body=%s",
                resp.status_code,
                resp.text[:300],
            )
            return None

        data = resp.json()
        new_access_token = data.get("access_token")
        new_refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in", 3600)
        if not new_access_token:
            return None

        cred.encrypted_access_token = encrypt_token(new_access_token)
        if new_refresh_token:
            cred.encrypted_refresh_token = encrypt_token(new_refresh_token)
        cred.token_expires_at = _expires_at(expires_in)
        await db.commit()
        return new_access_token, expires_in


async def get_fresh_token(
    db: AsyncSession,
    tenant_id: str,
    provider: str,
    credential_id: str | None = None,
) -> str | None:
    """Get a valid access token, refreshing if needed."""
    from app.models.tenant_credential import TenantCredential

    await set_tenant_context(db, tenant_id)
    stmt = select(TenantCredential).where(
        TenantCredential.tenant_id == tenant_id,
        TenantCredential.provider == provider,
        TenantCredential.is_active,
    )
    if credential_id:
        stmt = stmt.where(TenantCredential.id == credential_id)
    result = await db.execute(stmt)
    cred = result.scalar_one_or_none()

    if not cred:
        return None

    if _is_fresh(cred.token_expires_at):
        return decrypt_token(cred.encrypted_access_token)

    if provider == "microsoft":
        refreshed = await refresh_microsoft_token(db, tenant_id, credential_id)
    elif provider == "google":
        refreshed = await refresh_google_token(db, tenant_id, credential_id)
    elif provider == "zoom":
        refreshed = await refresh_zoom_token(db, tenant_id, credential_id)
    else:
        return None

    if refreshed:
        return refreshed[0]
    return None


async def get_fresh_user_token(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    provider: str,
) -> str | None:
    """Get a valid per-user access token, refreshing if needed."""
    from app.models.user_oauth_token import UserOAuthToken

    await set_tenant_context(db, tenant_id)
    result = await db.execute(
        select(UserOAuthToken).where(
            UserOAuthToken.user_id == user_id,
            UserOAuthToken.tenant_id == tenant_id,
            UserOAuthToken.provider == provider,
        )
    )
    token_row = result.scalar_one_or_none()

    if not token_row:
        logger.warning(
            "get_fresh_user_token: no token row found for user_id=%s provider=%s tenant_id=%s",
            user_id,
            provider,
            tenant_id,
        )
        return None

    if _is_fresh(token_row.token_expires_at):
        return decrypt_token(token_row.encrypted_access_token)

    refresh_token = None
    if token_row.encrypted_refresh_token:
        refresh_token = decrypt_token(token_row.encrypted_refresh_token)

    if not refresh_token:
        logger.warning(
            "get_fresh_user_token: token expired and no refresh token for user_id=%s provider=%s tenant_id=%s",
            user_id,
            provider,
            tenant_id,
        )
        return None

    if provider == "microsoft":
        ms_tenant = settings.MICROSOFT_TENANT_ID
        token_url = f"https://login.microsoftonline.com/{ms_tenant}/oauth2/v2.0/token"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_url,
                data={
                    "client_id": settings.MICROSOFT_CLIENT_ID,
                    "client_secret": settings.MICROSOFT_CLIENT_SECRET,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "get_fresh_user_token: microsoft refresh failed status=%d user_id=%s",
                    resp.status_code,
                    user_id,
                )
                return None
            data = resp.json()
            new_access_token = data.get("access_token")
            new_refresh_token = data.get("refresh_token")
            expires_in = data.get("expires_in", 3600)
            if not new_access_token:
                logger.warning(
                    "get_fresh_user_token: microsoft refresh returned no access_token user_id=%s",
                    user_id,
                )
                return None
            token_row.encrypted_access_token = encrypt_token(new_access_token)
            if new_refresh_token:
                token_row.encrypted_refresh_token = encrypt_token(new_refresh_token)
            token_row.token_expires_at = _expires_at(expires_in)
            await db.commit()
            return new_access_token

    if provider == "google":
        token_url = "https://oauth2.googleapis.com/token"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_url,
                data={
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "get_fresh_user_token: google refresh failed status=%d user_id=%s",
                    resp.status_code,
                    user_id,
                )
                return None
            data = resp.json()
            new_access_token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            if not new_access_token:
                logger.warning(
                    "get_fresh_user_token: google refresh returned no access_token user_id=%s",
                    user_id,
                )
                return None
            token_row.encrypted_access_token = encrypt_token(new_access_token)
            token_row.token_expires_at = _expires_at(expires_in)
            await db.commit()
            return new_access_token

    if provider == "zoom":
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://zoom.us/oauth/token",
                auth=(settings.ZOOM_CLIENT_ID, settings.ZOOM_CLIENT_SECRET),
                data={
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "get_fresh_user_token: zoom refresh failed status=%d user_id=%s",
                    resp.status_code,
                    user_id,
                )
                return None
            data = resp.json()
            new_access_token = data.get("access_token")
            new_refresh_token = data.get("refresh_token")
            expires_in = data.get("expires_in", 3600)
            if not new_access_token:
                logger.warning(
                    "get_fresh_user_token: zoom refresh returned no access_token user_id=%s",
                    user_id,
                )
                return None
            token_row.encrypted_access_token = encrypt_token(new_access_token)
            if new_refresh_token:
                token_row.encrypted_refresh_token = encrypt_token(new_refresh_token)
            token_row.token_expires_at = _expires_at(expires_in)
            await db.commit()
            return new_access_token

    return None
