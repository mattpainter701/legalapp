import logging
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import set_tenant_context
from app.models.user import User
from app.services.token_vault import get_fresh_token

settings = get_settings()
logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GOOGLE_DIRECTORY_BASE = "https://admin.googleapis.com/admin/directory/v1"


class UserSyncService:

    async def sync_microsoft_users(
        self,
        db: AsyncSession,
        tenant_id: str,
    ) -> dict:
        token = await get_fresh_token(db, tenant_id, "microsoft")
        if not token:
            raise RuntimeError("No Microsoft tenant-level OAuth token")

        created = 0
        updated = 0
        skipped = 0

        async with httpx.AsyncClient() as client:
            url = f"{GRAPH_BASE}/users"
            params = {
                "$select": "id,mail,userPrincipalName,displayName,givenName,surname,jobTitle,department",
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

            for ms_user in all_users:
                email = (ms_user.get("mail") or ms_user.get("userPrincipalName") or "").lower().strip()
                if not email:
                    skipped += 1
                    continue

                full_name = ms_user.get("displayName", "")
                if not full_name:
                    first = ms_user.get("givenName", "")
                    last = ms_user.get("surname", "")
                    full_name = f"{first} {last}".strip()

                existing = await db.execute(
                    select(User).where(User.email == email, User.tenant_id == tenant_id)
                )
                user_row = existing.scalar_one_or_none()

                if user_row:
                    if not user_row.full_name:
                        user_row.full_name = full_name
                    user_row.is_active = True
                    updated += 1
                else:
                    new_user = User(
                        id=uuid.uuid4(),
                        tenant_id=uuid.UUID(tenant_id),
                        email=email,
                        full_name=full_name or email.split("@")[0],
                        role="user",
                        oauth_provider="microsoft",
                        oauth_subject=ms_user.get("id"),
                        is_active=True,
                    )
                    db.add(new_user)
                    created += 1

            await db.commit()

        return {"created": created, "updated": updated, "skipped": skipped, "total": len(all_users)}

    async def sync_google_users(
        self,
        db: AsyncSession,
        tenant_id: str,
    ) -> dict:
        token = await get_fresh_token(db, tenant_id, "google")
        if not token:
            raise RuntimeError("No Google tenant-level OAuth token")

        created = 0
        updated = 0
        skipped = 0

        async with httpx.AsyncClient() as client:
            params = {
                "customer": "my_customer",
                "maxResults": 200,
                "projection": "basic",
                "query": "isSuspended=false",
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
                    raise RuntimeError(f"Google Directory sync failed: {resp.status_code}")

                data = resp.json()
                all_users.extend(data.get("users", []))
                next_token = data.get("nextPageToken")
                if not next_token:
                    break

            await set_tenant_context(db, tenant_id)

            for g_user in all_users:
                email = (g_user.get("primaryEmail") or "").lower().strip()
                if not email:
                    skipped += 1
                    continue

                full_name = g_user.get("name", {}).get("fullName", "")
                if not full_name:
                    given = g_user.get("name", {}).get("givenName", "")
                    family = g_user.get("name", {}).get("familyName", "")
                    full_name = f"{given} {family}".strip()

                existing = await db.execute(
                    select(User).where(User.email == email, User.tenant_id == tenant_id)
                )
                user_row = existing.scalar_one_or_none()

                if user_row:
                    if not user_row.full_name:
                        user_row.full_name = full_name
                    user_row.is_active = True
                    updated += 1
                else:
                    new_user = User(
                        id=uuid.uuid4(),
                        tenant_id=uuid.UUID(tenant_id),
                        email=email,
                        full_name=full_name or email.split("@")[0],
                        role="user",
                        oauth_provider="google",
                        oauth_subject=g_user.get("id"),
                        is_active=True,
                    )
                    db.add(new_user)
                    created += 1

            await db.commit()

        return {"created": created, "updated": updated, "skipped": skipped, "total": len(all_users)}

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

        try:
            result["google"] = await self.sync_google_users(db, tenant_id)
        except Exception as exc:
            logger.warning("Google user sync failed: %s", exc)
            result["google"] = {"error": str(exc)}

        return result


user_sync = UserSyncService()
