"""Seed the disposable database used by the real-browser first-customer tests.

This command is intentionally impossible to run against a normal development or
production database: it requires both ``DEV_MODE=true`` and ``E2E_TEST=true``,
and the configured database name must contain ``e2e``.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import bcrypt
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.database import async_session_maker, set_tenant_context
from app.models.tenant import Tenant, TenantSettings
from app.models.user import User
from app.models.conversation import Conversation
from app.services.rbac_service import provision_tenant_rbac


TENANT_DOMAIN = "playwright-e2e.example.com"
OTHER_TENANT_DOMAIN = "other-playwright-e2e.example.com"
OTHER_TENANT_CONVERSATION_ID = uuid.UUID("00000000-0000-4000-8000-0000000000e2")
DEFAULT_EMAIL = "reception@playwright-e2e.example.com"
DEFAULT_PASSWORD = "Playwright-Only-42!"


def _require_disposable_database() -> None:
    settings = get_settings()
    database_name = make_url(settings.DATABASE_URL).database or ""
    if os.getenv("E2E_TEST", "").lower() not in {"1", "true", "yes"}:
        raise SystemExit("Refusing to seed without E2E_TEST=true")
    if not settings.DEV_MODE:
        raise SystemExit("Refusing to seed unless DEV_MODE=true")
    if "e2e" not in database_name.lower():
        raise SystemExit(
            "Refusing to seed a database whose name does not contain 'e2e'"
        )


async def seed() -> None:
    _require_disposable_database()
    email = os.getenv("E2E_USER_EMAIL", DEFAULT_EMAIL).strip().lower()
    password = os.getenv("E2E_USER_PASSWORD", DEFAULT_PASSWORD)

    async with async_session_maker() as db:
        # The database is disposable, but deleting the named tenant also makes
        # repeated local runs deterministic without touching unrelated rows.
        await db.execute(
            delete(Tenant).where(
                Tenant.domain.in_([TENANT_DOMAIN, OTHER_TENANT_DOMAIN])
            )
        )
        await db.commit()

        tenant = Tenant(
            name="Playwright First Customer",
            domain=TENANT_DOMAIN,
            company_name="Playwright First Customer",
            billing_tier="intake_trial",
            is_active=True,
            onboarding_completed=True,
        )
        db.add(tenant)
        await db.flush()

        settings = TenantSettings(
            tenant_id=tenant.id,
            custom_config={"plan": "intake-only"},
        )
        receptionist = User(
            tenant_id=tenant.id,
            email=email,
            full_name="E2E Reception Admin",
            role="admin",
            password_hash=bcrypt.hashpw(
                password.encode("utf-8"), bcrypt.gensalt(rounds=4)
            ).decode("utf-8"),
            oauth_provider="e2e",
            oauth_subject="e2e-reception-admin",
            is_active=True,
            license_active=True,
        )
        assignee = User(
            tenant_id=tenant.id,
            email="casey.attorney@playwright-e2e.example.com",
            full_name="Casey Attorney",
            role="user",
            oauth_provider="e2e",
            oauth_subject="e2e-casey-attorney",
            is_active=True,
            license_active=True,
        )
        db.add_all([settings, receptionist, assignee])
        await db.flush()
        await db.commit()

        await provision_tenant_rbac(db, tenant.id, receptionist.id)
        await db.commit()

        # A real object in a separate tenant makes the browser suite's denial
        # check meaningful: it is not merely asking for a random UUID.
        other_tenant = Tenant(
            name="Other Playwright Tenant",
            domain=OTHER_TENANT_DOMAIN,
            company_name="Other Playwright Tenant",
            billing_tier="intake_trial",
            is_active=True,
            onboarding_completed=True,
        )
        db.add(other_tenant)
        await db.flush()
        await set_tenant_context(db, str(other_tenant.id))
        other_user = User(
            tenant_id=other_tenant.id,
            email="owner@other-playwright-e2e.example.com",
            full_name="Other Tenant Owner",
            role="admin",
            password_hash=bcrypt.hashpw(
                password.encode("utf-8"), bcrypt.gensalt(rounds=4)
            ).decode("utf-8"),
            oauth_provider="e2e",
            oauth_subject="e2e-other-tenant-owner",
            is_active=True,
            license_active=True,
        )
        db.add(other_user)
        await db.flush()
        await provision_tenant_rbac(db, other_tenant.id, other_user.id)
        db.add(
            Conversation(
                id=OTHER_TENANT_CONVERSATION_ID,
                tenant_id=other_tenant.id,
                user_id=other_user.id,
                title="Cross-tenant E2E sentinel",
            )
        )
        await db.commit()

    print(f"Seeded disposable first-customer tenant for {email}")


if __name__ == "__main__":
    asyncio.run(seed())
