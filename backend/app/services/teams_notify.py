"""Outbound Teams notification dispatch.

Resolves the Teams channels a Clarity event should post to and sends an
Adaptive Card to each. Best-effort and fully isolated: it opens its own DB
session (so it is safe to call from request handlers and from scheduler jobs
that run under an RLS bypass), and never raises.

Target resolution:
  1. Matter-specific ``TeamsChannelLink`` rows for ``matter_id`` (always
     notified when a matter is linked to a channel).
  2. ``TeamsNotificationSetting`` rows matching ``event_type`` and enabled —
     matter-specific first, falling back to the default route (matter_id IS NULL).
"""

import logging

from sqlalchemy import select

from app.database import async_session_maker, set_tenant_context
from app.models.teams_channel_link import TeamsChannelLink
from app.models.teams_notification_setting import TeamsNotificationSetting
from app.services import teams as teams_service
from app.services.teams_gate import get_teams_status

logger = logging.getLogger(__name__)


async def notify(
    tenant_id: str,
    event_type: str,
    *,
    title: str,
    fields: dict[str, str],
    matter_id: str | None = None,
    deep_link: str | None = None,
    user_id: str | None = None,
) -> int:
    """Post an Adaptive Card for an event to all resolved channels.

    Returns the number of channels successfully posted to (0 on any failure or
    when the tenant is not Teams-enabled).
    """
    try:
        async with async_session_maker() as db:
            connected, _missing = await get_teams_status(db, tenant_id)
            if not connected:
                return 0

            await set_tenant_context(db, str(tenant_id))
            targets: dict[tuple[str, str], None] = {}

            if matter_id:
                links = await db.execute(
                    select(TeamsChannelLink).where(
                        TeamsChannelLink.tenant_id == tenant_id,
                        TeamsChannelLink.matter_id == matter_id,
                        TeamsChannelLink.is_active,
                    )
                )
                for link in links.scalars().all():
                    targets[(link.team_id, link.channel_id)] = None

            settings_rows = await db.execute(
                select(TeamsNotificationSetting).where(
                    TeamsNotificationSetting.tenant_id == tenant_id,
                    TeamsNotificationSetting.event_type == event_type,
                    TeamsNotificationSetting.is_enabled,
                )
            )
            rows = list(settings_rows.scalars().all())
            if matter_id is not None:
                matter_specific = [
                    r for r in rows if str(r.matter_id) == str(matter_id)
                ]
            else:
                matter_specific = []
            chosen = matter_specific if matter_specific else [
                r for r in rows if r.matter_id is None
            ]
            for r in chosen:
                targets[(r.team_id, r.channel_id)] = None

        if not targets:
            return 0

        # Resolve a matter label for the card without mutating caller state.
        matter_name = fields.get("matter_name") or "Matter"
        card_fields = {k: v for k, v in fields.items() if k != "matter_name"}
        card = teams_service.build_matter_card(
            title=title,
            matter_name=matter_name,
            fields=card_fields,
            deep_link=deep_link,
        )

        sent = 0
        for team_id, channel_id in targets:
            ok = await teams_service.send_channel_message(
                tenant_id,
                team_id,
                channel_id,
                adaptive_card=card,
                user_id=user_id,
            )
            if ok:
                sent += 1
        return sent
    except Exception:
        logger.warning(
            "teams_notify.notify failed for tenant %s event %s",
            tenant_id,
            event_type,
            exc_info=True,
        )
        return 0
