import base64
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.google_client import gmail_request
from app.services.provider_http import ProviderError
from app.services.token_vault import get_fresh_user_token

settings = get_settings()
logger = logging.getLogger(__name__)


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
    params = {
        "q": query,
        "maxResults": max_results,
    }

    list_resp = await gmail_request(
        "GET", "/users/me/messages", token=token, params=params
    )

    msg_ids = [m["id"] for m in list_resp.json().get("messages", [])]
    if not msg_ids:
        return []

    messages = []
    for msg_id in msg_ids:
        try:
            detail_resp = await gmail_request(
                "GET",
                f"/users/me/messages/{msg_id}",
                token=token,
                params={
                    "format": "metadata",
                    "metadataHeaders": ["From", "To", "Subject", "Date"],
                },
            )
        except ProviderError as exc:
            logger.debug(
                "Gmail message detail failed for %s: %s",
                msg_id,
                exc.status_code,
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


async def gmail_read_raw(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    message_id: str,
) -> bytes:
    """Fetch the full RFC 822 message (.eml) for a single Gmail message.

    ``format=raw`` returns the message base64url-encoded in the ``raw`` field.
    """
    token = await _google_get_user_token(db, tenant_id, user_id)
    if not token:
        raise RuntimeError(f"No Google OAuth token for user {user_id}")

    resp = await gmail_request(
        "GET",
        f"/users/me/messages/{message_id}",
        token=token,
        params={"format": "raw"},
    )
    raw = resp.json().get("raw", "")
    if not raw:
        raise ProviderError(f"Gmail message {message_id} returned no raw content")
    return base64.urlsafe_b64decode(raw)
