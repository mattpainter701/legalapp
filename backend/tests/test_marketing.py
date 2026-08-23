from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.database import get_db
from app.main import app
from app.models.marketing import MarketingDemoRequest, MarketingEvent
from app.routers import marketing as marketing_router
from app.services.email import EmailDeliveryResult


@pytest_asyncio.fixture
async def public_marketing_client(db_session, test_redis):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    previous_redis = getattr(app.state, "redis", None)
    app.state.redis = test_redis
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.state.redis = previous_redis
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_demo_request_is_stored_and_notified(public_marketing_client, db_session, monkeypatch):
    send = AsyncMock(return_value=EmailDeliveryResult.SENT)
    monkeypatch.setattr(marketing_router.email_service, "send_email", send)
    monkeypatch.setattr(marketing_router.settings, "MARKETING_LEAD_EMAIL", "support@getlawhand.com")

    response = await public_marketing_client.post(
        "/api/marketing/demo-requests",
        json={
            "name": "Ada Counsel",
            "email": "ADA@EXAMPLE.COM",
            "firm_name": "Example Legal",
            "phone": "701-555-1212",
            "team_size": "6-20",
            "message": "We need a better intake handoff.",
            "source_path": "/pricing",
            "campaign": {"utm_source": "linkedin", "ignored": "drop-me"},
            "website": "",
        },
    )

    assert response.status_code == 202
    record = (await db_session.execute(select(MarketingDemoRequest))).scalar_one()
    assert record.email == "ada@example.com"
    assert record.firm_name == "Example Legal"
    assert record.notification_status == "sent"
    assert record.campaign == {"utm_source": "linkedin"}
    assert send.await_args.args[0] == ["support@getlawhand.com"]
    assert "Example Legal" in send.await_args.args[1]


@pytest.mark.asyncio
async def test_demo_request_survives_notification_outage(public_marketing_client, db_session, monkeypatch):
    monkeypatch.setattr(
        marketing_router.email_service,
        "send_email",
        AsyncMock(return_value=EmailDeliveryResult.DISABLED),
    )

    response = await public_marketing_client.post(
        "/api/marketing/demo-requests",
        json={"name": "Grace Legal", "email": "grace@example.com", "firm_name": "Grace LLP"},
    )

    assert response.status_code == 202
    record = (await db_session.execute(select(MarketingDemoRequest))).scalar_one()
    assert record.notification_status == "disabled"


@pytest.mark.asyncio
async def test_demo_honeypot_does_not_store_or_notify(public_marketing_client, db_session, monkeypatch):
    send = AsyncMock(return_value=EmailDeliveryResult.SENT)
    monkeypatch.setattr(marketing_router.email_service, "send_email", send)

    response = await public_marketing_client.post(
        "/api/marketing/demo-requests",
        json={
            "name": "Automated Sender",
            "email": "bot@example.com",
            "firm_name": "Bot Firm",
            "website": "https://spam.example",
        },
    )

    assert response.status_code == 202
    count = await db_session.scalar(select(func.count()).select_from(MarketingDemoRequest))
    assert count == 0
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_marketing_event_is_allowlisted_and_stored(public_marketing_client, db_session):
    response = await public_marketing_client.post(
        "/api/marketing/events",
        json={
            "name": "demo_cta_clicked",
            "session_id": "d1b13b9c-8d9f-48dc-b10e-749876ebcb8a",
            "page": "/pricing",
            "properties": {"placement": "pricing", "email": "must-not-store@example.com"},
        },
    )
    assert response.status_code == 204
    event = (await db_session.execute(select(MarketingEvent))).scalar_one()
    assert event.properties == {"placement": "pricing"}

    invalid = await public_marketing_client.post(
        "/api/marketing/events",
        json={
            "name": "arbitrary_event",
            "session_id": "d1b13b9c-8d9f-48dc-b10e-749876ebcb8a",
            "page": "/",
        },
    )
    assert invalid.status_code == 422