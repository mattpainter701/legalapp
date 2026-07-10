"""Production-safe Zoom Phone API gate.

Prints only aggregate readiness. OAuth/provider identifiers and tokens never
leave the process. A rotating refresh token is persisted by the normal service
before the read-only call-history probe runs.
"""

from __future__ import annotations

import asyncio
import secrets
import sys

from sqlalchemy import select

from app.database import async_session_maker, set_tenant_context
from app.models.tenant import Tenant
from app.models.tenant_credential import TenantCredential
from app.models.tenant_oauth_app import TenantOAuthApp
from app.services.zoom_phone import probe_zoom_phone_connection

REQUIRED_SCOPES = {
    "phone:read:list_call_logs:admin",
    "phone:read:call_log:admin",
}


async def main() -> int:
    async with async_session_maker() as root:
        tenant_ids = list(
            (
                await root.scalars(
                    select(Tenant.id)
                    .where(Tenant.is_active.is_(True))
                    .order_by(Tenant.id)
                )
            ).all()
        )

    configured = 0
    checked = 0
    for tenant_id in tenant_ids:
        async with async_session_maker() as db:
            await set_tenant_context(db, str(tenant_id))
            app = await db.scalar(
                select(TenantOAuthApp).where(
                    TenantOAuthApp.tenant_id == tenant_id,
                    TenantOAuthApp.provider == "zoom_phone",
                )
            )
            grant = await db.scalar(
                select(TenantCredential).where(
                    TenantCredential.tenant_id == tenant_id,
                    TenantCredential.provider == "zoom_phone",
                )
            )
            if not (app and app.is_active) and not (grant and grant.is_active):
                continue
            configured += 1
            ready = bool(
                app
                and app.is_active
                and app.encrypted_webhook_secret_token
                and app.zoom_account_id
                and grant
                and grant.is_active
                and grant.encrypted_refresh_token
                and grant.service_account_email
                and secrets.compare_digest(
                    app.zoom_account_id.strip(), grant.service_account_email.strip()
                )
                and grant.health == "healthy"
                and REQUIRED_SCOPES.issubset(set((grant.scopes or "").split()))
            )
            if not ready:
                print(
                    "An active tenant has incomplete Zoom Phone configuration.",
                    file=sys.stderr,
                )
                return 1
            try:
                result = await probe_zoom_phone_connection(
                    db,
                    tenant_id=str(tenant_id),
                )
            except Exception as exc:
                print(
                    f"Zoom Phone API probe failed: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                return 1
            if not result.get("ok"):
                print(
                    "Zoom Phone API probe returned an invalid result.", file=sys.stderr
                )
                return 1
            checked += 1

    if configured == 0 or checked != configured:
        print(
            "No production-ready tenant-owned Zoom Phone grant found.", file=sys.stderr
        )
        return 1
    print(f"Zoom Phone API probe passed for {checked} configured tenant(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
