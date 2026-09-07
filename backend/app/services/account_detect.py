"""Provider account-tier detection from OAuth claims and safe lazy backfills."""

import logging
import re
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import set_tenant_context
from app.models.tenant_credential import TenantCredential
from app.services.token_vault import get_fresh_token

logger = logging.getLogger(__name__)
CONSUMER_TID = "9188040d-6c67-4c5b-b112-36a304b66dad"
_DOMAIN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,253}[A-Za-z0-9])?\.[A-Za-z]{2,63}$"
)


def _domain(value: object) -> str | None:
    value = str(value or "").strip().lower().rstrip(".")
    return value if _DOMAIN.fullmatch(value) else None


def detect_google(claims: dict | None) -> tuple[str, str | None]:
    if not isinstance(claims, dict):
        return "unknown", None
    hd = _domain(claims.get("hd"))
    email = str(claims.get("email") or "").strip().lower()
    if hd and "@" in email and email.rsplit("@", 1)[1] == hd:
        return "workspace", hd
    # A malformed or mismatched hosted-domain claim must not upgrade a token.
    if "hd" in claims and claims.get("hd"):
        return "unknown", None
    if email.endswith("@gmail.com") or email.endswith("@googlemail.com"):
        return "personal", None
    if email and "@" in email:
        return "personal", None
    return "unknown", None


def detect_microsoft(claims: dict | None) -> tuple[str, str | None]:
    if not isinstance(claims, dict):
        return "unknown", None
    tid = str(claims.get("tid") or "").strip().lower()
    issuer = str(claims.get("iss") or "").lower()
    if tid == CONSUMER_TID or "consumers" in issuer:
        return "consumer", None
    try:
        uuid.UUID(tid)
    except (ValueError, AttributeError):
        return "unknown", None
    domain = _domain(claims.get("domain") or claims.get("tenant_domain"))
    if not domain:
        for key in ("preferred_username", "upn", "email"):
            value = str(claims.get(key) or "")
            if "@" in value:
                domain = _domain(value.rsplit("@", 1)[1])
                if domain:
                    break
    return "azure_ad", domain


async def _json_get(url: str, token: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                url, headers={"Authorization": f"Bearer {token}"}
            )
        if response.status_code != 200:
            return None
        data = response.json()
        return data if isinstance(data, dict) else None
    except Exception:
        logger.warning("provider account-tier backfill failed", exc_info=True)
        return None


async def backfill_google(token: str) -> tuple[str, str | None]:
    return detect_google(
        await _json_get("https://openidconnect.googleapis.com/v1/userinfo", token)
    )


async def backfill_microsoft(token: str) -> tuple[str, str | None]:
    data = await _json_get(
        "https://graph.microsoft.com/v1.0/organization?$select=verifiedDomains", token
    )
    if not data:
        return "unknown", None
    values = data.get("value") or []
    if not values:
        return "unknown", None
    domain = None
    for org in values:
        for item in org.get("verifiedDomains", []) if isinstance(org, dict) else []:
            if item.get("isDefault"):
                domain = _domain(item.get("name"))
                break
        if domain:
            break
    return ("azure_ad", domain) if values else ("unknown", None)


async def backfill_unknown_credentials(
    db: AsyncSession,
    tenant_id: str,
    credentials: list[TenantCredential] | None = None,
) -> list[TenantCredential]:
    """Best-effort classify legacy credentials, recording one safe attempt.

    The attempt timestamp is committed before network work so token failures
    cannot cause an unbounded request loop. Returned rows are freshly queried
    after any commit/rollback to avoid expired ORM state.
    """
    tenant_uuid = uuid.UUID(str(tenant_id))
    await set_tenant_context(db, str(tenant_uuid))
    if credentials is None:
        result = await db.execute(
            select(TenantCredential).where(TenantCredential.tenant_id == tenant_uuid)
        )
        credentials = list(result.scalars().all())
    providers = {
        credential.provider
        for credential in credentials
        if credential.provider in {"google", "microsoft"}
    }
    for provider in providers:
        await set_tenant_context(db, str(tenant_uuid))
        result = await db.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == tenant_uuid,
                TenantCredential.provider == provider,
            )
        )
        credential = result.scalar_one_or_none()
        if credential is None or credential.account_detected_at is not None:
            continue
        credential.account_detected_at = datetime.now(timezone.utc)
        await db.commit()
        try:
            token = await get_fresh_token(db, str(tenant_uuid), provider)
            detected = (
                (await backfill_google(token))
                if provider == "google" and token
                else (
                    await backfill_microsoft(token)
                    if provider == "microsoft" and token
                    else ("unknown", None)
                )
            )
            # The token refresh may commit/expire the ORM instance. Reload the
            # row after network work so a superseding credential update wins.
            await set_tenant_context(db, str(tenant_uuid))
            fresh_result = await db.execute(
                select(TenantCredential).where(
                    TenantCredential.tenant_id == tenant_uuid,
                    TenantCredential.provider == provider,
                )
            )
            fresh_credential = fresh_result.scalar_one_or_none()
            if fresh_credential is not None:
                apply_detection(fresh_credential, *detected)
            await db.commit()
        except Exception:
            logger.warning(
                "provider tier backfill failed for %s",
                provider,
                exc_info=True,
            )
            await db.rollback()
            await set_tenant_context(db, str(tenant_uuid))
        await set_tenant_context(db, str(tenant_uuid))
    await set_tenant_context(db, str(tenant_uuid))
    result = await db.execute(
        select(TenantCredential).where(TenantCredential.tenant_id == tenant_uuid)
    )
    return list(result.scalars().all())


def apply_detection(
    credential: TenantCredential, account_type: str, account_domain: str | None
) -> None:
    credential.account_type = account_type
    credential.account_domain = account_domain
    credential.account_detected_at = datetime.now(timezone.utc)


async def persist(
    db: AsyncSession,
    tenant_id: str,
    provider: str,
    account_type: str,
    account_domain: str | None,
) -> None:
    tenant_uuid = uuid.UUID(str(tenant_id))
    await set_tenant_context(db, str(tenant_uuid))
    row = (
        await db.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == tenant_uuid,
                TenantCredential.provider == provider,
            )
        )
    ).scalar_one_or_none()
    if row:
        apply_detection(row, account_type, account_domain)
        await db.commit()
