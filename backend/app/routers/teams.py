"""Microsoft Teams admin endpoints (Phase 1).

All routes are gated by ``require_teams_enabled`` — only tenants with an active
Microsoft integration whose granted scopes include the Teams scopes may use
them. Lets admins browse teams/channels, link matters to channels, configure
event→channel notification routing, and send a test Adaptive Card.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.models.plugin import Matter
from app.models.teams_channel_link import TeamsChannelLink
from app.models.teams_notification_setting import TeamsNotificationSetting
from app.schemas.teams import (
    ChannelCreateRequest,
    ChannelLinkCreate,
    ChannelLinkResponse,
    ChannelSummary,
    NotificationSettingResponse,
    NotificationSettingsUpdate,
    TeamSummary,
    TestMessageRequest,
)
from app.services import teams as teams_service
from app.services.teams_gate import require_teams_enabled

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/integrations/teams", tags=["teams"])


def _integration_error(exc: teams_service.TeamsIntegrationError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"error": "teams_token_unavailable", "message": str(exc)},
    )


async def _require_tenant_matter(
    db: AsyncSession, tenant_id: str, matter_id: str
) -> uuid.UUID:
    try:
        tenant_uuid = uuid.UUID(str(tenant_id))
        matter_uuid = uuid.UUID(matter_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid matter_id") from exc

    result = await db.execute(
        select(Matter.id).where(
            Matter.id == matter_uuid,
            Matter.tenant_id == tenant_uuid,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter_uuid


# ── Teams / channels discovery ───────────────────────────────────────────


@router.get("/teams", response_model=list[TeamSummary])
async def list_teams(request: Request, db: AsyncSession = Depends(get_db)):
    user, tenant_id = await require_teams_enabled(request, db)
    try:
        teams = await teams_service.list_joined_teams(tenant_id, user_id=str(user.id))
    except teams_service.TeamsIntegrationError as exc:
        raise _integration_error(exc) from exc
    return [TeamSummary(**t) for t in teams]


@router.get("/teams/{team_id}/channels", response_model=list[ChannelSummary])
async def list_team_channels(
    team_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user, tenant_id = await require_teams_enabled(request, db)
    try:
        channels = await teams_service.list_channels(
            tenant_id, team_id, user_id=str(user.id)
        )
    except teams_service.TeamsIntegrationError as exc:
        raise _integration_error(exc) from exc
    return [ChannelSummary(**c) for c in channels]


@router.post("/channels", response_model=ChannelSummary, status_code=201)
async def create_team_channel(
    payload: ChannelCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user, tenant_id = await require_teams_enabled(request, db)
    try:
        channel = await teams_service.create_channel(
            tenant_id,
            payload.team_id,
            payload.display_name,
            description=payload.description,
            user_id=str(user.id),
        )
    except teams_service.TeamsIntegrationError as exc:
        raise _integration_error(exc) from exc
    if not channel.get("id"):
        raise HTTPException(status_code=502, detail="teams_channel_create_failed")
    return ChannelSummary(**channel)


# ── Matter ↔ channel links ───────────────────────────────────────────────


@router.get("/links", response_model=list[ChannelLinkResponse])
async def list_links(request: Request, db: AsyncSession = Depends(get_db)):
    _user, tenant_id = await require_teams_enabled(request, db)
    await set_tenant_context(db, tenant_id)
    result = await db.execute(
        select(TeamsChannelLink).where(TeamsChannelLink.tenant_id == tenant_id)
    )
    return [ChannelLinkResponse.model_validate(r) for r in result.scalars().all()]


@router.post("/links", response_model=ChannelLinkResponse, status_code=201)
async def create_link(
    payload: ChannelLinkCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user, tenant_id = await require_teams_enabled(request, db)
    await set_tenant_context(db, tenant_id)
    matter_id = await _require_tenant_matter(db, tenant_id, payload.matter_id)

    # Idempotent: reactivate an existing link rather than violating the unique
    # (tenant_id, matter_id, channel_id) constraint.
    existing = await db.execute(
        select(TeamsChannelLink).where(
            TeamsChannelLink.tenant_id == tenant_id,
            TeamsChannelLink.matter_id == matter_id,
            TeamsChannelLink.channel_id == payload.channel_id,
        )
    )
    link = existing.scalar_one_or_none()
    if link:
        link.is_active = True
        link.team_id = payload.team_id
        link.team_display_name = payload.team_display_name
        link.channel_display_name = payload.channel_display_name
    else:
        link = TeamsChannelLink(
            tenant_id=uuid.UUID(tenant_id),
            matter_id=matter_id,
            team_id=payload.team_id,
            channel_id=payload.channel_id,
            team_display_name=payload.team_display_name,
            channel_display_name=payload.channel_display_name,
            created_by=user.id,
        )
        db.add(link)
    await db.commit()
    await db.refresh(link)
    return ChannelLinkResponse.model_validate(link)


@router.delete("/links/{link_id}", status_code=204)
async def delete_link(
    link_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    _user, tenant_id = await require_teams_enabled(request, db)
    await set_tenant_context(db, tenant_id)
    await db.execute(
        delete(TeamsChannelLink).where(
            TeamsChannelLink.tenant_id == tenant_id,
            TeamsChannelLink.id == link_id,
        )
    )
    await db.commit()
    return None


# ── Notification routing settings ────────────────────────────────────────


@router.get(
    "/notification-settings", response_model=list[NotificationSettingResponse]
)
async def get_notification_settings(
    request: Request, db: AsyncSession = Depends(get_db)
):
    _user, tenant_id = await require_teams_enabled(request, db)
    await set_tenant_context(db, tenant_id)
    result = await db.execute(
        select(TeamsNotificationSetting).where(
            TeamsNotificationSetting.tenant_id == tenant_id
        )
    )
    return [
        NotificationSettingResponse.model_validate(r) for r in result.scalars().all()
    ]


@router.put(
    "/notification-settings", response_model=list[NotificationSettingResponse]
)
async def replace_notification_settings(
    payload: NotificationSettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Replace the tenant's full notification-routing set with the supplied list."""
    user, tenant_id = await require_teams_enabled(request, db)
    await set_tenant_context(db, tenant_id)

    matter_ids: dict[str, uuid.UUID] = {}
    for item in payload.settings:
        if item.matter_id:
            matter_ids[item.matter_id] = await _require_tenant_matter(
                db, tenant_id, item.matter_id
            )

    await db.execute(
        delete(TeamsNotificationSetting).where(
            TeamsNotificationSetting.tenant_id == tenant_id
        )
    )
    created: list[TeamsNotificationSetting] = []
    for item in payload.settings:
        row = TeamsNotificationSetting(
            tenant_id=uuid.UUID(tenant_id),
            event_type=item.event_type,
            team_id=item.team_id,
            channel_id=item.channel_id,
            team_display_name=item.team_display_name,
            channel_display_name=item.channel_display_name,
            matter_id=matter_ids[item.matter_id] if item.matter_id else None,
            is_enabled=item.is_enabled,
            created_by=user.id,
        )
        db.add(row)
        created.append(row)
    await db.commit()
    for row in created:
        await db.refresh(row)
    return [NotificationSettingResponse.model_validate(r) for r in created]


# ── Test message ─────────────────────────────────────────────────────────


@router.post("/test-message")
async def send_test_message(
    payload: TestMessageRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user, tenant_id = await require_teams_enabled(request, db)
    card = teams_service.build_matter_card(
        title="Clarity Legal — Test Notification",
        matter_name=payload.matter_name or "Test Matter",
        fields={"Status": "Connected", "Source": "Admin test message"},
    )
    try:
        ok = await teams_service.send_channel_message(
            tenant_id,
            payload.team_id,
            payload.channel_id,
            adaptive_card=card,
            user_id=str(user.id),
        )
    except teams_service.TeamsIntegrationError as exc:
        raise _integration_error(exc) from exc
    if not ok:
        raise HTTPException(status_code=502, detail="teams_message_failed")
    return {"status": "sent"}
