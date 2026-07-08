"""Microsoft Teams collaboration via Microsoft Graph (delegated tokens).

Reuses the existing tenant-wide Microsoft credential (``TenantCredential``
provider="microsoft") through the token vault. Phase 1 supports listing the
admin's joined teams + channels and posting messages / Adaptive Cards to a
channel. Token configuration errors are surfaced to admin endpoints; background
notification dispatch catches them and degrades to a no-op.
"""

import asyncio
import json
import logging
import uuid
from typing import Any

import httpx
from sqlalchemy import select

from app.database import async_session_maker, set_tenant_context
from app.models.tenant_credential import TenantCredential
from app.models.user_oauth_token import UserOAuthToken
from app.services.token_vault import get_fresh_token, get_fresh_user_token

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Delegated Graph scopes required for Teams features. Kept separate from the
# base MICROSOFT_ADMIN_SCOPES so existing cloud-only tenants are not marked as
# scope-deficient — admins reconsent with ``&teams=1`` to add these.
TEAMS_REQUIRED_SCOPES = (
    "Channel.ReadBasic.All "
    "ChannelMessage.Send "
    "Chat.ReadWrite "
    "Team.ReadBasic.All "
    "TeamsActivity.Send"
)
TEAMS_CHANNEL_CREATE_SCOPE = "Channel.Create"
TEAMS_CONNECT_SCOPES = f"{TEAMS_REQUIRED_SCOPES} {TEAMS_CHANNEL_CREATE_SCOPE}"


class TeamsIntegrationError(RuntimeError):
    """Raised when Teams has no scoped, usable Microsoft Graph token."""


def _missing_required_scopes(granted: str | None) -> list[str]:
    granted_set = set((granted or "").split())
    return sorted(s for s in TEAMS_REQUIRED_SCOPES.split() if s not in granted_set)


def _has_required_scopes(granted: str | None) -> bool:
    return not _missing_required_scopes(granted)


async def _get_token(tenant_id: str, user_id: str | None = None) -> str:
    """Resolve a fresh Microsoft Graph token with Teams-scoped fallback.

    A per-user token is only usable when its stored scopes satisfy the Teams
    requirement. Otherwise the tenant credential is used, provided it is also
    scoped for Teams.
    """
    try:
        tenant_uuid = uuid.UUID(str(tenant_id))
        user_uuid = uuid.UUID(str(user_id)) if user_id else None
        async with async_session_maker() as db:
            await set_tenant_context(db, str(tenant_uuid))
            if user_uuid:
                user_result = await db.execute(
                    select(UserOAuthToken).where(
                        UserOAuthToken.user_id == user_uuid,
                        UserOAuthToken.tenant_id == tenant_uuid,
                        UserOAuthToken.provider == "microsoft",
                    )
                )
                user_token = user_result.scalar_one_or_none()
                if user_token and _has_required_scopes(user_token.scopes):
                    token = await get_fresh_user_token(
                        db, tenant_id, user_id, "microsoft"
                    )
                    if token:
                        return token
                    logger.warning(
                        "Scoped Microsoft user token unavailable for tenant %s user %s; falling back to tenant credential",
                        tenant_id,
                        user_id,
                    )
                elif user_token:
                    logger.info(
                        "Microsoft user token for tenant %s user %s missing Teams scopes %s; falling back to tenant credential",
                        tenant_id,
                        user_id,
                        _missing_required_scopes(user_token.scopes),
                    )

            tenant_result = await db.execute(
                select(TenantCredential).where(
                    TenantCredential.tenant_id == tenant_uuid,
                    TenantCredential.provider == "microsoft",
                    TenantCredential.is_active,
                )
            )
            tenant_credentials = tenant_result.scalars().all()
            tenant_credential = next(
                (
                    cred
                    for cred in tenant_credentials
                    if _has_required_scopes(cred.scopes)
                ),
                None,
            )
            if not tenant_credential:
                missing = (
                    _missing_required_scopes(tenant_credentials[0].scopes)
                    if tenant_credentials
                    else list(TEAMS_REQUIRED_SCOPES.split())
                )
                raise TeamsIntegrationError(
                    "Microsoft Teams integration is missing required scopes: "
                    + ", ".join(missing)
                )

            token = await get_fresh_token(
                db,
                str(tenant_uuid),
                "microsoft",
                credential_id=str(tenant_credential.id),
            )
            if token:
                return token

            raise TeamsIntegrationError(
                "Microsoft Teams integration has required scopes but no usable token"
            )
    except TeamsIntegrationError:
        raise
    except Exception:
        logger.warning(
            "Failed to get Microsoft Teams token for tenant %s user %s",
            tenant_id,
            user_id,
            exc_info=True,
        )
        raise TeamsIntegrationError("Unable to resolve Microsoft Teams token")


async def _graph_request(
    method: str,
    path: str,
    *,
    token: str,
    json_body: dict | None = None,
    params: dict | None = None,
    max_retries: int = 3,
) -> httpx.Response | None:
    """Issue a Graph request, honoring 429 ``Retry-After`` with backoff.

    Teams messaging endpoints throttle aggressively. Returns the final response
    (which the caller inspects) or None on network failure / exhausted retries.
    """
    url = path if path.startswith("http") else f"{GRAPH_BASE}{path}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    attempt = 0
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                resp = await client.request(
                    method, url, headers=headers, json=json_body, params=params
                )
            except httpx.HTTPError as exc:
                logger.warning("Graph %s %s network error: %s", method, path, exc)
                return None

            if resp.status_code != 429:
                return resp

            attempt += 1
            if attempt > max_retries:
                logger.warning(
                    "Graph %s %s throttled (429), retries exhausted", method, path
                )
                return resp
            retry_after = resp.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 2 ** attempt
            except ValueError:
                delay = 2 ** attempt
            logger.info(
                "Graph %s %s throttled (429), retry %d/%d after %.1fs",
                method,
                path,
                attempt,
                max_retries,
                delay,
            )
            await asyncio.sleep(delay)


async def list_joined_teams(
    tenant_id: str, user_id: str | None = None
) -> list[dict[str, Any]]:
    """List the teams the consenting account belongs to (``/me/joinedTeams``)."""
    token = await _get_token(tenant_id, user_id)
    if not token:
        return []
    resp = await _graph_request("GET", "/me/joinedTeams", token=token)
    if not resp or resp.status_code != 200:
        if resp is not None:
            logger.warning(
                "list_joined_teams failed for tenant %s: %s %s",
                tenant_id,
                resp.status_code,
                resp.text[:200],
            )
        return []
    return [
        {"id": t.get("id"), "display_name": t.get("displayName")}
        for t in resp.json().get("value", [])
        if t.get("id")
    ]


async def list_channels(
    tenant_id: str, team_id: str, user_id: str | None = None
) -> list[dict[str, Any]]:
    """List channels of a team (``/teams/{team_id}/channels``)."""
    token = await _get_token(tenant_id, user_id)
    if not token:
        return []
    resp = await _graph_request("GET", f"/teams/{team_id}/channels", token=token)
    if not resp or resp.status_code != 200:
        if resp is not None:
            logger.warning(
                "list_channels failed for tenant %s team %s: %s %s",
                tenant_id,
                team_id,
                resp.status_code,
                resp.text[:200],
            )
        return []
    return [
        {
            "id": c.get("id"),
            "display_name": c.get("displayName"),
            "membership_type": c.get("membershipType"),
        }
        for c in resp.json().get("value", [])
        if c.get("id")
    ]


async def create_channel(
    tenant_id: str,
    team_id: str,
    display_name: str,
    *,
    description: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Create a standard channel in a team using Microsoft Graph."""
    name = display_name.strip()
    if not name:
        raise TeamsIntegrationError("Channel name is required")
    token = await _get_token(tenant_id, user_id)
    body = {
        "displayName": name[:50],
        "description": description or f"Clarity Legal matter channel: {name[:50]}",
        "membershipType": "standard",
    }
    resp = await _graph_request(
        "POST",
        f"/teams/{team_id}/channels",
        token=token,
        json_body=body,
    )
    if not resp or resp.status_code not in (200, 201):
        if resp is not None:
            logger.warning(
                "create_channel failed for tenant %s team %s: %s %s",
                tenant_id,
                team_id,
                resp.status_code,
                resp.text[:500],
            )
            raise TeamsIntegrationError(
                f"Microsoft Graph could not create the channel ({resp.status_code}). "
                "Reconnect Teams if Channel.Create consent is missing."
            )
        raise TeamsIntegrationError("Microsoft Graph did not return a channel response")

    payload = resp.json()
    return {
        "id": payload.get("id"),
        "display_name": payload.get("displayName") or name[:50],
        "membership_type": payload.get("membershipType") or "standard",
    }


async def send_channel_message(
    tenant_id: str,
    team_id: str,
    channel_id: str,
    *,
    html: str | None = None,
    adaptive_card: dict | None = None,
    user_id: str | None = None,
) -> bool:
    """Post a message to a channel. Supports plain HTML or an Adaptive Card.

    Returns True on success. Never raises — logs and returns False on failure.
    """
    token = await _get_token(tenant_id, user_id)
    if not token:
        return False

    if adaptive_card is not None:
        body: dict[str, Any] = {
            "body": {
                "contentType": "html",
                "content": '<attachment id="card1"></attachment>',
            },
            "attachments": [
                {
                    "id": "card1",
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": json.dumps(adaptive_card),
                }
            ],
        }
    else:
        body = {"body": {"contentType": "html", "content": html or ""}}

    resp = await _graph_request(
        "POST",
        f"/teams/{team_id}/channels/{channel_id}/messages",
        token=token,
        json_body=body,
    )
    if not resp or resp.status_code not in (200, 201):
        if resp is not None:
            logger.warning(
                "send_channel_message failed for tenant %s team %s channel %s: %s %s",
                tenant_id,
                team_id,
                channel_id,
                resp.status_code,
                resp.text[:200],
            )
        return False
    return True

def build_matter_card(
    title: str,
    matter_name: str,
    fields: dict[str, str],
    deep_link: str | None = None,
) -> dict[str, Any]:
    """Build an Adaptive Card (v1.4) summarizing a matter notification."""
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "size": "Medium",
            "weight": "Bolder",
            "text": title,
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "spacing": "None",
            "text": matter_name,
            "isSubtle": True,
            "wrap": True,
        },
    ]
    if fields:
        body.append(
            {
                "type": "FactSet",
                "facts": [
                    {"title": str(k), "value": str(v)} for k, v in fields.items()
                ],
            }
        )

    card: dict[str, Any] = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
    }
    if deep_link:
        card["actions"] = [
            {"type": "Action.OpenUrl", "title": "Open in Clarity", "url": deep_link}
        ]
    return card
