"""PostgreSQL/provider-shaped evidence for the tenant SMS lifecycle."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from jose import jwt as jose_jwt
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.database import set_tenant_context
from app.models.communication_log import CommunicationLog
from app.models.contact import Contact, Lead
from app.models.conversion_loop import LeadChannelConsent
from app.models.demo_session import DemoSession
from app.models.matter_party import MatterParty
from app.models.operator_audit import OperatorAuditLog
from app.models.plugin import Matter
from app.models.rbac import Role, UserRole
from app.models.sms import SmsMessage, SmsProviderConfig, SmsReviewItem
from app.models.task import Task, TaskAutomationRun
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.chat_action import SmsClientAction, ResolvedSmsRecipientBinding
from app.services import sms as sms_service
from app.services.demo_purge import purge_demo_tenant
from app.services.demo_registry import DEMO_TABLE_REGISTRY, SENSITIVE_NEVER_CLONE
from app.services.sms import SmsError, send_sms, twilio_signature
from app.services.task_automation import _sms_bindings_are_current
from app.services.token_vault import encrypt_token


_SETTINGS = get_settings()


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.content = b"{}"

    def json(self):
        return self._payload


class _Provider:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict] = []

    async def post(self, url, *, data, auth):
        self.calls.append({"url": url, "data": dict(data), "auth": auth})
        return await self.handler(url=url, data=data, auth=auth)


def _install_provider(monkeypatch, provider: _Provider) -> None:
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, data, auth):
            return await provider.post(url, data=data, auth=auth)

    monkeypatch.setattr(sms_service.httpx, "AsyncClient", _Client)


async def _seed_lifecycle(
    db,
    *,
    tenant: Tenant,
    user: User,
    suffix: str = "primary",
    phone: str = "+15551234567",
    webhook_secret: str = "webhook-secret-primary",
    categories: list[str] | None = None,
):
    await set_tenant_context(db, str(tenant.id))
    contact = Contact(
        tenant_id=tenant.id,
        first_name="SMS",
        last_name=suffix,
        email=f"sms-{suffix}@example.invalid",
        phone=phone,
        created_by_user_id=user.id,
    )
    db.add(contact)
    await db.flush()
    matter = Matter(
        tenant_id=tenant.id,
        user_id=user.id,
        slug=f"sms-{suffix}-{uuid.uuid4().hex[:8]}",
        matter_name=f"SMS {suffix}",
        client_contact_id=contact.id,
    )
    lead = Lead(
        tenant_id=tenant.id,
        contact_id=contact.id,
        status="qualified",
        source="website",
        created_by_user_id=user.id,
    )
    db.add_all([matter, lead])
    await db.flush()
    consent = LeadChannelConsent(
        tenant_id=tenant.id,
        lead_id=lead.id,
        email_allowed=True,
        sms_allowed=True,
        sms_status="active",
        phone_verified=True,
        mobile_e164=phone,
        consented_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        consent_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        consent_source="public_intake",
        consent_language="en",
        consent_timezone="America/Chicago",
        quiet_hours_start="23:00",
        quiet_hours_end="06:00",
        allowed_categories=categories or ["appointment", "lead_follow_up"],
        disclosure_version="sms-v3",
        source="public_intake",
    )
    party = MatterParty(
        tenant_id=tenant.id,
        matter_id=matter.id,
        contact_id=contact.id,
        role="client",
        is_primary=True,
    )
    config = SmsProviderConfig(
        tenant_id=tenant.id,
        provider="twilio",
        account_sid=f"AC{suffix}",
        encrypted_auth_token=encrypt_token(f"auth-token-{suffix}"),
        encrypted_webhook_secret=encrypt_token(webhook_secret),
        messaging_service_sid=f"MG{suffix}",
        sender_ready=True,
        is_active=True,
        compliance_snapshot={
            "ownership_model": "firm-owned",
            "consent_policy": "documented-opt-in",
            "quiet_hours_policy": "recipient-timezone",
        },
        updated_by_user_id=user.id,
    )
    db.add_all([consent, party, config])
    await db.commit()
    return SimpleNamespace(
        contact=contact,
        matter=matter,
        lead=lead,
        consent=consent,
        party=party,
        config=config,
        webhook_secret=webhook_secret,
    )


def _signed_headers(
    *, path: str, params: dict[str, str], secret: str
) -> dict[str, str]:
    return {
        "X-Twilio-Signature": twilio_signature(
            auth_token=secret,
            url=f"http://test{path}",
            params=params,
        )
    }


def _user_token(user: User) -> str:
    return jose_jwt.encode(
        {
            "sub": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": user.role,
            "email": user.email,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        _SETTINGS.SECRET_KEY,
        algorithm=_SETTINGS.ALGORITHM,
    )


@pytest.mark.asyncio
async def test_concurrent_idempotency_reserves_before_exactly_one_provider_call(
    db_session, test_engine, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="concurrency"
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def accepted(**_kwargs):
        entered.set()
        await release.wait()
        return _Response(
            201,
            {"sid": "SM-CONCURRENT", "status": "queued", "from": "+15550001111"},
        )

    provider = _Provider(accepted)
    _install_provider(monkeypatch, provider)
    maker = async_sessionmaker(test_engine, expire_on_commit=False)

    async def attempt(body="Appointment confirmed"):
        async with maker() as db:
            await set_tenant_context(db, str(test_tenant.id))
            return await send_sms(
                db,
                tenant_id=test_tenant.id,
                user_id=test_user.id,
                contact_id=seeded.contact.id,
                matter_id=seeded.matter.id,
                body=body,
                category="appointment",
                idempotency_key="sms-concurrent-key",
            )

    first = asyncio.create_task(attempt())
    await asyncio.wait_for(entered.wait(), timeout=5)
    with pytest.raises(SmsError) as concurrent:
        await attempt()
    assert concurrent.value.status_code == 409
    release.set()
    submitted = await first
    assert submitted.status == "submitted"
    assert len(provider.calls) == 1
    assert provider.calls[0] == {
        "url": "https://api.twilio.com/2010-04-01/Accounts/ACconcurrency/Messages.json",
        "data": {
            "To": "+15551234567",
            "Body": "Appointment confirmed",
            "MessagingServiceSid": "MGconcurrency",
        },
        "auth": ("ACconcurrency", "auth-token-concurrency"),
    }
    assert "auth-token" not in str(submitted.raw_provider_event)

    with pytest.raises(SmsError) as mismatch:
        await attempt(body="Different canonical request")
    assert mismatch.value.status_code == 409
    assert len(provider.calls) == 1

    await set_tenant_context(db_session, str(test_tenant.id))
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(SmsMessage)
            .where(SmsMessage.tenant_id == test_tenant.id)
        )
        == 1
    )
    communication = await db_session.scalar(
        select(CommunicationLog).where(
            CommunicationLog.tenant_id == test_tenant.id,
            CommunicationLog.channel == "sms",
        )
    )
    assert communication.status == "submitted"


@pytest.mark.asyncio
async def test_signed_status_webhook_is_tenant_bound_and_never_regresses_truth(
    db_session, client, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="status"
    )

    async def accepted(**_kwargs):
        return _Response(
            201, {"sid": "SM-STATUS", "status": "queued", "from": "+15550001111"}
        )

    provider = _Provider(accepted)
    _install_provider(monkeypatch, provider)
    message = await send_sms(
        db_session,
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        contact_id=seeded.contact.id,
        matter_id=seeded.matter.id,
        body="Status lifecycle",
        category="appointment",
        idempotency_key="sms-status-key",
    )
    task = Task(
        tenant_id=test_tenant.id,
        title="Approved SMS",
        matter_id=seeded.matter.id,
        contact_id=seeded.contact.id,
        status="in_progress",
        created_by_user_id=test_user.id,
    )
    db_session.add(task)
    await db_session.flush()
    run = TaskAutomationRun(
        tenant_id=test_tenant.id,
        task_id=task.id,
        action_type="sms_client",
        idempotency_key="approved-sms-run",
        status="submitted",
        provider="twilio",
        provider_message_id=message.provider_message_id,
        delivery_certainty="provider_accepted",
    )
    db_session.add(run)
    await db_session.commit()

    path = f"/api/sms/webhooks/{test_tenant.id}/status"

    async def callback(status: str, *, signature_secret=seeded.webhook_secret):
        params = {"MessageSid": "SM-STATUS", "MessageStatus": status}
        return await client.post(
            path,
            data=params,
            headers=_signed_headers(path=path, params=params, secret=signature_secret),
        )

    unknown = await callback("invented")
    assert unknown.status_code == 200
    assert unknown.json()["provider_status"] == "queued"
    assert (await callback("sent")).json()["provider_status"] == "sent"
    delivered = await callback("delivered")
    assert delivered.status_code == 200
    assert delivered.json()["status"] == "delivered"
    for regressive in ("sent", "failed", "queued"):
        replay = await callback(regressive)
        assert replay.json()["provider_status"] == "delivered"
        assert replay.json()["status"] == "delivered"
    assert (await callback("delivered")).json() == delivered.json()

    await db_session.refresh(run)
    assert run.status == "sent"
    assert run.delivery_certainty == "confirmed_sent"
    await db_session.refresh(message)
    communication = await db_session.get(CommunicationLog, message.communication_log_id)
    assert communication.status == "delivered"

    params = {"MessageSid": "SM-STATUS", "MessageStatus": "failed"}
    invalid = await client.post(
        path, data=params, headers={"X-Twilio-Signature": "bad"}
    )
    assert invalid.status_code == 401

    other = Tenant(
        name="Other SMS tenant",
        domain=f"other-sms-{uuid.uuid4().hex}.invalid",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(other)
    await db_session.flush()
    db_session.add(
        SmsProviderConfig(
            tenant_id=other.id,
            provider="twilio",
            encrypted_webhook_secret=encrypt_token("different-tenant-secret"),
        )
    )
    await db_session.commit()
    other_path = f"/api/sms/webhooks/{other.id}/status"
    cross_tenant = await client.post(
        other_path,
        data=params,
        headers=_signed_headers(
            path=other_path, params=params, secret=seeded.webhook_secret
        ),
    )
    assert cross_tenant.status_code == 401


@pytest.mark.asyncio
async def test_stop_start_help_replay_and_ambiguous_inbound_review_queue(
    db_session, client, test_tenant, test_user
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="inbound"
    )
    path = f"/api/sms/webhooks/{test_tenant.id}/inbound"

    async def inbound(sid: str, body: str):
        params = {
            "MessageSid": sid,
            "From": seeded.contact.phone,
            "To": "+15550001111",
            "Body": body,
        }
        return await client.post(
            path,
            data=params,
            headers=_signed_headers(
                path=path, params=params, secret=seeded.webhook_secret
            ),
        )

    stopped = await inbound("SM-IN-STOP", "STOP")
    assert stopped.status_code == 200
    assert (await inbound("SM-IN-STOP", "STOP")).json() == stopped.json()
    await db_session.refresh(seeded.consent)
    assert seeded.consent.sms_status == "opted_out"
    assert seeded.consent.sms_allowed is False
    assert seeded.consent.revoked_at is not None
    with pytest.raises(SmsError) as opted_out:
        await send_sms(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            contact_id=seeded.contact.id,
            matter_id=seeded.matter.id,
            body="Must remain blocked",
            category="appointment",
            idempotency_key="sms-after-stop",
        )
    assert opted_out.value.status_code == 403

    started = await inbound("SM-IN-START", "START")
    assert started.status_code == 200
    await db_session.refresh(seeded.consent)
    assert seeded.consent.sms_status == "active"
    assert seeded.consent.sms_allowed is True
    assert seeded.consent.consent_source == "provider_inbound_start"
    restarted_at = seeded.consent.consented_at
    assert (await inbound("SM-IN-HELP", "HELP")).status_code == 200
    await db_session.refresh(seeded.consent)
    assert seeded.consent.consented_at == restarted_at

    second_matter = Matter(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"ambiguous-{uuid.uuid4().hex[:8]}",
        matter_name="Second active matter",
        client_contact_id=seeded.contact.id,
    )
    db_session.add(second_matter)
    await db_session.commit()
    ambiguous = await inbound("SM-IN-REVIEW", "Can someone call me?")
    assert ambiguous.status_code == 200
    assert ambiguous.json()["status"] == "review_required"
    review = await db_session.scalar(
        select(SmsReviewItem).where(SmsReviewItem.tenant_id == test_tenant.id)
    )
    assert review.reason == "ambiguous_inbound_route"
    assert set(review.candidate_matter_ids) == {
        str(seeded.matter.id),
        str(second_matter.id),
    }
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(SmsMessage)
            .where(SmsMessage.provider_message_id == "SM-IN-STOP")
        )
        == 1
    )


@pytest.mark.asyncio
async def test_approval_recheck_rejects_category_and_multiple_lead_consent_conflicts(
    db_session, test_tenant, test_user
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="approval"
    )
    task = SimpleNamespace(tenant_id=test_tenant.id, matter_id=seeded.matter.id)

    def action(category: str):
        return SmsClientAction(
            type="sms_client",
            recipient_bindings=[
                ResolvedSmsRecipientBinding(
                    party_id=seeded.party.id,
                    contact_id=seeded.contact.id,
                    phone=seeded.contact.phone,
                )
            ],
            body="Review this reminder",
            category=category,
            matter_id=seeded.matter.id,
            idempotency_key=f"approval-{category}",
        )

    assert await _sms_bindings_are_current(db_session, task, action("appointment"))
    assert not await _sms_bindings_are_current(db_session, task, action("billing"))

    second_lead = Lead(
        tenant_id=test_tenant.id,
        contact_id=seeded.contact.id,
        status="qualified",
        source="referral",
        created_by_user_id=test_user.id,
    )
    db_session.add(second_lead)
    await db_session.flush()
    db_session.add(
        LeadChannelConsent(
            tenant_id=test_tenant.id,
            lead_id=second_lead.id,
            sms_allowed=False,
            sms_status="opted_out",
            phone_verified=True,
            mobile_e164=seeded.contact.phone,
            consented_at=datetime.now(timezone.utc) - timedelta(days=1),
            consent_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            consent_source="public_intake",
            disclosure_version="sms-v2",
            allowed_categories=["appointment"],
            revoked_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()
    assert not await _sms_bindings_are_current(db_session, task, action("appointment"))
    with pytest.raises(SmsError) as ambiguous:
        await send_sms(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            contact_id=seeded.contact.id,
            matter_id=seeded.matter.id,
            body="Must fail closed",
            category="appointment",
            idempotency_key="ambiguous-consent",
        )
    assert ambiguous.value.status_code == 403


@pytest.mark.asyncio
async def test_provider_failures_are_durable_without_fake_delivery(
    db_session, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="failure"
    )

    unrelated = Matter(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"unrelated-{uuid.uuid4().hex[:8]}",
        matter_name="Unrelated matter",
    )
    db_session.add(unrelated)
    await db_session.commit()
    with pytest.raises(SmsError) as misbound:
        await send_sms(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            contact_id=seeded.contact.id,
            matter_id=unrelated.id,
            body="Must not attach to the wrong matter",
            category="appointment",
            idempotency_key="wrong-matter-binding",
        )
    assert misbound.value.status_code == 403

    seeded.config.is_active = False
    await db_session.commit()
    with pytest.raises(SmsError) as unconfigured:
        await send_sms(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            contact_id=seeded.contact.id,
            matter_id=seeded.matter.id,
            body="No configured provider",
            category="appointment",
            idempotency_key="provider-not-configured",
        )
    assert unconfigured.value.status_code == 503
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(SmsMessage)
            .where(SmsMessage.idempotency_key == "provider-not-configured")
        )
        == 0
    )
    seeded.config.is_active = True
    await db_session.commit()

    async def rejected(**_kwargs):
        return _Response(400, {"status": "failed", "code": 21610})

    rejected_provider = _Provider(rejected)
    _install_provider(monkeypatch, rejected_provider)
    with pytest.raises(SmsError) as failure:
        await send_sms(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            contact_id=seeded.contact.id,
            matter_id=seeded.matter.id,
            body="Provider rejects",
            category="appointment",
            idempotency_key="provider-rejected",
        )
    assert failure.value.status_code == 503
    rejected_row = await db_session.scalar(
        select(SmsMessage).where(SmsMessage.idempotency_key == "provider-rejected")
    )
    assert rejected_row.status == "provider_failed"
    rejected_log = await db_session.get(
        CommunicationLog, rejected_row.communication_log_id
    )
    assert rejected_log.status == "failed"

    async def uncertain(**_kwargs):
        raise TimeoutError("provider response lost")

    uncertain_provider = _Provider(uncertain)
    _install_provider(monkeypatch, uncertain_provider)
    with pytest.raises(SmsError) as unknown:
        await send_sms(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            contact_id=seeded.contact.id,
            matter_id=seeded.matter.id,
            body="Provider outcome unknown",
            category="appointment",
            idempotency_key="provider-unknown",
        )
    assert unknown.value.status_code == 503
    unknown_row = await db_session.scalar(
        select(SmsMessage).where(SmsMessage.idempotency_key == "provider-unknown")
    )
    assert unknown_row.status == "provider_unknown"
    unknown_log = await db_session.get(
        CommunicationLog, unknown_row.communication_log_id
    )
    assert unknown_log.status == "unknown"
    with pytest.raises(SmsError) as replay:
        await send_sms(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            contact_id=seeded.contact.id,
            matter_id=seeded.matter.id,
            body="Provider outcome unknown",
            category="appointment",
            idempotency_key="provider-unknown",
        )
    assert replay.value.status_code == 409
    assert len(uncertain_provider.calls) == 1


@pytest.mark.asyncio
async def test_composite_fk_and_runtime_rls_reject_cross_tenant_sms_links(
    db_session, test_tenant, test_user
):
    primary = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="tenant-a"
    )
    other_tenant = Tenant(
        name="Tenant B",
        domain=f"tenant-b-{uuid.uuid4().hex}.invalid",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(other_tenant)
    await db_session.flush()
    other_user = User(
        tenant_id=other_tenant.id,
        email=f"tenant-b-{uuid.uuid4().hex}@example.invalid",
        full_name="Tenant B user",
        role="admin",
        oauth_provider="fixture",
        oauth_subject=uuid.uuid4().hex,
    )
    db_session.add(other_user)
    await db_session.commit()
    other = await _seed_lifecycle(
        db_session, tenant=other_tenant, user=other_user, suffix="tenant-b"
    )

    await set_tenant_context(db_session, str(test_tenant.id))
    db_session.add(
        SmsMessage(
            tenant_id=test_tenant.id,
            contact_id=other.contact.id,
            matter_id=primary.matter.id,
            idempotency_key="cross-tenant-contact",
            request_digest="a" * 64,
            direction="outbound",
            body="Must fail",
            category="appointment",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()

    runtime_url = os.getenv("RLS_TEST_DATABASE_URL")
    if not runtime_url:
        pytest.skip("RLS_TEST_DATABASE_URL is required for runtime-role rehearsal")
    runtime_engine = create_async_engine(runtime_url, pool_pre_ping=True)
    maker = async_sessionmaker(runtime_engine, expire_on_commit=False)
    try:
        async with maker() as runtime_db:
            role = (
                await runtime_db.execute(
                    text(
                        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user"
                    )
                )
            ).one()
            assert role == (False, False)
            await set_tenant_context(runtime_db, str(test_tenant.id))
            assert (
                await runtime_db.scalar(
                    select(func.count()).select_from(SmsProviderConfig)
                )
                == 1
            )
            await runtime_db.rollback()
            await set_tenant_context(runtime_db, str(other_tenant.id))
            assert (
                await runtime_db.scalar(
                    select(func.count()).select_from(SmsProviderConfig)
                )
                == 1
            )
            with pytest.raises(Exception, match="row-level security"):
                await runtime_db.execute(
                    text(
                        """
                        INSERT INTO sms_messages
                          (tenant_id, idempotency_key, request_digest, direction, body, category)
                        VALUES (:tenant_id, 'rls-cross-tenant', :digest, 'outbound',
                                'blocked', 'appointment')
                        """
                    ),
                    {"tenant_id": str(test_tenant.id), "digest": "b" * 64},
                )
    finally:
        await runtime_engine.dispose()


@pytest.mark.asyncio
async def test_expired_demo_purge_removes_sms_secrets_content_and_review_but_keeps_audit(
    db_session, tmp_path, monkeypatch
):
    from app.services import demo_purge

    sms_tables = {"sms_provider_configs", "sms_messages", "sms_review_items"}
    assert sms_tables <= SENSITIVE_NEVER_CLONE
    assert all(not DEMO_TABLE_REGISTRY[name].clone for name in sms_tables)
    monkeypatch.setattr(demo_purge.get_settings(), "UPLOAD_DIR", str(tmp_path))

    fixture = Tenant(
        name="SMS fixture",
        domain=f"sms-fixture-{uuid.uuid4().hex}.invalid",
        billing_tier="fixture",
    )
    demo = Tenant(
        name="Expired SMS demo",
        domain=f"sms-{uuid.uuid4().hex}.demo.invalid",
        billing_tier="demo",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    db_session.add_all([fixture, demo])
    await db_session.flush()
    session = DemoSession(
        tenant_id=demo.id,
        fixture_tenant_id=fixture.id,
        fixture_version="sms-purge-v1",
        prospect_name="SMS prospect",
        prospect_email="sms-prospect@example.invalid",
        status="expired",
        quota=20,
        expires_at=demo.expires_at,
    )
    config = SmsProviderConfig(
        tenant_id=demo.id,
        provider="twilio",
        account_sid="AC-DEMO-SECRET",
        encrypted_auth_token=encrypt_token("demo-auth-token"),
        encrypted_webhook_secret=encrypt_token("demo-webhook-secret"),
        messaging_service_sid="MG-DEMO",
        sender_ready=True,
        is_active=True,
    )
    message = SmsMessage(
        tenant_id=demo.id,
        idempotency_key="demo-sensitive-message",
        request_digest="c" * 64,
        provider_message_id="SM-DEMO-SENSITIVE",
        direction="inbound",
        status="review_required",
        from_number="+15559990000",
        to_number="+15558880000",
        body="Sensitive prospective-client message",
        category="customer_reply",
    )
    db_session.add_all([session, config, message])
    await db_session.flush()
    db_session.add(
        SmsReviewItem(
            tenant_id=demo.id,
            sms_message_id=message.id,
            reason="unmatched_inbound_route",
        )
    )
    await db_session.commit()

    deleted = await purge_demo_tenant(db_session, demo.id)

    assert {name: deleted[name] for name in sms_tables} == {
        "sms_provider_configs": 1,
        "sms_messages": 1,
        "sms_review_items": 1,
    }
    for table in sms_tables:
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant_id"),
                {"tenant_id": str(demo.id)},
            )
            == 0
        )
    audit = await db_session.scalar(
        select(OperatorAuditLog).where(
            OperatorAuditLog.action == "demo.session.purged",
            OperatorAuditLog.resource_id == str(session.id),
        )
    )
    assert audit.metadata_json["tenant_id"] == str(demo.id)
    assert audit.metadata_json["deleted_rows"] >= 3


@pytest.mark.asyncio
async def test_admin_config_response_never_returns_provider_credentials(
    db_session, client, test_tenant, test_user
):
    await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="credential-response"
    )
    response = await client.put(
        "/api/sms/config",
        json={
            "account_sid": "AC-ROTATED",
            "auth_token": "rotated-auth-token",
            "webhook_secret": "rotated-webhook-secret",
            "messaging_service_sid": "MG-ROTATED",
            "sender_ready": True,
            "is_active": True,
            "compliance_snapshot": {
                "ownership_model": "firm-owned",
                "consent_policy": "documented-opt-in",
                "quiet_hours_policy": "recipient-timezone",
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    serialized = response.text
    assert "auth_token" not in payload
    assert "webhook_secret" not in payload
    assert "encrypted_" not in serialized
    assert "auth-token-credential-response" not in serialized
    assert "webhook-secret-primary" not in serialized
    assert "rotated-auth-token" not in serialized
    assert "rotated-webhook-secret" not in serialized
    audit = await db_session.scalar(
        select(OperatorAuditLog).where(
            OperatorAuditLog.action == "sms.provider_config.updated"
        )
    )
    assert audit.actor_type == "tenant_user"
    assert audit.actor_id == str(test_user.id)
    assert audit.metadata_json == {
        "tenant_id": str(test_tenant.id),
        "provider": "twilio",
        "sender_ready": True,
        "is_active": True,
        "ownership_model": "firm-owned",
    }


@pytest.mark.asyncio
async def test_custom_role_capability_controls_staff_sms_send(
    db_session, client, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="rbac"
    )
    allowed = User(
        tenant_id=test_tenant.id,
        email=f"sms-allowed-{uuid.uuid4().hex}@example.invalid",
        full_name="SMS allowed",
        role="user",
    )
    denied = User(
        tenant_id=test_tenant.id,
        email=f"sms-denied-{uuid.uuid4().hex}@example.invalid",
        full_name="SMS denied",
        role="user",
    )
    role = Role(
        tenant_id=test_tenant.id,
        name=f"SMS case staff {uuid.uuid4().hex[:8]}",
        capabilities=["manage_matters"],
    )
    db_session.add_all([allowed, denied, role])
    await db_session.flush()
    db_session.add(
        UserRole(
            tenant_id=test_tenant.id,
            user_id=allowed.id,
            role_id=role.id,
            source="manual",
        )
    )
    await db_session.commit()

    async def accepted(**_kwargs):
        return _Response(201, {"sid": "SM-RBAC", "status": "queued"})

    provider = _Provider(accepted)
    _install_provider(monkeypatch, provider)
    payload = {
        "contact_id": str(seeded.contact.id),
        "matter_id": str(seeded.matter.id),
        "body": "Permission-bound reminder",
        "category": "appointment",
        "idempotency_key": "sms-rbac-send",
    }
    forbidden = await client.post(
        "/api/sms/send",
        json=payload,
        headers={"Authorization": f"Bearer {_user_token(denied)}"},
    )
    assert forbidden.status_code == 403
    assert len(provider.calls) == 0
    permitted = await client.post(
        "/api/sms/send",
        json=payload,
        headers={"Authorization": f"Bearer {_user_token(allowed)}"},
    )
    assert permitted.status_code == 200
    assert permitted.json()["status"] == "submitted"
    assert len(provider.calls) == 1
