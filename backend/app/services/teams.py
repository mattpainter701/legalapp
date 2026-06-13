"""Microsoft Teams collaboration via Microsoft Graph (delegated tokens).

Reuses the existing tenant-wide Microsoft credential (``TenantCredential``
provider="microsoft") through the token vault. Phase 1 supports listing the
admin's joined teams + channels and posting messages / Adaptive Cards to a
channel. All calls degrade gracefully (return None / [] and log) so a Teams
failure never breaks the calling request or scheduler job.
"""

import asyncio
import json
import logging
from typing import Any

import httpx

from app.database import async_session_maker
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


async def _get_token(tenant_id: str, user_id: str | None = None) -> str | None:
    """Resolve a fresh Microsoft Graph token, preferring a per-user token."""
    try:
        async with async_session_maker() as db:
            if user_id:
                token = await get_fresh_user_token(db, tenant_id, user_id, "microsoft")
                if token:
                    return token
            return await get_fresh_token(db, tenant_id, "microsoft")
    except Exception:
        logger.warning(
            "Failed to get Microsoft token for tenant %s user %s",
            tenant_id,
            user_id,
            exc_info=True,
        )
        return None


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
