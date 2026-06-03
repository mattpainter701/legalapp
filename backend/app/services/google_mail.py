import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.token_vault import get_fresh_user_token

settings = get_settings()
logger = logging.getLogger(__name__)

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"


async def _google_get_user_token(
    db: AsyncSession, tenant_id: str, user_id: str
) -> str | None:
    return await get_fresh_user_token(db, tenant_id, user_id, "google")


async def gmail_read_mail(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    days: int = 7,
    max_results: int = 50,
) -> list[dict]:
    token = await _google_get_user_token(db, tenant_id, user_id)
    if not token:
        raise RuntimeError(f"No Google OAuth token for user {user_id}")

    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y/%m/%d")
    query = f"after:{after}"
    url = f"{GMAIL_BASE}/users/me/messages"
    params = {
        "q": query,
        "maxResults": max_results,
    }

    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient() as client:
        list_resp = await client.get(url, headers=headers, params=params)
        if list_resp.status_code != 200:
            logger.warning(
                "Gmail list failed: %s %s", list_resp.status_code, list_resp.text[:200]
            )
            raise RuntimeError(f"Gmail API list failed: {list_resp.status_code}")

        msg_ids = [m["id"] for m in list_resp.json().get("messages", [])]
        if not msg_ids:
            return []

        messages = []
        for msg_id in msg_ids:
            detail_resp = await client.get(
                f"{GMAIL_BASE}/users/me/messages/{msg_id}",
                headers=headers,
                params={
                    "format": "metadata",
                    "metadataHeaders": "From,To,Subject,Date",
                },
            )
            if detail_resp.status_code != 200:
                logger.debug(
                    "Gmail message detail failed for %s: %s",
                    msg_id,
                    detail_resp.status_code,
                )
                continue

            msg = detail_resp.json()
            payload = msg.get("payload", {})
            headers_dict = {}
            for h in payload.get("headers", []):
                headers_dict[h["name"].lower()] = h["value"]

            snippet = msg.get("snippet", "")[:2000]
            label_ids = msg.get("labelIds", [])

            messages.append(
                {
                    "id": msg.get("id"),
                    "thread_id": msg.get("threadId"),
                    "subject": headers_dict.get("subject", ""),
                    "body_preview": snippet,
                    "from": headers_dict.get("from", ""),
                    "to": headers_dict.get("to", ""),
                    "date": headers_dict.get("date", ""),
                    "is_read": "UNREAD" not in label_ids,
                    "labels": label_ids,
                    "importance": "high" if "IMPORTANT" in label_ids else "normal",
                }
            )

    return messages
