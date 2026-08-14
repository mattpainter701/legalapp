"""Re-encrypt stored credentials with the primary Fernet key.

Configure TOKEN_ENCRYPTION_KEYS as ``new_key,old_key`` before running. The
operation is restartable: every ciphertext remains decryptable throughout the
staged rollout, and each tenant is committed independently.
"""

import argparse
import asyncio
from collections.abc import Iterable

from sqlalchemy import select

from app.database import async_session_maker, set_tenant_context
from app.models.llm_provider_key import LLMProviderKey
from app.models.qbo import QBOIntegration
from app.models.tenant import Tenant, TenantSettings
from app.models.tenant_credential import TenantCredential
from app.models.tenant_oauth_app import TenantOAuthApp
from app.models.user_oauth_token import UserOAuthToken
from app.services.token_vault import rotate_token_ciphertext


def _rotate_fields(rows: Iterable[object], fields: tuple[str, ...]) -> int:
    rotated = 0
    for row in rows:
        for field in fields:
            value = getattr(row, field, None)
            if value:
                setattr(row, field, rotate_token_ciphertext(value))
                rotated += 1
    return rotated


async def rotate_all(*, dry_run: bool) -> int:
    total = 0
    async with async_session_maker() as db:
        tenant_ids = list((await db.scalars(select(Tenant.id))).all())

    for tenant_id in tenant_ids:
        async with async_session_maker() as db:
            await set_tenant_context(db, str(tenant_id))
            tenant_credentials = list(
                (
                    await db.scalars(
                        select(TenantCredential).where(
                            TenantCredential.tenant_id == tenant_id
                        )
                    )
                ).all()
            )
            user_tokens = list(
                (
                    await db.scalars(
                        select(UserOAuthToken).where(
                            UserOAuthToken.tenant_id == tenant_id
                        )
                    )
                ).all()
            )
            oauth_apps = list(
                (
                    await db.scalars(
                        select(TenantOAuthApp).where(
                            TenantOAuthApp.tenant_id == tenant_id
                        )
                    )
                ).all()
            )
            qbo_rows = list(
                (
                    await db.scalars(
                        select(QBOIntegration).where(
                            QBOIntegration.tenant_id == tenant_id
                        )
                    )
                ).all()
            )
            total += _rotate_fields(
                tenant_credentials,
                ("encrypted_access_token", "encrypted_refresh_token"),
            )
            total += _rotate_fields(
                user_tokens, ("encrypted_access_token", "encrypted_refresh_token")
            )
            total += _rotate_fields(
                oauth_apps,
                (
                    "encrypted_client_id",
                    "encrypted_client_secret",
                    "encrypted_webhook_secret_token",
                ),
            )
            total += _rotate_fields(
                qbo_rows, ("encrypted_access_token", "encrypted_refresh_token")
            )

            tenant_settings = await db.scalar(
                select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
            )
            config = (
                dict(tenant_settings.customer_llm_config or {})
                if tenant_settings
                else {}
            )
            encrypted_api_key = config.get("encrypted_api_key")
            if encrypted_api_key:
                config["encrypted_api_key"] = rotate_token_ciphertext(encrypted_api_key)
                tenant_settings.customer_llm_config = config
                total += 1

            if dry_run:
                await db.rollback()
            else:
                await db.commit()

    # Operator LLM provider keys are platform-owned rather than tenant-owned.
    async with async_session_maker() as db:
        provider_keys = list((await db.scalars(select(LLMProviderKey))).all())
        total += _rotate_fields(provider_keys, ("encrypted_key",))
        if dry_run:
            await db.rollback()
        else:
            await db.commit()
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    count = asyncio.run(rotate_all(dry_run=args.dry_run))
    mode = "validated" if args.dry_run else "rotated"
    print(f"{mode} {count} encrypted credential value(s)")


if __name__ == "__main__":
    main()
