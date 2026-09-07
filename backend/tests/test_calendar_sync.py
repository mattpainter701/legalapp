import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.user_oauth_token import UserOAuthToken


def test_scheduled_event_rejects_unknown_iana_timezone():
    from app.schemas.calendar import ScheduledEventCreate

    with pytest.raises(ValueError, match="valid IANA timezone"):
        ScheduledEventCreate(
            title="Timezone validation",
            start_at="2026-09-07T08:30:00-05:00",
            end_at="2026-09-07T09:00:00-05:00",
            timezone="Central Time",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("instant", "expected_local"),
    [
        # America/Chicago changes from CST to CDT at 02:00 on this date.
        (datetime(2026, 3, 8, 13, 30, tzinfo=timezone.utc), "2026-03-08T08:30:00"),
        # It changes back to CST at 02:00 on this date.
        (datetime(2026, 11, 1, 14, 30, tzinfo=timezone.utc), "2026-11-01T08:30:00"),
    ],
)
async def test_scheduled_microsoft_event_uses_local_wall_time_for_provider(
    monkeypatch, instant, expected_local
):
    """An offset-bearing stored instant must not be sent to Graph with its offset."""
    from app.services import calendar_sync as calendar_sync_module

    captured: dict = {}

    class FakeResponse:
        status_code = 201

        def json(self):
            return {"id": "provider-event"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            captured.update(kwargs["json"])
            return FakeResponse()

    async def fake_token(*args, **kwargs):
        return "token"

    monkeypatch.setattr(calendar_sync_module, "get_fresh_user_token", fake_token)
    monkeypatch.setattr(calendar_sync_module.httpx, "AsyncClient", FakeClient)

    await calendar_sync_module.CalendarSyncService().ms_create_scheduled_event(
        None,
        "tenant-id",
        "user-id",
        subject="Timezone boundary",
        start_dt=instant,
        end_dt=instant + timedelta(minutes=30),
        timezone_name="America/Chicago",
    )

    assert captured["start"] == {
        "dateTime": expected_local,
        "timeZone": "America/Chicago",
    }
    assert (
        captured["end"]["dateTime"]
        == (datetime.fromisoformat(expected_local) + timedelta(minutes=30)).isoformat()
    )


@pytest.mark.asyncio
async def test_calendar_sync_provider_auth_failure_is_not_app_401(client, monkeypatch):
    from app.routers import calendar as calendar_router

    async def fake_ms_get_events(*args, **kwargs):
        raise ValueError(
            "No Microsoft calendar token. Please reconnect your calendar in Settings."
        )

    monkeypatch.setattr(
        calendar_router.calendar_sync, "ms_get_events", fake_ms_get_events
    )

    resp = await client.post(
        "/api/calendar/sync",
        json={"provider": "microsoft", "sync_deadlines": True},
    )

    assert resp.status_code == 424
    assert "reconnect your calendar" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_legacy_email_calendar_path_uses_calendar_sync_status(
    client, monkeypatch
):
    from app.routers import calendar as calendar_router

    async def fake_google_get_events(*args, **kwargs):
        raise ValueError(
            "Google Calendar read failed (HTTP 401). Please try again or reconnect your calendar in Settings."
        )

    monkeypatch.setattr(
        calendar_router.calendar_sync, "google_get_events", fake_google_get_events
    )

    resp = await client.post(
        "/api/email/calendar",
        json={"provider": "google", "sync_deadlines": False},
    )

    assert resp.status_code == 424
    assert "HTTP 401" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_calendar_providers_reports_missing_calendar_scopes(
    client, db_session, test_tenant, test_user
):
    row = UserOAuthToken(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        provider="microsoft",
        encrypted_access_token="placeholder",
        encrypted_refresh_token=None,
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes="offline_access User.Read Mail.Read",
    )
    db_session.add(row)
    await db_session.commit()

    resp = await client.get("/api/auth/calendar-providers")

    assert resp.status_code == 200
    data = resp.json()
    assert "microsoft" not in data["providers"]
    assert data["provider_status"]["microsoft"]["reason"] == "missing_scopes"
    assert data["provider_status"]["microsoft"]["needs_reconnect"] is True
    assert data["provider_status"]["microsoft"]["missing_scopes"] == [
        "Calendars.ReadWrite"
    ]


@pytest.mark.asyncio
async def test_scheduled_event_create_lists_in_calendar(client):
    start = datetime.now(timezone.utc) + timedelta(days=3)
    end = start + timedelta(minutes=45)

    create_resp = await client.post(
        "/api/calendar/scheduled-events",
        json={
            "title": "Client prep call",
            "description": "Prep notes",
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "timezone": "America/Chicago",
            "calendar_provider": None,
            "meeting_provider": "none",
            "attendees": ["client@example.com"],
        },
    )

    assert create_resp.status_code == 201
    created = create_resp.json()
    assert created["sync_status"] == "local"
    assert created["join_url"] is None

    events_resp = await client.get(
        "/api/calendar/events",
        params={"start": start.date().isoformat(), "end": start.date().isoformat()},
    )

    assert events_resp.status_code == 200
    events = events_resp.json()["events"]
    scheduled = [event for event in events if event["event_type"] == "scheduled_event"]
    assert len(scheduled) == 1
    assert scheduled[0]["title"] == "Client prep call"
    assert scheduled[0]["meeting_provider"] == "none"


@pytest.mark.asyncio
async def test_scheduled_event_rejects_teams_without_microsoft_calendar(client):
    start = datetime.now(timezone.utc) + timedelta(days=2)
    resp = await client.post(
        "/api/calendar/scheduled-events",
        json={
            "title": "Bad Teams event",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(minutes=30)).isoformat(),
            "calendar_provider": "google",
            "meeting_provider": "teams",
        },
    )

    assert resp.status_code == 422
    assert "Teams meetings require Microsoft Calendar" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_zoom_only_scheduled_event_can_be_created(client, monkeypatch):
    from app.routers import calendar as calendar_router

    async def fake_create_external_event(db, event, *, tenant_id, user_id):
        event.join_url = "https://zoom.us/j/123"
        event.meeting_id = "123"
        event.sync_status = "synced"
        return event

    monkeypatch.setattr(
        calendar_router, "create_external_event", fake_create_external_event
    )

    start = datetime.now(timezone.utc) + timedelta(days=4)
    resp = await client.post(
        "/api/calendar/scheduled-events",
        json={
            "title": "Zoom-only mediation check-in",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(minutes=30)).isoformat(),
            "calendar_provider": None,
            "meeting_provider": "zoom",
        },
    )

    assert resp.status_code == 201
    data = resp.json()
    assert data["calendar_provider"] is None
    assert data["meeting_provider"] == "zoom"
    assert data["join_url"] == "https://zoom.us/j/123"
