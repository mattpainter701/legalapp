"""Repair one legacy Zoom Phone grant that has no explicit account binding.

This is intentionally a narrow, one-time operator tool. It does not discover
tenants, accept account identifiers from an operator, or overwrite an existing
mapping. The only source of truth for the repaired mapping is a successful Zoom
refresh-token response.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session_maker, set_tenant_context
from app.models.tenant import Tenant
from app.models.tenant_credential import TenantCredential
from app.models.tenant_oauth_app import TenantOAuthApp
from app.services.token_vault import decrypt_token, encrypt_token


ZOOM_PHONE_PROVIDER = "zoom_phone"
ZOOM_TOKEN_URL = "https://zoom.us/oauth/token"
ZOOM_TOKEN_TIMEOUT_SECONDS = 20.0
DB_LOCK_TIMEOUT_SECONDS = 10
settings = get_settings()


class ZoomPhoneAccountRepairError(RuntimeError):
    """A safe-to-display refusal or repair failure."""


@dataclass(frozen=True, slots=True)
class ZoomRefreshResult:
    account_id: str
    access_token: str
    refresh_token: str | None
    scopes: str | None
    expires_in: int


def _nonempty_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ZoomPhoneAccountRepairError(
            f"Zoom token refresh returned no usable {field}."
        )
    return value.strip()


def _optional_string(value: Any, *, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ZoomPhoneAccountRepairError(
            f"Zoom token refresh returned an invalid {field}."
        )
    value = value.strip()
    return value or None


def _parse_expires_in(value: Any) -> int:
    if isinstance(value, bool):
        raise ZoomPhoneAccountRepairError(
            "Zoom token refresh returned an invalid expiry."
        )
    try:
        expires_in = int(value)
    except (TypeError, ValueError):
        raise ZoomPhoneAccountRepairError(
            "Zoom token refresh returned an invalid expiry."
        ) from None
    if expires_in <= 0:
        raise ZoomPhoneAccountRepairError(
            "Zoom token refresh returned an invalid expiry."
        )
    return expires_in


async def _request_zoom_refresh(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ZoomRefreshResult:
    try:
        async with httpx.AsyncClient(
            timeout=ZOOM_TOKEN_TIMEOUT_SECONDS,
            follow_redirects=False,
            transport=transport,
        ) as client:
            response = await client.post(
                ZOOM_TOKEN_URL,
                auth=(client_id, client_secret),
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
    except httpx.HTTPError as exc:
        raise ZoomPhoneAccountRepairError(
            "Zoom token refresh could not be completed."
        ) from exc

    if response.status_code != 200:
        # Never include Zoom's response body: it can contain account, client,
        # or token material and is not required for this one-time decision.
        raise ZoomPhoneAccountRepairError(
            f"Zoom token refresh failed with HTTP {response.status_code}."
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise ZoomPhoneAccountRepairError(
            "Zoom token refresh returned an invalid response."
        ) from exc
    if not isinstance(payload, dict):
        raise ZoomPhoneAccountRepairError(
            "Zoom token refresh returned an invalid response."
        )

    return ZoomRefreshResult(
        account_id=_nonempty_string(payload.get("account_id"), field="account ID"),
        access_token=_nonempty_string(
            payload.get("access_token"), field="access token"
        ),
        refresh_token=_optional_string(
            payload.get("refresh_token"), field="refresh token"
        ),
        scopes=_optional_string(payload.get("scope"), field="scope list"),
        expires_in=_parse_expires_in(payload.get("expires_in", 3600)),
    )


def _require_unbound_legacy_rows(
    app: TenantOAuthApp,
    grant: TenantCredential,
) -> None:
    app_mapping = (app.zoom_account_id or "").strip()
    grant_mapping = (grant.service_account_email or "").strip()
    if not app_mapping and not grant_mapping:
        return
    if app_mapping and grant_mapping:
        if secrets.compare_digest(app_mapping, grant_mapping):
            raise ZoomPhoneAccountRepairError(
                "Zoom Phone account binding already exists; no repair was performed."
            )
        raise ZoomPhoneAccountRepairError(
            "Existing Zoom Phone account mappings conflict; repair was refused."
        )
    raise ZoomPhoneAccountRepairError(
        "A partial Zoom Phone account mapping exists; repair was refused."
    )


async def repair_legacy_zoom_phone_account_binding(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Bind exactly one active legacy app/grant using Zoom's refresh response.

    The tenant, OAuth app, and grant remain locked through the refresh and
    commit. No ORM field is changed until the complete provider response and
    replacement ciphertexts have been validated.
    """

    try:
        await set_tenant_context(db, str(tenant_id))
        bind = db.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            await db.execute(
                text(f"SET LOCAL lock_timeout = '{DB_LOCK_TIMEOUT_SECONDS}s'")
            )

        tenant = await db.scalar(
            select(Tenant).where(Tenant.id == tenant_id).with_for_update()
        )
        if tenant is None or not tenant.is_active:
            raise ZoomPhoneAccountRepairError(
                "The required tenant is missing or inactive."
            )

        apps = list(
            (
                await db.scalars(
                    select(TenantOAuthApp)
                    .where(
                        TenantOAuthApp.tenant_id == tenant_id,
                        TenantOAuthApp.provider == ZOOM_PHONE_PROVIDER,
                        TenantOAuthApp.is_active.is_(True),
                    )
                    .with_for_update()
                )
            ).all()
        )
        if len(apps) != 1:
            raise ZoomPhoneAccountRepairError(
                "The tenant must have exactly one active Zoom Phone OAuth app."
            )

        grants = list(
            (
                await db.scalars(
                    select(TenantCredential)
                    .where(
                        TenantCredential.tenant_id == tenant_id,
                        TenantCredential.provider == ZOOM_PHONE_PROVIDER,
                        TenantCredential.is_active.is_(True),
                    )
                    .with_for_update()
                )
            ).all()
        )
        if len(grants) != 1:
            raise ZoomPhoneAccountRepairError(
                "The tenant must have exactly one active Zoom Phone OAuth grant."
            )

        app = apps[0]
        grant = grants[0]
        _require_unbound_legacy_rows(app, grant)

        if not grant.encrypted_refresh_token:
            raise ZoomPhoneAccountRepairError(
                "The active Zoom Phone grant has no refresh token."
            )
        try:
            client_id = decrypt_token(app.encrypted_client_id)
            client_secret = decrypt_token(app.encrypted_client_secret)
            refresh_token = decrypt_token(grant.encrypted_refresh_token)
        except Exception as exc:
            raise ZoomPhoneAccountRepairError(
                "Stored Zoom Phone credentials could not be decrypted."
            ) from exc
        if (
            not client_id.strip()
            or not client_secret.strip()
            or not refresh_token.strip()
        ):
            raise ZoomPhoneAccountRepairError(
                "Stored Zoom Phone credentials are incomplete."
            )

        refreshed = await _request_zoom_refresh(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            transport=transport,
        )

        # Build every replacement value before mutating either ORM row. This
        # keeps encryption/configuration errors on the no-write path.
        encrypted_access_token = encrypt_token(refreshed.access_token)
        encrypted_refresh_token = (
            encrypt_token(refreshed.refresh_token)
            if refreshed.refresh_token
            else grant.encrypted_refresh_token
        )
        scope_string = refreshed.scopes or grant.scopes or settings.ZOOM_PHONE_SCOPES
        granted_scopes = set(scope_string.split())
        required_scopes = set(settings.ZOOM_PHONE_SCOPES.split())
        missing_scopes = required_scopes - granted_scopes
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=refreshed.expires_in)

        # Re-check the locked rows immediately before assignment. There is no
        # force path and an existing value is never overwritten.
        _require_unbound_legacy_rows(app, grant)
        app.zoom_account_id = refreshed.account_id
        grant.service_account_email = refreshed.account_id
        grant.encrypted_access_token = encrypted_access_token
        grant.encrypted_refresh_token = encrypted_refresh_token
        grant.token_expires_at = expires_at
        grant.scopes = scope_string
        grant.missing_scopes = " ".join(sorted(missing_scopes)) or None
        grant.health = "missing_scopes" if missing_scopes else "healthy"
        grant.last_refresh_at = now
        grant.last_refresh_error = None

        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def _run(tenant_id: UUID) -> None:
    async with async_session_maker() as db:
        await repair_legacy_zoom_phone_account_binding(db, tenant_id=tenant_id)


def _parse_tenant_id(raw: str) -> UUID:
    try:
        return UUID(raw)
    except (AttributeError, TypeError, ValueError):
        raise ZoomPhoneAccountRepairError(
            "--tenant-id must be an exact UUID."
        ) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Repair one unbound legacy Zoom Phone OAuth grant."
    )
    parser.add_argument(
        "--tenant-id",
        required=True,
        help="Exact tenant UUID (never discovered or inferred)",
    )
    args = parser.parse_args(argv)

    try:
        tenant_id = _parse_tenant_id(args.tenant_id)
        asyncio.run(_run(tenant_id))
    except ZoomPhoneAccountRepairError as exc:
        print(f"Zoom Phone account repair refused: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # Raw database/provider exceptions can embed identifiers or request
        # material, so deliberately suppress their details at this boundary.
        print(
            "Zoom Phone account repair failed safely due to an unexpected error.",
            file=sys.stderr,
        )
        return 1

    print("Zoom Phone legacy account binding repaired successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
