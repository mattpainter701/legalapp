import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import get_db
from app.main import app
from app.models.tenant import Tenant, TenantSettings
from app.models.user import User
from app.routers import auth as auth_router
from app.services.email import EmailDeliveryResult, email_service


@pytest_asyncio.fixture
async def public_client(db_session, monkeypatch):
    monkeypatch.setattr(auth_router.settings, "PUBLIC_SIGNUP_ENABLED", True)

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_public_signup_provisions_intake_tenant(public_client, db_session):
    resp = await public_client.post(
        "/api/auth/signup/plan",
        json={
            "plan": "intake-only",
            "firm_name": "Reception Co",
            "email": "owner@reception.co",
            "password": "longenoughpw123",
            "full_name": "Owner One",
            "staff_size": 4,
            "address": "100 First Customer Way",
            "phone": "+1 701-555-0101",
        },
    )
    assert resp.status_code == 201
    user = (
        await db_session.execute(select(User).where(User.email == "owner@reception.co"))
    ).scalar_one()
    assert user.role == "admin"
    ts = (
        await db_session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == user.tenant_id)
        )
    ).scalar_one()
    assert ts.custom_config["plan"] == "intake-only"
    tenant = (
        await db_session.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    ).scalar_one()
    assert tenant.billing_tier == "intake_trial"
    assert tenant.staff_size == 4
    assert tenant.address == "100 First Customer Way"
    assert tenant.phone == "+1 701-555-0101"


@pytest.mark.asyncio
async def test_public_signup_sets_thirty_day_trial_and_no_premium(
    public_client, db_session
):
    resp = await public_client.post(
        "/api/auth/signup/plan",
        json={
            "plan": "intake-only",
            "firm_name": "Trial Co",
            "email": "owner@trial.co",
            "password": "longenoughpw123",
            "full_name": "Trial Owner",
        },
    )
    assert resp.status_code == 201

    user = (
        await db_session.execute(select(User).where(User.email == "owner@trial.co"))
    ).scalar_one()
    tenant = (
        await db_session.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    ).scalar_one()
    ts = (
        await db_session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == user.tenant_id)
        )
    ).scalar_one()

    # Premium AI is held back for the whole trial.
    assert user.premium_ai_enabled is False
    # The trial is explicit in config and enforced through expires_at.
    assert ts.custom_config["trial"] is True
    assert "trial_ends_at" in ts.custom_config
    assert tenant.expires_at is not None
    remaining = tenant.expires_at.astimezone(timezone.utc) - datetime.now(timezone.utc)
    assert timedelta(days=29, hours=23) < remaining <= timedelta(days=30, minutes=5)


@pytest.mark.asyncio
async def test_public_signup_notifies_operator(public_client, db_session, monkeypatch):
    captured: dict = {}

    async def fake_send(to, subject, html_body, text_body="", **kwargs):
        captured["to"] = list(to)
        captured["subject"] = subject
        return EmailDeliveryResult.SENT

    monkeypatch.setattr(email_service, "send_email", fake_send)

    resp = await public_client.post(
        "/api/auth/signup/plan",
        json={
            "plan": "intake-only",
            "firm_name": "Notify Co",
            "email": "owner@notify.co",
            "password": "longenoughpw123",
            "full_name": "Notify Owner",
        },
    )
    assert resp.status_code == 201
    assert captured.get("to")
    assert "trial" in captured["subject"].lower()


@pytest.mark.asyncio
async def test_public_signup_survives_notification_failure(
    public_client, db_session, monkeypatch
):
    async def boom(*args, **kwargs):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(email_service, "send_email", boom)

    resp = await public_client.post(
        "/api/auth/signup/plan",
        json={
            "plan": "intake-only",
            "firm_name": "Resilient Co",
            "email": "owner@resilient.co",
            "password": "longenoughpw123",
            "full_name": "Resilient Owner",
        },
    )
    # A notification failure must never fail the signup itself.
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_public_signup_rejects_mcp_tenant(public_client, db_session):
    resp = await public_client.post(
        "/api/auth/signup/plan",
        json={
            "plan": "mcp-only",
            "firm_name": "Research API Co",
            "email": "owner@research-api.co",
            "password": "longenoughpw123",
            "full_name": "Owner Two",
        },
    )
    assert resp.status_code == 403
    assert (
        await db_session.execute(
            select(User).where(User.email == "owner@research-api.co")
        )
    ).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_signup_rejects_non_public_plan(public_client):
    resp = await public_client.post(
        "/api/auth/signup/plan",
        json={
            "plan": "full-platform",
            "firm_name": "X",
            "email": "x@y.co",
            "password": "longenoughpw123",
            "full_name": "X",
        },
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_launch_mode_rejects_public_email_and_oauth_signup(
    public_client, db_session, monkeypatch
):
    monkeypatch.setattr(auth_router.settings, "PUBLIC_SIGNUP_ENABLED", False)

    plan_response = await public_client.post(
        "/api/auth/signup/plan",
        json={
            "plan": "intake-only",
            "firm_name": "Unapproved Firm",
            "email": "owner@unapproved.example",
            "password": "longenoughpw123",
            "full_name": "Unapproved Owner",
        },
    )
    register_response = await public_client.post(
        "/api/auth/register",
        json={
            "email": "owner2@unapproved.example",
            "password": "longenoughpw123",
            "full_name": "Unapproved Owner Two",
            "company_name": "Unapproved Firm Two",
        },
    )
    oauth_response = await public_client.get("/api/auth/google/login?signup=true")

    assert plan_response.status_code == 403
    assert register_response.status_code == 403
    assert oauth_response.status_code == 403
    assert "request access" in plan_response.json()["detail"].lower()
    assert (
        await db_session.execute(
            select(User).where(User.email.like("%@unapproved.example"))
        )
    ).scalars().all() == []
