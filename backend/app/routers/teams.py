"""Microsoft Teams admin endpoints.

Chat routes are gated by ``require_teams_enabled`` — only tenants with an
active Microsoft integration whose granted scopes include the Teams scopes may
use them. Lets admins browse teams/channels, link matters to channels,
configure event→channel notification routing, and send a test Adaptive Card.

The ``/voice`` routes configure Teams Phone call capture, which runs on
application permissions rather than the delegated token; see
``app.services.teams_voice``.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.models.communication_log import CommunicationLog
from app.models.plugin import Matter
from app.models.teams_channel_link import TeamsChannelLink
from app.models.teams_notification_setting import TeamsNotificationSetting
from app.schemas.teams import (
    ChannelCreateRequest,
    ChannelLinkCreate,
    ChannelLinkResponse,
    ChannelSummary,
    EventTypeSummary,
    NotificationSettingResponse,
    NotificationSettingsUpdate,
    TeamSummary,
    TestMessageRequest,
    VoiceSettingsUpdate,
    VoiceStatusResponse,
    VoiceSyncResponse,
    VoiceTestResponse,
)
from app.services import teams as teams_service
from app.services import teams_notify
from app.services import teams_voice
from app.services.durable_job_worker import TEAMS_VOICE_CALL_JOB
from app.services.durable_jobs import enqueue_job
from app.services.teams_gate import require_teams_enabled

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(prefix="/api/integrations/teams", tags=["teams"])


def _integration_error(exc: teams_service.TeamsIntegrationError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"error": "teams_token_unavailable", "message": str(exc)},
    )


async def _matter_names(
    db: AsyncSession, tenant_id: str, matter_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Look up display names for a set of matters in one query.

    Link and routing rows store only IDs; the admin UI needs names, and a raw
    truncated UUID is not something an admin can recognize a matter by.
    """
    ids = [m for m in matter_ids if m]
    if not ids:
        return {}
    result = await db.execute(
        select(Matter.id, Matter.matter_name, Matter.slug).where(
            Matter.tenant_id == uuid.UUID(str(tenant_id)),
            Matter.id.in_(ids),
        )
    )
    return {
        row.id: (row.matter_name or row.slug or str(row.id)) for row in result.all()
    }


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
        select(TeamsChannelLink)
        .where(TeamsChannelLink.tenant_id == tenant_id)
        .order_by(TeamsChannelLink.created_at.desc())
    )
    rows = list(result.scalars().all())
    names = await _matter_names(db, tenant_id, [r.matter_id for r in rows])
    responses = []
    for row in rows:
        payload = ChannelLinkResponse.model_validate(row)
        payload.matter_name = names.get(row.matter_id)
        responses.append(payload)
    return responses


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
    response = ChannelLinkResponse.model_validate(link)
    names = await _matter_names(db, tenant_id, [link.matter_id])
    response.matter_name = names.get(link.matter_id)
    return response


@router.delete("/links/{link_id}", status_code=204)
async def delete_link(
    link_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    _user, tenant_id = await require_teams_enabled(request, db)
    await set_tenant_context(db, tenant_id)
    try:
        link_uuid = uuid.UUID(link_id)
    except ValueError as exc:
        # Without this the raw string reached the UUID column and Postgres
        # raised, surfacing as a 500 on what is plainly a bad request.
        raise HTTPException(status_code=422, detail="Invalid link_id") from exc
    result = await db.execute(
        delete(TeamsChannelLink).where(
            TeamsChannelLink.tenant_id == tenant_id,
            TeamsChannelLink.id == link_uuid,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Link not found")
    return None


# ── Notification routing settings ────────────────────────────────────────


@router.get("/event-types", response_model=list[EventTypeSummary])
async def list_event_types(request: Request, db: AsyncSession = Depends(get_db)):
    """The events that can be routed to a channel.

    Served from the dispatcher's own catalogue so the admin UI and the code
    that actually fires notifications can never drift apart.
    """
    await require_teams_enabled(request, db)
    return [EventTypeSummary(**e) for e in teams_notify.event_type_catalogue()]


@router.get("/notification-settings", response_model=list[NotificationSettingResponse])
async def get_notification_settings(
    request: Request, db: AsyncSession = Depends(get_db)
):
    _user, tenant_id = await require_teams_enabled(request, db)
    await set_tenant_context(db, tenant_id)
    result = await db.execute(
        select(TeamsNotificationSetting)
        .where(TeamsNotificationSetting.tenant_id == tenant_id)
        .order_by(TeamsNotificationSetting.event_type)
    )
    rows = list(result.scalars().all())
    names = await _matter_names(db, tenant_id, [r.matter_id for r in rows])
    responses = []
    for row in rows:
        payload = NotificationSettingResponse.model_validate(row)
        payload.matter_name = names.get(row.matter_id) if row.matter_id else None
        responses.append(payload)
    return responses


@router.put("/notification-settings", response_model=list[NotificationSettingResponse])
async def replace_notification_settings(
    payload: NotificationSettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Replace the tenant's full notification-routing set with the supplied list."""
    user, tenant_id = await require_teams_enabled(request, db)
    await set_tenant_context(db, tenant_id)

    unknown = sorted(
        {
            item.event_type
            for item in payload.settings
            if not teams_notify.is_known_event_type(item.event_type)
        }
    )
    if unknown:
        # An unroutable event type would be saved happily and then never fire,
        # which reads to the admin as a broken integration.
        raise HTTPException(
            status_code=422,
            detail={
                "error": "teams_unknown_event_type",
                "unknown_event_types": unknown,
                "known_event_types": sorted(teams_notify.TEAMS_EVENT_TYPES),
            },
        )

    matter_ids: dict[str, uuid.UUID] = {}
    for item in payload.settings:
        if item.matter_id and item.matter_id not in matter_ids:
            matter_ids[item.matter_id] = await _require_tenant_matter(
                db, tenant_id, item.matter_id
            )

    # Collapse to the storage key before writing. Two identical routes in one
    # payload used to reach the (tenant, event, channel, matter) unique index
    # and fail the whole save with a 500.
    deduped: dict[tuple[str, str, str | None], object] = {}
    for item in payload.settings:
        deduped[(item.event_type, item.channel_id, item.matter_id)] = item

    await db.execute(
        delete(TeamsNotificationSetting).where(
            TeamsNotificationSetting.tenant_id == tenant_id
        )
    )
    created: list[TeamsNotificationSetting] = []
    for item in deduped.values():
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
    names = await _matter_names(db, tenant_id, [r.matter_id for r in created])
    responses = []
    for row in created:
        response = NotificationSettingResponse.model_validate(row)
        response.matter_name = names.get(row.matter_id) if row.matter_id else None
        responses.append(response)
    return responses


# ── Test message ─────────────────────────────────────────────────────────


@router.post("/test-message")
async def send_test_message(
    payload: TestMessageRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user, tenant_id = await require_teams_enabled(request, db)
    card = teams_service.build_matter_card(
        title="LawHand — Test Notification",
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


# ── Teams voice (Teams Phone capture) ────────────────────────────────────
#
# Voice runs on application permissions rather than the delegated Teams token,
# so these routes configure a separate credential path. They still sit behind
# ``require_teams_enabled`` — voice is an extension of a working Teams
# connection, not a way around one — except the webhook, which Microsoft calls
# unauthenticated and which proves itself with ``clientState`` instead.


def _voice_webhook_url(tenant_id: str) -> str:
    return f"{settings.BACKEND_URL}/api/integrations/teams/voice/webhook/{tenant_id}"


def _admin_consent_url(
    entra_tenant_id: str | None, client_id: str | None
) -> str | None:
    """Deep link that walks a tenant admin through granting the app role."""
    if not entra_tenant_id or not client_id:
        return None
    return (
        f"https://login.microsoftonline.com/{entra_tenant_id}/adminconsent"
        f"?client_id={client_id}"
    )


def _voice_error(exc: teams_voice.TeamsVoiceError) -> HTTPException:
    status = 409 if isinstance(exc, teams_voice.TeamsVoiceNotConfigured) else 502
    return HTTPException(
        status_code=status,
        detail={"error": "teams_voice_unavailable", "message": str(exc)},
    )


async def _voice_status_payload(
    db: AsyncSession, tenant_id: str
) -> VoiceStatusResponse:
    row = await teams_voice.get_voice_settings(db, tenant_id=tenant_id)
    try:
        credentials = await teams_voice.get_voice_app_credentials(
            db, tenant_id=tenant_id
        )
        source = credentials.source
        client_id = credentials.client_id
    except teams_voice.TeamsVoiceError:
        source = None
        client_id = None

    captured = await db.scalar(
        select(func.count(CommunicationLog.id)).where(
            CommunicationLog.tenant_id == uuid.UUID(str(tenant_id)),
            CommunicationLog.external_ref.like("teams_voice:call:%"),
        )
    )
    last_call_at = await db.scalar(
        select(func.max(CommunicationLog.occurred_at)).where(
            CommunicationLog.tenant_id == uuid.UUID(str(tenant_id)),
            CommunicationLog.external_ref.like("teams_voice:call:%"),
        )
    )

    return VoiceStatusResponse(
        feature_enabled=settings.TEAMS_FEATURE_ENABLED,
        configured=bool(row and row.entra_tenant_id),
        enabled=bool(row and row.is_enabled),
        entra_tenant_id=row.entra_tenant_id if row else None,
        app_credentials_source=source,
        required_application_permission=teams_voice.TEAMS_VOICE_APP_ROLE,
        subscription_active=bool(row and row.subscription_id),
        subscription_expires_at=row.subscription_expires_at if row else None,
        webhook_url=_voice_webhook_url(tenant_id),
        admin_consent_url=_admin_consent_url(
            row.entra_tenant_id if row else None, client_id
        ),
        last_sync_at=row.last_sync_at if row else None,
        last_sync_status=row.last_sync_status if row else None,
        last_sync_error=row.last_sync_error if row else None,
        captured_call_count=int(captured or 0),
        last_call_at=last_call_at,
    )


@router.get("/voice/status", response_model=VoiceStatusResponse)
async def voice_status(request: Request, db: AsyncSession = Depends(get_db)):
    _user, tenant_id = await require_teams_enabled(request, db)
    await set_tenant_context(db, tenant_id)
    return await _voice_status_payload(db, tenant_id)


@router.put("/voice/settings", response_model=VoiceStatusResponse)
async def update_voice_settings(
    payload: VoiceSettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user, tenant_id = await require_teams_enabled(request, db)
    await set_tenant_context(db, tenant_id)

    if payload.is_enabled and payload.entra_tenant_id is None:
        existing = await teams_voice.get_voice_settings(db, tenant_id=tenant_id)
        if not existing or not existing.entra_tenant_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "teams_voice_missing_directory",
                    "message": (
                        "Add your Microsoft Entra directory (tenant) ID before "
                        "enabling voice capture."
                    ),
                },
            )

    try:
        await teams_voice.upsert_voice_settings(
            db,
            tenant_id=tenant_id,
            entra_tenant_id=payload.entra_tenant_id,
            is_enabled=payload.is_enabled,
            configured_by_user_id=user.id,
        )
    except teams_voice.TeamsVoiceError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "teams_voice_invalid_directory", "message": str(exc)},
        ) from exc

    # Turning voice off should stop Microsoft sending us call records, not just
    # stop us storing them.
    if payload.is_enabled is False:
        try:
            await teams_voice.delete_subscription(db, tenant_id=tenant_id)
        except teams_voice.TeamsVoiceError:
            logger.warning(
                "Could not remove the Teams voice subscription for tenant %s",
                tenant_id,
                exc_info=True,
            )

    await db.commit()
    return await _voice_status_payload(db, tenant_id)


@router.post("/voice/subscription", response_model=VoiceStatusResponse)
async def create_voice_subscription(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """Create or renew the Graph change-notification subscription."""
    _user, tenant_id = await require_teams_enabled(request, db)
    await set_tenant_context(db, tenant_id)
    try:
        await teams_voice.ensure_subscription(
            db,
            tenant_id=tenant_id,
            notification_url=_voice_webhook_url(tenant_id),
        )
    except teams_voice.TeamsVoiceError as exc:
        await db.rollback()
        raise _voice_error(exc) from exc
    await db.commit()
    return await _voice_status_payload(db, tenant_id)


@router.delete("/voice/subscription", response_model=VoiceStatusResponse)
async def remove_voice_subscription(
    request: Request, db: AsyncSession = Depends(get_db)
):
    _user, tenant_id = await require_teams_enabled(request, db)
    await set_tenant_context(db, tenant_id)
    await teams_voice.delete_subscription(db, tenant_id=tenant_id)
    await db.commit()
    return await _voice_status_payload(db, tenant_id)


@router.post("/voice/test", response_model=VoiceTestResponse)
async def test_voice_connection(request: Request, db: AsyncSession = Depends(get_db)):
    """Prove the app-only credential and permission work, end to end."""
    _user, tenant_id = await require_teams_enabled(request, db)
    await set_tenant_context(db, tenant_id)
    try:
        result = await teams_voice.probe_voice_connection(db, tenant_id=tenant_id)
    except teams_voice.TeamsVoiceError as exc:
        raise _voice_error(exc) from exc
    return VoiceTestResponse(**result)


@router.post("/voice/sync", response_model=VoiceSyncResponse)
async def sync_voice_calls(
    request: Request,
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    """Run the PSTN-report reconciliation now instead of waiting for the sweep."""
    _user, tenant_id = await require_teams_enabled(request, db)
    await set_tenant_context(db, tenant_id)
    try:
        result = await teams_voice.sync_teams_voice_call_history(
            db, tenant_id=tenant_id, days=days
        )
    except teams_voice.TeamsVoiceError as exc:
        await db.rollback()
        raise _voice_error(exc) from exc
    await db.commit()
    return VoiceSyncResponse(
        imported=result.imported,
        updated=result.updated,
        skipped=result.skipped,
        days=days,
    )


@router.post("/voice/webhook/{tenant_id}")
async def voice_webhook(
    tenant_id: uuid.UUID,
    request: Request,
    validationToken: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Receive Graph call-record change notifications.

    Microsoft calls this unauthenticated, twice over:

    1. Subscription validation — a POST carrying ``validationToken`` that must
       be echoed back as ``text/plain`` within 10 seconds.
    2. Normal delivery — a batch of notifications whose ``clientState`` must
       match the per-tenant secret. Notification content is never trusted for
       call data; only the record id is kept, and the worker re-reads the call
       from Graph with our own token.
    """
    if validationToken:
        return PlainTextResponse(content=validationToken, status_code=200)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_payload")

    await set_tenant_context(db, str(tenant_id))
    row = await teams_voice.get_voice_settings(db, tenant_id=str(tenant_id))
    if not row or not row.is_enabled:
        # Accepted-and-dropped: replying non-2xx would make Graph retry, and
        # eventually drop the subscription, for a tenant that has switched the
        # feature off on purpose.
        return {"status": "ignored", "reason": "voice_disabled"}

    expected_state = teams_voice.client_state_of(row)
    notifications = body.get("value")
    if not isinstance(notifications, list) or not notifications:
        return {"status": "ignored", "reason": "empty_batch"}
    if not all(
        teams_voice.verify_client_state(
            expected_state, item.get("clientState") if isinstance(item, dict) else None
        )
        for item in notifications
    ):
        logger.warning(
            "Rejected Teams voice notification with a bad clientState for tenant %s",
            tenant_id,
        )
        raise HTTPException(status_code=401, detail="invalid_client_state")

    jobs = teams_voice.teams_voice_webhook_jobs(
        body, subscription_id=row.subscription_id
    )
    queued = 0
    for job in jobs:
        await enqueue_job(
            db,
            tenant_id=tenant_id,
            kind=TEAMS_VOICE_CALL_JOB,
            idempotency_key=job.idempotency_key,
            payload=job.payload,
        )
        queued += 1
    await db.commit()
    return {"status": "accepted", "queued": queued}
