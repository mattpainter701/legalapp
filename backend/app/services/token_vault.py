import asyncio
import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import set_tenant_context

logger = logging.getLogger(__name__)

settings = get_settings()

TOKEN_REFRESH_TIMEOUT = 20.0
TOKEN_REFRESH_MAX_ATTEMPTS = 3
TOKEN_REFRESH_BASE_DELAY = 0.25
TOKEN_REFRESH_MAX_DELAY = 2.0
DB_LOCK_TIMEOUT_MS = 5000


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


def _record_refresh_success(row) -> None:
    if hasattr(row, "health"):
        row.health = (
            "missing_scopes" if getattr(row, "missing_scopes", None) else "healthy"
        )
    if hasattr(row, "last_refresh_at"):
        row.last_refresh_at = datetime.now(timezone.utc)
    if hasattr(row, "last_refresh_error"):
        row.last_refresh_error = None
    if hasattr(row, "is_active"):
        row.is_active = True


def _record_refresh_failure(row, status_code: int | None, body: str) -> None:
    error = f"{status_code or 'error'} {body[:500]}".strip()
    if hasattr(row, "last_refresh_at"):
        row.last_refresh_at = datetime.now(timezone.utc)
    if hasattr(row, "last_refresh_error"):
        row.last_refresh_error = error
    if "invalid_grant" in body:
        if hasattr(row, "health"):
            row.health = "revoked"
        if hasattr(row, "is_active"):
            row.is_active = False
    elif hasattr(row, "health") and getattr(row, "health", None) != "revoked":
        row.health = "refresh_failed"


def encrypt_token(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()


def _retry_after_delay(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())


def _retry_delay(resp: httpx.Response | None, attempt: int) -> float:
    retry_after = _retry_after_delay(resp.headers.get("Retry-After") if resp else None)
    if retry_after is not None:
        return min(retry_after, TOKEN_REFRESH_MAX_DELAY)
    return min(TOKEN_REFRESH_BASE_DELAY * (2**attempt), TOKEN_REFRESH_MAX_DELAY)


def _is_transient_response(resp: httpx.Response) -> bool:
    return resp.status_code == 429 or 500 <= resp.status_code < 600


async def _post_token_with_retry(url: str, **kwargs) -> httpx.Response:
    last_exc: httpx.HTTPError | None = None
    async with httpx.AsyncClient(timeout=TOKEN_REFRESH_TIMEOUT) as client:
        for attempt in range(TOKEN_REFRESH_MAX_ATTEMPTS):
            try:
                resp = await client.post(url, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if attempt == TOKEN_REFRESH_MAX_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(_retry_delay(None, attempt))
                continue

            if (
                not _is_transient_response(resp)
                or attempt == TOKEN_REFRESH_MAX_ATTEMPTS - 1
            ):
                return resp
            await asyncio.sleep(_retry_delay(resp, attempt))

    if last_exc:
        raise last_exc
    raise RuntimeError("token refresh retry loop exited unexpectedly")


async def _set_lock_timeout(db: AsyncSession) -> None:
    bind = db.get_bind()
    if bind and bind.dialect.name == "postgresql":
        await db.execute(text(f"SET LOCAL lock_timeout = '{DB_LOCK_TIMEOUT_MS}ms'"))


def _is_lock_timeout(exc: DBAPIError) -> bool:
    sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    return sqlstate in {"55P03", "57014"}


async def _execute_locked_scalar(db: AsyncSession, stmt):
    try:
        await _set_lock_timeout(db)
        result = await db.execute(stmt)
    except DBAPIError as exc:
        if not _is_lock_timeout(exc):
            raise
        await db.rollback()
        logger.warning("Timed out waiting for OAuth token row lock")
        return None
    return result.scalar_one_or_none()


async def _locked_tenant_credential(
    db: AsyncSession,
    tenant_id: str,
    provider: str,
    credential_id: str | None = None,
):
    from app.models.tenant_credential import TenantCredential

    await set_tenant_context(db, tenant_id)
    stmt = (
        select(TenantCredential)
        .where(
            TenantCredential.tenant_id == tenant_id,
            TenantCredential.provider == provider,
            TenantCredential.is_active,
        )
        .with_for_update()
    )
    if credential_id:
        stmt = stmt.where(TenantCredential.id == credential_id)
    return await _execute_locked_scalar(db, stmt)


async def _locked_user_token(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    provider: str,
):
    from app.models.user_oauth_token import UserOAuthToken

    await set_tenant_context(db, tenant_id)
    stmt = (
        select(UserOAuthToken)
        .where(
            UserOAuthToken.user_id == user_id,
            UserOAuthToken.tenant_id == tenant_id,
            UserOAuthToken.provider == provider,
        )
        .with_for_update()
    )
    return await _execute_locked_scalar(db, stmt)


def _token_request(provider: str, refresh_token: str) -> tuple[str, dict]:
    if provider == "microsoft":
        ms_tenant = settings.MICROSOFT_TENANT_ID
        return (
            f"https://login.microsoftonline.com/{ms_tenant}/oauth2/v2.0/token",
            {
                "data": {
                    "client_id": settings.MICROSOFT_CLIENT_ID,
                    "client_secret": settings.MICROSOFT_CLIENT_SECRET,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                }
            },
        )
    if provider == "google":
        return (
            "https://oauth2.googleapis.com/token",
            {
                "data": {
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                }
            },
        )
    if provider == "zoom":
        return (
            "https://zoom.us/oauth/token",
            {
                "auth": (settings.ZOOM_CLIENT_ID, settings.ZOOM_CLIENT_SECRET),
                "data": {
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            },
        )
    raise ValueError(f"Unsupported OAuth provider: {provider}")


async def _refresh_locked_row(
    db: AsyncSession,
    row,
    provider: str,
) -> tuple[str, int] | None:
    if not row or not getattr(row, "encrypted_refresh_token", None):
        return None

    refresh_token = decrypt_token(row.encrypted_refresh_token)
    token_url, request_kwargs = _token_request(provider, refresh_token)

    try:
        resp = await _post_token_with_retry(token_url, **request_kwargs)
    except httpx.HTTPError as exc:
        _record_refresh_failure(row, None, str(exc))
        await db.commit()
        logger.warning("%s token refresh request failed", provider, exc_info=True)
        return None

    if resp.status_code != 200:
        _record_refresh_failure(row, resp.status_code, resp.text)
        await db.commit()
        logger.warning(
            "%s token refresh failed: status=%d body=%s",
            provider,
            resp.status_code,
            resp.text[:300],
        )
        return None

    data = resp.json()
    new_access_token = data.get("access_token")
    new_refresh_token = data.get("refresh_token")
    expires_in = data.get("expires_in", 3600)

    if not new_access_token:
        _record_refresh_failure(row, None, "missing access_token in token response")
        await db.commit()
        return None

    row.encrypted_access_token = encrypt_token(new_access_token)
    if new_refresh_token:
        row.encrypted_refresh_token = encrypt_token(new_refresh_token)
    row.token_expires_at = _expires_at(expires_in)
    _record_refresh_success(row)
    await db.commit()

    return new_access_token, expires_in


async def refresh_microsoft_token(
    db: AsyncSession,
    tenant_id: str,
    credential_id: str | None = None,
) -> tuple[str, int] | None:
    """Refresh an M365 token. Returns (new_access_token, expires_in) or None."""
    cred = await _locked_tenant_credential(
        db, tenant_id, "microsoft", credential_id=credential_id
    )
    return await _refresh_locked_row(db, cred, "microsoft")


async def refresh_google_token(
    db: AsyncSession,
    tenant_id: str,
    credential_id: str | None = None,
) -> tuple[str, int] | None:
    """Refresh a Google token. Returns (new_access_token, expires_in) or None."""
    cred = await _locked_tenant_credential(
        db, tenant_id, "google", credential_id=credential_id
    )
    return await _refresh_locked_row(db, cred, "google")


async def refresh_zoom_token(
    db: AsyncSession,
    tenant_id: str,
    credential_id: str | None = None,
) -> tuple[str, int] | None:
    """Refresh a tenant-level Zoom token. Returns (new_access_token, expires_in)."""
    cred = await _locked_tenant_credential(
        db, tenant_id, "zoom", credential_id=credential_id
    )
    return await _refresh_locked_row(db, cred, "zoom")


async def revoke_google_token(token: str) -> bool:
    """Revoke a Google OAuth token (refresh token preferred — invalidates the whole grant)."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/revoke",
                data={"token": token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError:
        logger.warning("Google token revoke request failed", exc_info=True)
        return False
    if resp.status_code == 200:
        return True
    logger.warning(
        "Google token revoke failed: status=%d body=%s", resp.status_code, resp.text[:300]
    )
    return False


async def revoke_microsoft_token(token: str) -> bool:
    """Best-effort Microsoft token revocation.

    Unlike Google, the Microsoft identity platform v2 endpoint has no per-token
    revoke API for confidential-client access/refresh tokens. The closest
    equivalent (``revokeSignInSessions``) invalidates ALL of the user's sessions
    across every app and requires elevated Graph permissions this app does not
    request, so it is deliberately not called here. Disconnecting still deletes
    the stored credential so this app stops using the token; the token itself
    remains valid at Microsoft until it naturally expires.
    """
    logger.info(
        "Microsoft has no safe per-token revoke API; local credential removal only"
    )
    return False


async def revoke_provider_token(
    provider: str, access_token: str | None, refresh_token: str | None
) -> bool:
    """Revoke whichever token invalidates the most at the provider, if any exists."""
    token = refresh_token or access_token
    if not token:
        return False
    if provider == "google":
        return await revoke_google_token(token)
    if provider == "microsoft":
        return await revoke_microsoft_token(token)
    return False


async def get_fresh_token(
    db: AsyncSession,
    tenant_id: str,
    provider: str,
    credential_id: str | None = None,
) -> str | None:
    """Get a valid access token, refreshing if needed."""
    if provider not in {"microsoft", "google", "zoom"}:
        return None

    cred = await _locked_tenant_credential(
        db, tenant_id, provider, credential_id=credential_id
    )
    if not cred:
        return None

    if _is_fresh(cred.token_expires_at):
        return decrypt_token(cred.encrypted_access_token)

    refreshed = await _refresh_locked_row(db, cred, provider)
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
    if provider not in {"microsoft", "google", "zoom"}:
        return None

    token_row = await _locked_user_token(db, tenant_id, user_id, provider)

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

    if not token_row.encrypted_refresh_token:
        logger.warning(
            "get_fresh_user_token: token expired and no refresh token for user_id=%s provider=%s tenant_id=%s",
            user_id,
            provider,
            tenant_id,
        )
        return None

    refreshed = await _refresh_locked_row(db, token_row, provider)
    if refreshed:
        return refreshed[0]
    return None
