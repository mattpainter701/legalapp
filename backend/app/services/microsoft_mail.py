import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.graph_client import graph_request
from app.services.provider_http import ProviderAuthError
from app.services.token_vault import get_fresh_token, get_fresh_user_token

settings = get_settings()
logger = logging.getLogger(__name__)


async def _ms_get_tenant_token(db: AsyncSession, tenant_id: str) -> str | None:
    return await get_fresh_token(db, tenant_id, "microsoft")


async def _ms_get_user_token(
    db: AsyncSession, tenant_id: str, user_id: str
) -> str | None:
    return await get_fresh_user_token(db, tenant_id, user_id, "microsoft")


async def ms_read_mail_user(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    days: int = 7,
    max_results: int = 50,
) -> list[dict]:
    token = await _ms_get_user_token(db, tenant_id, user_id)
    if not token:
        raise RuntimeError(f"No Microsoft OAuth token for user {user_id}")

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    # The OAuth token is delegated for the signed-in Microsoft user. The local
    # LawHand user_id is not an Entra/Graph user id, so use /me for mailbox reads.
    params = {
        "$filter": f"receivedDateTime ge {since}",
        "$top": max_results,
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,bodyPreview,from,toRecipients,receivedDateTime,isRead,importance,hasAttachments,conversationId",
    }

    resp = await graph_request("GET", "/me/messages", token=token, params=params)

    data = resp.json()
    messages = []
    for msg in data.get("value", []):
        messages.append(
            {
                "id": msg.get("id"),
                "subject": msg.get("subject", ""),
                "body_preview": (msg.get("bodyPreview") or "")[:2000],
                "from": (msg.get("from", {}) or {})
                .get("emailAddress", {})
                .get("address", ""),
                "from_name": (msg.get("from", {}) or {})
                .get("emailAddress", {})
                .get("name", ""),
                "to": [
                    r.get("emailAddress", {}).get("address", "")
                    for r in (msg.get("toRecipients") or [])
                ],
                "received": msg.get("receivedDateTime"),
                "is_read": msg.get("isRead", False),
                "importance": msg.get("importance", "normal"),
                "has_attachments": msg.get("hasAttachments", False),
                "conversation_id": msg.get("conversationId"),
            }
        )
    return messages


async def ms_read_mail_raw(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    message_id: str,
) -> bytes:
    """Fetch the full MIME (.eml) for a single message via Graph.

    ``GET /me/messages/{id}/$value`` returns the raw RFC 822 message, suitable
    for archiving as a ``.eml`` file.
    """
    token = await _ms_get_user_token(db, tenant_id, user_id)
    if not token:
        raise RuntimeError(f"No Microsoft OAuth token for user {user_id}")

    resp = await graph_request("GET", f"/me/messages/{message_id}/$value", token=token)
    return resp.content


async def ms_read_mail_tenant(
    db: AsyncSession,
    tenant_id: str,
    days: int = 7,
    max_results: int = 50,
) -> list[dict]:
    token = await _ms_get_tenant_token(db, tenant_id)
    if not token:
        raise RuntimeError(f"No tenant-level Microsoft token for {tenant_id}")

    params = {"$select": "id,userPrincipalName,mail", "$top": 50}

    all_messages = []
    users_resp = await graph_request("GET", "/users", token=token, params=params)
    users = users_resp.json().get("value", [])

    for user in users:
        uid = user["id"]
        try:
            messages = await ms_read_mail_user(
                db, tenant_id, uid, days, min(max_results, 20)
            )
            for m in messages:
                m["user_id"] = uid
                m["user_email"] = user.get("mail") or user.get("userPrincipalName", "")
            all_messages.extend(messages)
        except ProviderAuthError:
            logger.debug("Skip user %s: provider auth failed", user.get("mail", uid))
            continue
        except RuntimeError:
            logger.debug("Skip user %s: no per-user token", user.get("mail", uid))
            continue

    return all_messages
