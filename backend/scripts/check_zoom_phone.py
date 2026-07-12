"""Production-safe Zoom Phone API gate.

Prints only aggregate readiness. OAuth/provider identifiers and tokens never
leave the process. A rotating refresh token is persisted by the normal service
before the read-only call-history probe runs.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from uuid import UUID

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


async def main(tenant_id: UUID) -> int:
    async with async_session_maker() as db:
        await set_tenant_context(db, str(tenant_id))
        active_tenant_id = await db.scalar(
            select(Tenant.id).where(
                Tenant.id == tenant_id,
                Tenant.is_active.is_(True),
            )
        )
        if active_tenant_id is None:
            print("Required Zoom Phone tenant is missing or inactive.", file=sys.stderr)
            return 1
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
                "Required tenant has incomplete Zoom Phone configuration.",
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
            print("Zoom Phone API probe returned an invalid result.", file=sys.stderr)
            return 1

    print("Zoom Phone API probe passed for the required tenant.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tenant-id",
        required=True,
        type=UUID,
        help="Exact sold tenant UUID to probe",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    raise SystemExit(asyncio.run(main(arguments.tenant_id)))
