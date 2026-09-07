from datetime import datetime, timezone
import logging
import uuid

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import set_tenant_context
from app.models.user import User
from app.services.token_vault import get_fresh_token
from app.services import capabilities as cap

settings = get_settings()
logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GOOGLE_DIRECTORY_BASE = "https://admin.googleapis.com/admin/directory/v1"


class UserSyncService:
    async def _directory_sync_blocked(
        self, db: AsyncSession, tenant_id: str, provider: str
    ) -> dict | None:
        from app.models.tenant_credential import TenantCredential

        row = (
            await db.execute(
                select(TenantCredential).where(
                    TenantCredential.tenant_id == uuid.UUID(str(tenant_id)),
                    TenantCredential.provider == provider,
                )
            )
        ).scalar_one_or_none()
        matrix = cap.resolve(
            provider,
            row.account_type if row else None,
            row.scopes if row else None,
            row.last_user_sync_status if row else None,
        )
        directory = matrix["directory_sync"]
        if directory["available"]:
            return None
        await self._save_sync_state(
            db, tenant_id, provider, status="not_applicable", error=directory["reason"]
        )
        return {
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "total": 0,
            "status": "not_applicable",
            "reason": directory["reason"],
        }

    async def _existing_users_by_email(
        self, db: AsyncSession, tenant_id: str
    ) -> dict[str, User]:
        """Load the tenant directory once so sync does not issue one query/user."""
        result = await db.execute(
            select(User).where(User.tenant_id == uuid.UUID(tenant_id))
        )
        return {
            (user.email or "").lower().strip(): user
            for user in result.scalars().all()
            if user.email
        }

    async def _save_sync_state(
        self,
        db: AsyncSession,
        tenant_id: str,
        provider: str,
        *,
        status: str,
        total: int = 0,
        created: int = 0,
        updated: int = 0,
        error: str | None = None,
    ) -> None:
        from app.models.tenant_credential import TenantCredential

        result = await db.execute(
            update(TenantCredential)
            .where(
                TenantCredential.tenant_id == uuid.UUID(tenant_id),
                TenantCredential.provider == provider,
            )
            .values(
                last_user_sync_at=datetime.now(timezone.utc),
                last_user_sync_total=total,
                last_user_sync_created=created,
                last_user_sync_updated=updated,
                last_user_sync_status=status,
                last_user_sync_error=error,
            )
        )
        if result.rowcount == 0:
            logger.warning(
                "_save_sync_state: no TenantCredential row for tenant=%s provider=%s",
                tenant_id,
                provider,
            )
        await db.commit()

    async def record_sync_failure(
        self, db: AsyncSession, tenant_id: str, provider: str, error: str
    ) -> None:
        await self._save_sync_state(
            db, tenant_id, provider, status="failed", error=error
        )

    async def sync_microsoft_users(
        self,
        db: AsyncSession,
        tenant_id: str,
    ) -> dict:
        token = await get_fresh_token(db, tenant_id, "microsoft")
        if not token:
            raise RuntimeError("No Microsoft tenant-level OAuth token")
        blocked = await self._directory_sync_blocked(db, tenant_id, "microsoft")
        if blocked is not None:
            return blocked

        created = 0
        updated = 0
        skipped = 0

        async with httpx.AsyncClient() as client:
            url = f"{GRAPH_BASE}/users"
            params = {
                "$select": "id,mail,userPrincipalName,displayName,givenName,surname,jobTitle,department,accountEnabled",
                "$top": 200,
            }

            all_users = []
            while url:
                resp = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    params=params if "?" not in url else None,
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"MS Graph user sync failed: {resp.status_code}")

                data = resp.json()
                all_users.extend(data.get("value", []))
                url = data.get("@odata.nextLink", None)

            await set_tenant_context(db, tenant_id)
            users_by_email = await self._existing_users_by_email(db, tenant_id)
            workspace_mcp_default: bool | None = None

            for ms_user in all_users:
                if ms_user.get("accountEnabled") is False:
                    skipped += 1
                    continue

                email = (
                    (ms_user.get("mail") or ms_user.get("userPrincipalName") or "")
                    .lower()
                    .strip()
                )
                if not email:
                    skipped += 1
                    continue

                full_name = ms_user.get("displayName", "")
                if not full_name:
                    first = ms_user.get("givenName", "")
                    last = ms_user.get("surname", "")
                    full_name = f"{first} {last}".strip()

                user_row = users_by_email.get(email)

                if user_row:
                    if not user_row.full_name:
                        user_row.full_name = full_name
                    user_row.is_active = True
                    updated += 1
                else:
                    from app.services.workspace_mcp_access import (
                        tenant_workspace_mcp_default,
                    )

                    if workspace_mcp_default is None:
                        workspace_mcp_default = await tenant_workspace_mcp_default(
                            db, tenant_id
                        )
                    new_user = User(
                        id=uuid.uuid4(),
                        tenant_id=uuid.UUID(tenant_id),
                        email=email,
                        full_name=full_name or email.split("@")[0],
                        role="user",
                        oauth_provider="microsoft",
                        oauth_subject=ms_user.get("id"),
                        is_active=True,
                        license_active=True,
                        workspace_mcp_enabled=workspace_mcp_default,
                    )
                    db.add(new_user)
                    users_by_email[email] = new_user
                    created += 1

            await db.commit()

            await self._save_sync_state(
                db,
                tenant_id,
                "microsoft",
                status="ok",
                total=len(all_users),
                created=created,
                updated=updated,
            )

        return {
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "total": len(all_users),
        }

    async def sync_google_users(
        self,
        db: AsyncSession,
        tenant_id: str,
    ) -> dict:
        token = await get_fresh_token(db, tenant_id, "google")
        if not token:
            raise RuntimeError("No Google tenant-level OAuth token")
        blocked = await self._directory_sync_blocked(db, tenant_id, "google")
        if blocked is not None:
            return blocked

        created = 0
        updated = 0
        skipped = 0

        async with httpx.AsyncClient() as client:
            params = {
                "customer": "my_customer",
                "maxResults": 200,
                "projection": "basic",
            }

            all_users = []
            url = f"{GOOGLE_DIRECTORY_BASE}/users"
            next_token = None

            while True:
                if next_token:
                    params["pageToken"] = next_token
                resp = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                )
                if resp.status_code != 200:
                    detail = resp.text[:500] if resp.text else ""
                    if resp.status_code == 403:
                        if (
                            "insufficient" in detail.lower()
                            or "scope" in detail.lower()
                        ):
                            raise RuntimeError(
                                "Google Directory access denied: missing admin.directory.user.readonly scope. "
                                "Re-authorize Google Workspace in Admin → Integrations to grant the required scopes. "
                                f"(HTTP 403: {detail[:200]})"
                            )
                        raise RuntimeError(
                            "Google Directory access denied (HTTP 403). "
                            "Ensure the Admin SDK API is enabled in your Google Cloud Console "
                            "and that the authorizing Google account is a Workspace admin with "
                            "Directory read access. OAuth consent must include "
                            "admin.directory.user.readonly scope. "
                            "Re-authorize in Admin → Integrations."
                        )
                    if resp.status_code == 400 and "invalid input" in detail.lower():
                        raise RuntimeError(
                            "Google Directory sync failed: the connected Google account "
                            "is not a Google Workspace account (or has no directory access). "
                            "The 'my_customer' directory only exists for Workspace tenants — "
                            "personal Gmail accounts return HTTP 400 Invalid Input. "
                            "Connect a Workspace admin account in Admin → Integrations, "
                            "or disable directory sync for this tenant. "
                            f"(HTTP 400: {detail[:200]})"
                        )
                    raise RuntimeError(
                        f"Google Directory sync failed: {resp.status_code} — {detail[:200]}"
                    )

                data = resp.json()
                all_users.extend(data.get("users", []))
                next_token = data.get("nextPageToken")
                if not next_token:
                    break

            await set_tenant_context(db, tenant_id)
            users_by_email = await self._existing_users_by_email(db, tenant_id)
            workspace_mcp_default: bool | None = None

            for g_user in all_users:
                if g_user.get("suspended") is True:
                    skipped += 1
                    continue

                email = (g_user.get("primaryEmail") or "").lower().strip()
                if not email:
                    skipped += 1
                    continue

                full_name = g_user.get("name", {}).get("fullName", "")
                if not full_name:
                    given = g_user.get("name", {}).get("givenName", "")
                    family = g_user.get("name", {}).get("familyName", "")
                    full_name = f"{given} {family}".strip()

                user_row = users_by_email.get(email)

                if user_row:
                    if not user_row.full_name:
                        user_row.full_name = full_name
                    user_row.is_active = True
                    updated += 1
                else:
                    from app.services.workspace_mcp_access import (
                        tenant_workspace_mcp_default,
                    )

                    if workspace_mcp_default is None:
                        workspace_mcp_default = await tenant_workspace_mcp_default(
                            db, tenant_id
                        )
                    new_user = User(
                        id=uuid.uuid4(),
                        tenant_id=uuid.UUID(tenant_id),
                        email=email,
                        full_name=full_name or email.split("@")[0],
                        role="user",
                        oauth_provider="google",
                        oauth_subject=g_user.get("id"),
                        is_active=True,
                        license_active=True,
                        workspace_mcp_enabled=workspace_mcp_default,
                    )
                    db.add(new_user)
                    users_by_email[email] = new_user
                    created += 1

            await db.commit()

            await self._save_sync_state(
                db,
                tenant_id,
                "google",
                status="ok",
                total=len(all_users),
                created=created,
                updated=updated,
            )

        return {
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "total": len(all_users),
        }

    async def sync_all(
        self,
        db: AsyncSession,
        tenant_id: str,
    ) -> dict:
        result = {"microsoft": None, "google": None}

        try:
            result["microsoft"] = await self.sync_microsoft_users(db, tenant_id)
        except Exception as exc:
            logger.warning("Microsoft user sync failed: %s", exc)
            result["microsoft"] = {"error": str(exc)}
            if result["microsoft"].get("status") != "not_applicable":
                await self.record_sync_failure(db, tenant_id, "microsoft", str(exc))

        try:
            result["google"] = await self.sync_google_users(db, tenant_id)
        except Exception as exc:
            logger.warning("Google user sync failed: %s", exc)
            result["google"] = {"error": str(exc)}
            if result["google"].get("status") != "not_applicable":
                await self.record_sync_failure(db, tenant_id, "google", str(exc))

        return result


user_sync = UserSyncService()
