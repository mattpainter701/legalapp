import uuid

import pytest
from sqlalchemy import select, text

from app.models.client_portal import ClientPortalInvite
from app.models.communication_log import CommunicationLog
from app.models.matter_assignment import MatterAssignment
from app.models.plugin import Matter, MatterEvent
from app.services import email as email_module


@pytest.mark.asyncio
async def test_create_matter_rebinds_tenant_context_before_post_commit_refresh(
    client, db_session, test_tenant, test_user, monkeypatch
):
    original_refresh = db_session.refresh
    checked = {}

    async def assert_scoped_matter_refresh(instance, *args, **kwargs):
        if isinstance(instance, Matter):
            current_tenant = (
                await db_session.execute(
                    text("SELECT current_setting('app.current_tenant_id', true)")
                )
            ).scalar_one()
            assert current_tenant == str(test_tenant.id)
            checked["matter_refresh"] = True
        return await original_refresh(instance, *args, **kwargs)

    monkeypatch.setattr(db_session, "refresh", assert_scoped_matter_refresh)

    resp = await client.post(
        "/api/matters",
        json={
            "matter_name": "RLS Refresh Matter",
            "description": "Created through the matter API regression path.",
            "practice_area": "family",
        },
    )

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert checked["matter_refresh"] is True
    assert data["matter_name"] == "RLS Refresh Matter"
    assert data["assignments"][0]["user_id"] == str(test_user.id)
    assert data["assignments"][0]["role"] == "lead_attorney"

    matter_id = uuid.UUID(data["id"])
    matter = await db_session.get(Matter, matter_id)
    assert matter.tenant_id == test_tenant.id
    assert matter.user_id == test_user.id

    assignment = (
        await db_session.execute(
            select(MatterAssignment).where(MatterAssignment.matter_id == matter_id)
        )
    ).scalar_one()
    assert assignment.user_id == test_user.id
    assert assignment.is_primary is True

    event = (
        await db_session.execute(
            select(MatterEvent).where(MatterEvent.matter_id == matter_id)
        )
    ).scalar_one()
    assert event.event_type == "intake"


@pytest.mark.asyncio
async def test_matter_field_options_returns_unique_firm_used_values(client):
    for payload in (
        {
            "matter_name": "Smith Family Matter",
            "matter_type": "Family Law",
            "role": "Petitioner",
            "jurisdiction": "North Dakota",
            "counterparty": "Acme Holdings",
        },
        {
            "matter_name": "Jones Family Matter",
            "matter_type": "family law",
            "role": "Respondent",
            "jurisdiction": "Minnesota",
            "counterparty": "Beta LLC",
        },
    ):
        created = await client.post("/api/matters", json=payload)
        assert created.status_code == 201, created.text

    response = await client.get("/api/matters/field-options")

    assert response.status_code == 200, response.text
    data = response.json()
    assert (
        len([value for value in data["matter_types"] if value.lower() == "family law"])
        == 1
    )
    assert data["roles"] == ["Petitioner", "Respondent"]
    assert data["jurisdictions"] == ["Minnesota", "North Dakota"]
    assert data["counterparties"] == ["Acme Holdings", "Beta LLC"]


@pytest.mark.asyncio
async def test_email_client_failure_is_http_error_but_attempt_remains_recorded(
    client, db_session, test_tenant, monkeypatch
):
    created = await client.post(
        "/api/matters",
        json={
            "matter_name": "Email Delivery Matter",
            "practice_area": "family",
        },
    )
    assert created.status_code == 201, created.text
    matter_id = created.json()["id"]
    monkeypatch.setattr(email_module.settings, "EMAIL_ENABLED", False)

    response = await client.post(
        f"/api/matters/{matter_id}/email-client",
        json={
            "to_email": "client@example.com",
            "subject": "Matter update",
            "body": "Please review this update.",
        },
    )

    assert response.status_code == 503
    assert "outbound email is unavailable" in response.json()["detail"]
    attempt = (
        await db_session.execute(
            select(CommunicationLog).where(
                CommunicationLog.tenant_id == test_tenant.id,
                CommunicationLog.matter_id == uuid.UUID(matter_id),
                CommunicationLog.channel == "email",
            )
        )
    ).scalar_one()
    assert attempt.status == "failed"
    assert attempt.subject == "Matter update"


@pytest.mark.asyncio
async def test_client_portal_invite_reports_failed_delivery_but_preserves_link(
    client, db_session, test_tenant, monkeypatch
):
    created = await client.post(
        "/api/matters",
        json={
            "matter_name": "Portal Invite Matter",
            "practice_area": "estate",
        },
    )
    assert created.status_code == 201, created.text
    matter_id = created.json()["id"]
    monkeypatch.setattr(email_module.settings, "EMAIL_ENABLED", False)

    response = await client.post(
        f"/api/matters/{matter_id}/portal/invite",
        json={"email": "portal-client@example.com"},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["email_sent"] is False
    assert "outbound email is unavailable" in payload["delivery_error"]
    assert "/portal/client/accept?token=" in payload["invite_url"]
    invite = await db_session.get(ClientPortalInvite, uuid.UUID(payload["id"]))
    assert invite is not None
    assert invite.revoked is False
