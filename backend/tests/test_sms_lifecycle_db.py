"""PostgreSQL/provider-shaped evidence for the tenant SMS lifecycle."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from jose import jwt as jose_jwt
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.routers.sms as sms_router
import app.routers.conversion_loop as conversion_router
import app.services.chat_tools.handlers as chat_handlers

from app.config import get_settings
from app.database import set_tenant_context
from app.models.communication_log import CommunicationLog
from app.models.contact import Contact, Lead
from app.models.conversion_loop import LeadChannelConsent, SmsConsentEvent
from app.models.demo_session import DemoSession
from app.models.durable_job import DurableJob
from app.models.matter_party import MatterParty
from app.models.matter_assignment import MatterAssignment
from app.models.operator_audit import OperatorAuditLog
from app.models.plugin import Matter
from app.models.rbac import Role, UserRole
from app.models.sms import (
    SmsMessage,
    SmsNumberSuppression,
    SmsNumberSuppressionEvent,
    SmsProviderConfig,
    SmsReviewItem,
)
from app.models.task import Task, TaskAutomationRun
from app.models.tenant import Tenant, TenantSettings
from app.models.user import User
from app.schemas.chat_action import (
    ResolvedSmsRecipientBinding,
    SmsClientAction,
    SmsConsentEvidenceBinding,
)
from app.services import sms as sms_service
from app.services import task_automation
from app.services.chat_tools import ChatToolError, resolve_tool
from app.services.chat_tools.handlers import ChatToolContext
from app.services.demo_purge import purge_demo_tenant
from app.services.demo_registry import DEMO_TABLE_REGISTRY, SENSITIVE_NEVER_CLONE
from app.services.sms import (
    SmsError,
    apply_compliance_keyword,
    lock_sms_number_suppression,
    mark_stale_sms_dispatches_for_reconciliation,
    send_sms,
    twilio_signature,
)
from app.services.task_automation import (
    ActionApprovalConflict,
    _action_sources_are_current,
    _run_sms_client,
    _sms_bindings_are_current,
    action_payload_sha256,
    enqueue_durable_automation,
)
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
    def __init__(self, handler, *, lookup_handler=None):
        self.handler = handler
        self.lookup_handler = lookup_handler
        self.calls: list[dict] = []
        self.lookup_calls: list[dict] = []

    async def post(self, url, *, data, auth):
        self.calls.append({"url": url, "data": dict(data), "auth": auth})
        return await self.handler(url=url, data=data, auth=auth)

    async def get(self, url, *, auth):
        self.lookup_calls.append({"url": url, "auth": auth})
        if self.lookup_handler is None:
            raise AssertionError("provider lookup was not expected")
        return await self.lookup_handler(url=url, auth=auth)


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

        async def get(self, url, *, auth):
            return await provider.get(url, auth=auth)

    monkeypatch.setattr(sms_service.httpx, "AsyncClient", _Client)


def _consent_evidence(seeded, *, categories: list[str] | None = None):
    values = {
        "consent_id": seeded.consent.id,
        "contact_id": seeded.contact.id,
        "mobile_e164": seeded.consent.mobile_e164,
        "phone_verified": seeded.consent.phone_verified,
        "consent_source": seeded.consent.consent_source,
        "disclosure_version": seeded.consent.disclosure_version,
        "consented_at": seeded.consent.consented_at,
        "consent_expires_at": seeded.consent.consent_expires_at,
        "consent_timezone": seeded.consent.consent_timezone,
        "quiet_hours_start": seeded.consent.quiet_hours_start,
        "quiet_hours_end": seeded.consent.quiet_hours_end,
        "allowed_categories": categories or seeded.consent.allowed_categories,
    }
    provisional = SmsConsentEvidenceBinding.model_construct(
        **values, evidence_sha256=""
    )
    return SmsConsentEvidenceBinding(**values, evidence_sha256=provisional.digest())


async def _seed_lifecycle(
    db,
    *,
    tenant: Tenant,
    user: User,
    suffix: str = "primary",
    phone: str = "+15551234567",
    auth_token: str | None = None,
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
    provider_token = auth_token or f"auth-token-{suffix}"
    config = SmsProviderConfig(
        tenant_id=tenant.id,
        provider="twilio",
        account_sid=f"AC{suffix}",
        encrypted_auth_token=encrypt_token(provider_token),
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
        auth_token=provider_token,
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

    seeded.consent.sms_allowed = False
    seeded.consent.sms_status = "opted_out"
    seeded.consent.sms_revoked_at = datetime.now(timezone.utc)
    seeded.config.is_active = False
    await db_session.commit()
    replayed = await attempt()
    assert replayed.id == submitted.id
    assert replayed.status == "submitted"
    assert len(provider.calls) == 1

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
        action_snapshot={
            "type": "sms_client",
            "idempotency_key": "sms-status-key",
        },
        status="sending",
        provider="twilio",
        delivery_certainty="outcome_unknown",
    )
    db_session.add(run)
    await db_session.commit()

    path = f"/api/sms/webhooks/{test_tenant.id}/status"

    async def callback(status: str, *, signature_secret=seeded.auth_token):
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
    assert run.sms_message_id == message.id
    await db_session.refresh(message)
    communication = await db_session.get(CommunicationLog, message.communication_log_id)
    assert communication.status == "delivered"

    params = {"MessageSid": "SM-STATUS", "MessageStatus": "failed"}
    invalid = await client.post(
        path, data=params, headers={"X-Twilio-Signature": "bad"}
    )
    assert invalid.status_code == 401
    foreign_account_params = {
        **params,
        "AccountSid": "AC-FOREIGN-ACCOUNT",
    }
    foreign_account = await client.post(
        path,
        data=foreign_account_params,
        headers=_signed_headers(
            path=path,
            params=foreign_account_params,
            secret=seeded.auth_token,
        ),
    )
    assert foreign_account.status_code == 401

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
            encrypted_auth_token=encrypt_token("different-tenant-auth-token"),
        )
    )
    await db_session.commit()
    other_path = f"/api/sms/webhooks/{other.id}/status"
    cross_tenant = await client.post(
        other_path,
        data=params,
        headers=_signed_headers(
            path=other_path, params=params, secret=seeded.auth_token
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

    second_matter = Matter(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"ambiguous-{uuid.uuid4().hex[:8]}",
        matter_name="Second active matter",
        client_contact_id=seeded.contact.id,
    )
    db_session.add(second_matter)
    await db_session.commit()

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
            headers=_signed_headers(path=path, params=params, secret=seeded.auth_token),
        )

    stopped = await inbound("SM-IN-STOP", "STOP")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "review_required"
    assert (await inbound("SM-IN-STOP", "STOP")).json() == stopped.json()
    await db_session.refresh(seeded.consent)
    assert seeded.consent.sms_status == "opted_out"
    assert seeded.consent.sms_allowed is False
    assert seeded.consent.sms_revoked_at is not None
    assert seeded.consent.revoked_at is None
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

    seeded.consent.quiet_hours_start = "07:00"
    seeded.consent.quiet_hours_end = "07:00"
    await db_session.commit()
    blocked_start = await inbound("SM-IN-START-BLOCKED", "START")
    assert blocked_start.status_code == 200
    await db_session.refresh(seeded.consent)
    assert seeded.consent.sms_status == "blocked"
    assert seeded.consent.sms_allowed is False
    assert seeded.consent.sms_revoked_at is not None

    seeded.consent.quiet_hours_start = "23:00"
    seeded.consent.quiet_hours_end = "06:00"
    await db_session.commit()

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
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(CommunicationLog)
            .where(
                CommunicationLog.tenant_id == test_tenant.id,
                CommunicationLog.external_ref == "sms:SM-IN-STOP",
            )
        )
        == 0
    )
    consent_events = list(
        (
            await db_session.scalars(
                select(SmsConsentEvent).where(
                    SmsConsentEvent.tenant_id == test_tenant.id,
                    SmsConsentEvent.consent_id == seeded.consent.id,
                )
            )
        ).all()
    )
    assert [event.action for event in consent_events] == [
        "provider_stop",
        "provider_start_blocked",
        "provider_start",
    ]


@pytest.mark.asyncio
async def test_unmatched_stop_is_durable_before_later_identity_and_tenant_scoped(
    db_session, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="unmatched-stop"
    )
    stopped_number = "+15559876543"
    result = await apply_compliance_keyword(
        db_session,
        tenant_id=test_tenant.id,
        from_number=stopped_number,
        keyword="STOP",
        provider_message_id="SM-UNMATCHED-STOP",
    )
    await db_session.commit()
    assert result == {
        "keyword": "STOP",
        "matched_contact_ids": [],
        "matched_consent_count": 0,
        "applied": True,
        "number_suppressed": True,
    }
    suppression = await db_session.scalar(
        select(SmsNumberSuppression).where(
            SmsNumberSuppression.tenant_id == test_tenant.id,
            SmsNumberSuppression.mobile_e164 == stopped_number,
        )
    )
    event = await db_session.scalar(
        select(SmsNumberSuppressionEvent).where(
            SmsNumberSuppressionEvent.tenant_id == test_tenant.id,
            SmsNumberSuppressionEvent.suppression_id == suppression.id,
        )
    )
    assert event.action == "provider_stop"
    assert event.provider_message_id == "SM-UNMATCHED-STOP"

    blocked_start = await apply_compliance_keyword(
        db_session,
        tenant_id=test_tenant.id,
        from_number=stopped_number,
        keyword="START",
        provider_message_id="SM-UNMATCHED-START",
    )
    await db_session.commit()
    assert blocked_start["applied"] is False
    assert blocked_start["number_suppressed"] is True
    suppression_events = list(
        (
            await db_session.scalars(
                select(SmsNumberSuppressionEvent)
                .where(
                    SmsNumberSuppressionEvent.tenant_id == test_tenant.id,
                    SmsNumberSuppressionEvent.suppression_id == suppression.id,
                )
                .order_by(
                    SmsNumberSuppressionEvent.occurred_at, SmsNumberSuppressionEvent.id
                )
            )
        ).all()
    )
    assert [row.action for row in suppression_events] == [
        "provider_stop",
        "provider_start_blocked",
    ]

    seeded.contact.phone = stopped_number
    seeded.consent.mobile_e164 = stopped_number
    seeded.consent.sms_allowed = True
    seeded.consent.sms_status = "active"
    seeded.consent.sms_revoked_at = None
    seeded.consent.revoked_at = None
    seeded.consent.consented_at = datetime.now(timezone.utc)
    await db_session.commit()
    provider = _Provider(lambda **_kwargs: pytest.fail("suppressed SMS dispatched"))
    _install_provider(monkeypatch, provider)
    with pytest.raises(SmsError, match="durable provider opt-out") as blocked:
        await send_sms(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            contact_id=seeded.contact.id,
            matter_id=seeded.matter.id,
            body="Must remain suppressed",
            category="appointment",
            idempotency_key="unmatched-stop-later-identity",
        )
    assert blocked.value.delivery_certainty == "not_attempted"
    assert provider.calls == []

    other_tenant = Tenant(
        name="Suppression isolation tenant",
        domain=f"suppression-{uuid.uuid4().hex}.invalid",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(other_tenant)
    await db_session.flush()
    await set_tenant_context(db_session, str(other_tenant.id))
    other_fence = await lock_sms_number_suppression(
        db_session,
        tenant_id=other_tenant.id,
        mobile_e164=stopped_number,
        initial_suppressed=False,
    )
    assert other_fence.is_suppressed is False


@pytest.mark.asyncio
async def test_stop_serializes_with_send_in_both_orders(
    db_session, test_engine, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="stop-race"
    )
    maker = async_sessionmaker(test_engine, expire_on_commit=False)
    provider_entered = asyncio.Event()
    provider_release = asyncio.Event()

    async def accepted(**_kwargs):
        provider_entered.set()
        await provider_release.wait()
        return _Response(201, {"sid": "SM-STOP-RACE", "status": "queued"})

    provider = _Provider(accepted)
    _install_provider(monkeypatch, provider)

    async with maker() as stop_db:
        await set_tenant_context(stop_db, str(test_tenant.id))
        evidence = await apply_compliance_keyword(
            stop_db,
            tenant_id=test_tenant.id,
            from_number=seeded.contact.phone,
            keyword="STOP",
            provider_message_id="SM-STOP-FIRST",
        )
        assert evidence["applied"] is True

        async def blocked_send():
            async with maker() as send_db:
                await set_tenant_context(send_db, str(test_tenant.id))
                return await send_sms(
                    send_db,
                    tenant_id=test_tenant.id,
                    user_id=test_user.id,
                    contact_id=seeded.contact.id,
                    matter_id=seeded.matter.id,
                    body="STOP wins",
                    category="appointment",
                    idempotency_key="sms-stop-wins",
                )

        send_task = asyncio.create_task(blocked_send())
        await asyncio.sleep(0.1)
        assert not send_task.done()
        await stop_db.commit()
        with pytest.raises(SmsError, match="durable provider opt-out") as blocked:
            await asyncio.wait_for(send_task, timeout=5)
        assert blocked.value.status_code == 409
    assert provider.calls == []

    await set_tenant_context(db_session, str(test_tenant.id))
    consent = await db_session.get(LeadChannelConsent, seeded.consent.id)
    consent.sms_allowed = True
    consent.sms_status = "active"
    consent.sms_revoked_at = None
    consent.revoked_at = None
    consent.consented_at = datetime.now(timezone.utc)
    seeded.contact.sms_opt_in = True
    restarted = await apply_compliance_keyword(
        db_session,
        tenant_id=test_tenant.id,
        from_number=seeded.contact.phone,
        keyword="START",
        provider_message_id="SM-RESTART-BETWEEN-RACES",
    )
    assert restarted["applied"] is True
    await db_session.commit()

    async def winning_send():
        async with maker() as send_db:
            await set_tenant_context(send_db, str(test_tenant.id))
            return await send_sms(
                send_db,
                tenant_id=test_tenant.id,
                user_id=test_user.id,
                contact_id=seeded.contact.id,
                matter_id=seeded.matter.id,
                body="Send wins",
                category="appointment",
                idempotency_key="sms-send-wins",
            )

    send_task = asyncio.create_task(winning_send())
    await asyncio.wait_for(provider_entered.wait(), timeout=5)

    async def waiting_stop():
        async with maker() as stop_db:
            await set_tenant_context(stop_db, str(test_tenant.id))
            result = await apply_compliance_keyword(
                stop_db,
                tenant_id=test_tenant.id,
                from_number=seeded.contact.phone,
                keyword="STOP",
                provider_message_id="SM-STOP-SECOND",
            )
            await stop_db.commit()
            return result

    stop_task = asyncio.create_task(waiting_stop())
    await asyncio.sleep(0.1)
    assert not stop_task.done()
    provider_release.set()
    submitted = await asyncio.wait_for(send_task, timeout=5)
    assert submitted.status == "submitted"
    assert (await asyncio.wait_for(stop_task, timeout=5))["applied"] is True
    assert len(provider.calls) == 1
    await db_session.refresh(consent)
    assert consent.sms_allowed is False
    assert consent.sms_revoked_at is not None


@pytest.mark.asyncio
async def test_stop_suppresses_duplicate_phone_identities_without_a_matter(
    db_session, client, test_tenant, test_user
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="duplicate-phone"
    )
    phone = "+15557654321"
    contacts = []
    consents = []
    for index in range(2):
        contact = Contact(
            tenant_id=test_tenant.id,
            first_name="Duplicate",
            last_name=str(index),
            phone=phone,
        )
        db_session.add(contact)
        await db_session.flush()
        lead = Lead(
            tenant_id=test_tenant.id,
            contact_id=contact.id,
            status="qualified",
            source="website",
        )
        db_session.add(lead)
        await db_session.flush()
        consent = LeadChannelConsent(
            tenant_id=test_tenant.id,
            lead_id=lead.id,
            sms_allowed=True,
            sms_status="active",
            phone_verified=True,
            mobile_e164=phone,
            consented_at=datetime.now(timezone.utc),
            consent_source="public_intake",
            consent_timezone="America/Chicago",
            quiet_hours_start="23:00",
            quiet_hours_end="06:00",
            allowed_categories=["appointment"],
            disclosure_version="sms-v3",
        )
        db_session.add(consent)
        contacts.append(contact)
        consents.append(consent)
    await db_session.commit()
    path = f"/api/sms/webhooks/{test_tenant.id}/inbound"
    params = {
        "MessageSid": "SM-DUPLICATE-STOP",
        "From": phone,
        "To": "+15550001111",
        "Body": "STOP",
    }
    response = await client.post(
        path,
        data=params,
        headers=_signed_headers(path=path, params=params, secret=seeded.auth_token),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "review_required"
    for consent in consents:
        await db_session.refresh(consent)
        assert consent.sms_allowed is False
        assert consent.sms_revoked_at is not None
    review = await db_session.scalar(
        select(SmsReviewItem).where(
            SmsReviewItem.sms_message_id == uuid.UUID(response.json()["id"])
        )
    )
    assert set(review.candidate_contact_ids) == {str(row.id) for row in contacts}


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
            consent_evidence=[
                _consent_evidence(
                    seeded,
                    categories=(
                        seeded.consent.allowed_categories
                        if category in seeded.consent.allowed_categories
                        else [category]
                    ),
                )
            ],
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
            sms_revoked_at=datetime.now(timezone.utc),
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
async def test_staff_sms_revocation_is_channel_specific_and_reconsent_is_provenanced(
    db_session, client, test_tenant, test_user
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="staff-consent"
    )
    path = f"/api/intake/leads/{seeded.lead.id}/consent"
    revoked = await client.post(
        path,
        json={
            "email_allowed": True,
            "sms_allowed": False,
            "phone_verified": False,
            "disclosure_version": "sms-v3",
            "consent_source": "staff_recorded",
        },
    )
    assert revoked.status_code == 200
    await db_session.refresh(seeded.consent)
    assert seeded.consent.email_allowed is True
    assert seeded.consent.sms_allowed is False
    assert seeded.consent.sms_revoked_at is not None
    assert seeded.consent.revoked_at is None

    reconsented = await client.post(
        path,
        json={
            "email_allowed": True,
            "sms_allowed": True,
            "phone_verified": True,
            "mobile_e164": seeded.contact.phone,
            "disclosure_version": "sms-v4",
            "consent_source": "signed_fee_agreement",
            "consent_language": "en",
            "consent_timezone": "America/Chicago",
            "quiet_hours_start": "21:00",
            "quiet_hours_end": "07:00",
            "allowed_categories": ["appointment", "lead_follow_up"],
            "consent_expires_at": (
                datetime.now(timezone.utc) + timedelta(days=180)
            ).isoformat(),
        },
    )
    assert reconsented.status_code == 200
    await db_session.refresh(seeded.consent)
    assert seeded.consent.sms_status == "active"
    assert seeded.consent.sms_revoked_at is None
    assert seeded.consent.consent_source == "signed_fee_agreement"
    assert seeded.consent.disclosure_version == "sms-v4"
    events = list(
        (
            await db_session.scalars(
                select(SmsConsentEvent)
                .where(
                    SmsConsentEvent.tenant_id == test_tenant.id,
                    SmsConsentEvent.consent_id == seeded.consent.id,
                )
                .order_by(SmsConsentEvent.occurred_at, SmsConsentEvent.id)
            )
        ).all()
    )
    assert [event.action for event in events] == ["staff_revoke", "staff_grant"]
    grant = events[-1]
    assert grant.phone_verified is True
    assert grant.consented_at == seeded.consent.consented_at
    assert grant.consent_expires_at == seeded.consent.consent_expires_at
    assert grant.consent_source == "signed_fee_agreement"
    assert grant.disclosure_version == "sms-v4"
    assert grant.consent_language == "en"
    assert grant.consent_timezone == "America/Chicago"
    assert grant.quiet_hours_start == "21:00"
    assert grant.quiet_hours_end == "07:00"
    assert grant.allowed_categories == ["appointment", "lead_follow_up"]


@pytest.mark.asyncio
async def test_stop_and_staff_regrant_serialize_in_both_orders(
    db_session, test_engine, client, test_tenant, test_user, monkeypatch
):
    maker = async_sessionmaker(test_engine, expire_on_commit=False)
    payload = {
        "email_allowed": True,
        "sms_allowed": True,
        "phone_verified": True,
        "disclosure_version": "sms-race-v1",
        "consent_source": "signed_fee_agreement",
        "consent_language": "en",
        "consent_timezone": "America/Chicago",
        "quiet_hours_start": "21:00",
        "quiet_hours_end": "07:00",
        "allowed_categories": ["appointment"],
        "consent_expires_at": (
            datetime.now(timezone.utc) + timedelta(days=180)
        ).isoformat(),
    }

    stop_first = await _seed_lifecycle(
        db_session,
        tenant=test_tenant,
        user=test_user,
        suffix="stop-regrant-stop-first",
        phone="+15551234581",
    )
    async with maker() as stop_db:
        await set_tenant_context(stop_db, str(test_tenant.id))
        await apply_compliance_keyword(
            stop_db,
            tenant_id=test_tenant.id,
            from_number=stop_first.contact.phone,
            keyword="STOP",
            provider_message_id="SM-STOP-REGRANT-FIRST",
        )
        regrant_task = asyncio.create_task(
            client.post(
                f"/api/intake/leads/{stop_first.lead.id}/consent",
                json={**payload, "mobile_e164": stop_first.contact.phone},
            )
        )
        await asyncio.sleep(0.1)
        assert not regrant_task.done()
        await stop_db.commit()
    blocked = await asyncio.wait_for(regrant_task, timeout=5)
    assert blocked.status_code == 409
    assert "must send START" in str(blocked.json()["detail"])

    regrant_first = await _seed_lifecycle(
        db_session,
        tenant=test_tenant,
        user=test_user,
        suffix="stop-regrant-grant-first",
        phone="+15551234582",
    )
    audit_reached = asyncio.Event()
    release_regrant = asyncio.Event()
    original_audit = conversion_router.record_operator_audit

    async def pause_before_regrant_commit(*args, **kwargs):
        await original_audit(*args, **kwargs)
        if kwargs.get("action") == "sms.consent.updated":
            audit_reached.set()
            await release_regrant.wait()

    monkeypatch.setattr(
        conversion_router,
        "record_operator_audit",
        pause_before_regrant_commit,
    )
    regrant_task = asyncio.create_task(
        client.post(
            f"/api/intake/leads/{regrant_first.lead.id}/consent",
            json={**payload, "mobile_e164": regrant_first.contact.phone},
        )
    )
    await asyncio.wait_for(audit_reached.wait(), timeout=5)

    async def stop_after_regrant_lock():
        async with maker() as stop_db:
            await set_tenant_context(stop_db, str(test_tenant.id))
            result = await apply_compliance_keyword(
                stop_db,
                tenant_id=test_tenant.id,
                from_number=regrant_first.contact.phone,
                keyword="STOP",
                provider_message_id="SM-STOP-AFTER-REGRANT",
            )
            await stop_db.commit()
            return result

    stop_task = asyncio.create_task(stop_after_regrant_lock())
    await asyncio.sleep(0.1)
    assert not stop_task.done()
    release_regrant.set()
    granted = await asyncio.wait_for(regrant_task, timeout=5)
    assert granted.status_code == 200
    stopped = await asyncio.wait_for(stop_task, timeout=5)
    assert stopped["applied"] is True

    await set_tenant_context(db_session, str(test_tenant.id))
    db_session.expire_all()
    final_consent = await db_session.get(LeadChannelConsent, regrant_first.consent.id)
    final_suppression = await db_session.scalar(
        select(SmsNumberSuppression).where(
            SmsNumberSuppression.tenant_id == test_tenant.id,
            SmsNumberSuppression.mobile_e164 == regrant_first.contact.phone,
        )
    )
    assert final_consent.sms_allowed is False
    assert final_consent.sms_status == "opted_out"
    assert final_consent.sms_revoked_at is not None
    assert final_suppression.is_suppressed is True


@pytest.mark.asyncio
async def test_stop_does_not_wait_on_an_unrelated_tenant_phone_identity(
    db_session, test_engine, test_tenant, test_user
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="stop-narrow-lock"
    )
    unrelated = Contact(
        tenant_id=test_tenant.id,
        first_name="Unrelated",
        last_name="Recipient",
        phone="+15550009999",
        created_by_user_id=test_user.id,
    )
    db_session.add(unrelated)
    await db_session.commit()
    maker = async_sessionmaker(test_engine, expire_on_commit=False)

    async with maker() as locker:
        await set_tenant_context(locker, str(test_tenant.id))
        await locker.scalar(
            select(Contact)
            .where(Contact.id == unrelated.id, Contact.tenant_id == test_tenant.id)
            .with_for_update()
        )

        async def stop_target_number():
            async with maker() as stop_db:
                await set_tenant_context(stop_db, str(test_tenant.id))
                result = await apply_compliance_keyword(
                    stop_db,
                    tenant_id=test_tenant.id,
                    from_number=seeded.contact.phone,
                    keyword="STOP",
                    provider_message_id="SM-NARROW-LOCK",
                )
                await stop_db.commit()
                return result

        result = await asyncio.wait_for(stop_target_number(), timeout=2)
        assert result["applied"] is True
        assert result["matched_contact_ids"] == [str(seeded.contact.id)]
        await locker.rollback()


@pytest.mark.asyncio
async def test_matter_party_authorization_serializes_with_dispatch_in_both_orders(
    db_session, test_engine, test_tenant, test_user, monkeypatch
):
    maker = async_sessionmaker(test_engine, expire_on_commit=False)

    removal_first = await _seed_lifecycle(
        db_session,
        tenant=test_tenant,
        user=test_user,
        suffix="party-removal-first",
    )
    removal_first.matter.client_contact_id = None
    await db_session.commit()
    after_reservation = asyncio.Event()
    continue_send = asyncio.Event()
    original_suppression_lock = sms_service.lock_sms_number_suppression

    async def pause_after_reservation(*args, **kwargs):
        after_reservation.set()
        await continue_send.wait()
        return await original_suppression_lock(*args, **kwargs)

    monkeypatch.setattr(
        sms_service, "lock_sms_number_suppression", pause_after_reservation
    )
    never_called = _Provider(
        lambda **_kwargs: pytest.fail("authorization loss must prevent provider I/O")
    )
    _install_provider(monkeypatch, never_called)

    async def removal_first_send():
        async with maker() as send_db:
            await set_tenant_context(send_db, str(test_tenant.id))
            with pytest.raises(SmsError) as blocked:
                await send_sms(
                    send_db,
                    tenant_id=test_tenant.id,
                    user_id=test_user.id,
                    contact_id=removal_first.contact.id,
                    matter_id=removal_first.matter.id,
                    body="Do not send after party removal",
                    category="appointment",
                    idempotency_key="matter-party-removal-first",
                )
            return blocked.value

    blocked_task = asyncio.create_task(removal_first_send())
    await asyncio.wait_for(after_reservation.wait(), timeout=5)
    async with maker() as removal_db:
        await set_tenant_context(removal_db, str(test_tenant.id))
        await removal_db.execute(
            delete(MatterParty).where(
                MatterParty.tenant_id == test_tenant.id,
                MatterParty.id == removal_first.party.id,
            )
        )
        await removal_db.commit()
    continue_send.set()
    blocked = await asyncio.wait_for(blocked_task, timeout=5)
    assert blocked.status_code == 409
    assert blocked.code == "sms_matter_authorization_changed"
    assert never_called.calls == []
    await set_tenant_context(db_session, str(test_tenant.id))
    blocked_message = await db_session.scalar(
        select(SmsMessage).where(
            SmsMessage.idempotency_key == "matter-party-removal-first"
        )
    )
    assert blocked_message.status == "blocked_matter_authorization_changed"
    blocked_audit = await db_session.scalar(
        select(OperatorAuditLog).where(
            OperatorAuditLog.action == "sms.dispatch.blocked",
            OperatorAuditLog.resource_id == str(blocked_message.id),
        )
    )
    assert blocked_audit.metadata_json["reason"] == "matter_authorization_changed"

    monkeypatch.setattr(
        sms_service,
        "lock_sms_number_suppression",
        original_suppression_lock,
    )
    send_first = await _seed_lifecycle(
        db_session,
        tenant=test_tenant,
        user=test_user,
        suffix="party-send-first",
        phone="+15551234568",
    )
    send_first.matter.client_contact_id = None
    await db_session.commit()
    provider_entered = asyncio.Event()
    provider_release = asyncio.Event()

    async def accepted(**_kwargs):
        provider_entered.set()
        await provider_release.wait()
        return _Response(
            201,
            {
                "sid": "SM-PARTY-SEND-FIRST",
                "status": "queued",
                "from": "+15550001111",
            },
        )

    provider = _Provider(accepted)
    _install_provider(monkeypatch, provider)

    async def dispatch_first():
        async with maker() as send_db:
            await set_tenant_context(send_db, str(test_tenant.id))
            return await send_sms(
                send_db,
                tenant_id=test_tenant.id,
                user_id=test_user.id,
                contact_id=send_first.contact.id,
                matter_id=send_first.matter.id,
                body="Authorized before party removal",
                category="appointment",
                idempotency_key="matter-party-send-first",
            )

    async def remove_after_dispatch_lock():
        async with maker() as removal_db:
            await set_tenant_context(removal_db, str(test_tenant.id))
            await removal_db.execute(
                delete(MatterParty).where(
                    MatterParty.tenant_id == test_tenant.id,
                    MatterParty.id == send_first.party.id,
                )
            )
            await removal_db.commit()

    send_task = asyncio.create_task(dispatch_first())
    await asyncio.wait_for(provider_entered.wait(), timeout=5)
    removal_task = asyncio.create_task(remove_after_dispatch_lock())
    await asyncio.sleep(0.1)
    assert not removal_task.done()
    provider_release.set()
    sent = await asyncio.wait_for(send_task, timeout=5)
    await asyncio.wait_for(removal_task, timeout=5)
    assert sent.status == "submitted"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_provider_failures_are_durable_without_fake_delivery(
    db_session, client, test_tenant, test_user, monkeypatch
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
    assert misbound.value.status_code == 404

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
    with pytest.raises(SmsError) as rejected_replay:
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
    assert rejected_replay.value.delivery_certainty == "provider_rejected"
    assert len(rejected_provider.calls) == 1

    async def failed_after_acceptance(**_kwargs):
        return _Response(
            201,
            {
                "sid": "SM-FAILED-AFTER-ACCEPTANCE",
                "status": "failed",
                "from": "+15550001111",
                "error_code": 30003,
            },
        )

    accepted_failure_provider = _Provider(failed_after_acceptance)
    _install_provider(monkeypatch, accepted_failure_provider)
    with pytest.raises(SmsError) as accepted_failure:
        await send_sms(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            contact_id=seeded.contact.id,
            matter_id=seeded.matter.id,
            body="Accepted before delivery failure",
            category="appointment",
            idempotency_key="provider-failed-after-acceptance",
        )
    assert (
        accepted_failure.value.delivery_certainty == "provider_failed_after_acceptance"
    )
    accepted_failure_row = await db_session.scalar(
        select(SmsMessage).where(
            SmsMessage.idempotency_key == "provider-failed-after-acceptance"
        )
    )
    assert accepted_failure_row.status == "provider_failed_after_acceptance"
    assert accepted_failure_row.provider_message_id == "SM-FAILED-AFTER-ACCEPTANCE"
    with pytest.raises(SmsError) as accepted_failure_replay:
        await send_sms(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            contact_id=seeded.contact.id,
            matter_id=seeded.matter.id,
            body="Accepted before delivery failure",
            category="appointment",
            idempotency_key="provider-failed-after-acceptance",
        )
    assert (
        accepted_failure_replay.value.delivery_certainty
        == "provider_failed_after_acceptance"
    )
    assert len(accepted_failure_provider.calls) == 1

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
    assert unknown.value.delivery_certainty == "outcome_unknown"
    assert unknown.value.reconciliation_required is True
    unknown_row = await db_session.scalar(
        select(SmsMessage).where(SmsMessage.idempotency_key == "provider-unknown")
    )
    assert unknown_row.status == "provider_unknown"
    assert unknown.value.sms_message_id == unknown_row.id
    assert unknown_row.reconciliation_required_at is not None
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
    api_replay = await client.post(
        "/api/sms/send",
        json={
            "contact_id": str(seeded.contact.id),
            "matter_id": str(seeded.matter.id),
            "body": "Provider outcome unknown",
            "category": "appointment",
            "idempotency_key": "provider-unknown",
        },
    )
    assert api_replay.status_code == 409
    assert api_replay.json()["detail"] == {
        "code": "sms_error",
        "message": "Provider outcome is unknown; reconcile this SMS before retrying",
        "delivery_certainty": "outcome_unknown",
        "sms_message_id": str(unknown_row.id),
        "reconciliation_required": True,
    }
    assert len(uncertain_provider.calls) == 1

    attested = await client.post(
        f"/api/sms/messages/{unknown_row.id}/reconcile",
        json={"resolution": "operator_attested_unknown"},
    )
    assert attested.status_code == 200
    assert attested.json()["status"] == "provider_unknown"
    assert attested.json()["delivery_certainty"] == "outcome_unknown"
    await db_session.refresh(unknown_row)
    assert unknown_row.reconciliation_resolved_at is None
    assert unknown_row.reconciliation_resolution == "operator_attested_unknown"
    assert unknown_row.operator_observed_absent_at is not None
    with pytest.raises(SmsError) as attested_replay:
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
    assert attested_replay.value.delivery_certainty == "outcome_unknown"
    assert attested_replay.value.reconciliation_required is True
    assert len(uncertain_provider.calls) == 1
    audit = await db_session.scalar(
        select(OperatorAuditLog).where(
            OperatorAuditLog.action == "sms.dispatch.operator_attested",
            OperatorAuditLog.resource_id == str(unknown_row.id),
        )
    )
    assert audit.metadata_json["tenant_id"] == str(test_tenant.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_payload", "audit_outcome", "expected_status"),
    [
        ({"status": "failed", "code": 21610}, "rejected", "provider_unknown"),
        (
            {
                "sid": "SM-AUDIT-FAIL-AFTER-ACCEPTANCE",
                "status": "failed",
                "from": "+15550001111",
                "error_code": 30003,
            },
            "failed_after_acceptance",
            "provider_unknown",
        ),
    ],
)
async def test_provider_terminal_state_never_commits_without_its_atomic_audit(
    provider_payload,
    audit_outcome,
    expected_status,
    db_session,
    test_engine,
    test_tenant,
    test_user,
    monkeypatch,
):
    seeded = await _seed_lifecycle(
        db_session,
        tenant=test_tenant,
        user=test_user,
        suffix=f"atomic-audit-{audit_outcome}",
        phone=("+15551234583" if audit_outcome == "rejected" else "+15551234584"),
    )

    async def provider_response(**_kwargs):
        return _Response(
            400 if "sid" not in provider_payload else 201, provider_payload
        )

    _install_provider(monkeypatch, _Provider(provider_response))
    original_audit = sms_service._append_sms_outcome_audit

    def fail_target_audit(*args, **kwargs):
        if kwargs.get("outcome") == audit_outcome:
            raise RuntimeError("forced terminal audit failure")
        return original_audit(*args, **kwargs)

    monkeypatch.setattr(
        sms_service,
        "_append_sms_outcome_audit",
        fail_target_audit,
    )
    maker = async_sessionmaker(test_engine, expire_on_commit=False)
    async with maker() as send_db:
        await set_tenant_context(send_db, str(test_tenant.id))
        with pytest.raises(SmsError) as uncertain:
            await send_sms(
                send_db,
                tenant_id=test_tenant.id,
                user_id=test_user.id,
                contact_id=seeded.contact.id,
                matter_id=seeded.matter.id,
                body=f"Atomic {audit_outcome}",
                category="appointment",
                idempotency_key=f"atomic-audit-{audit_outcome}",
            )
        assert uncertain.value.delivery_certainty == "outcome_unknown"
        assert uncertain.value.reconciliation_required is True

    await set_tenant_context(db_session, str(test_tenant.id))
    db_session.expire_all()
    message = await db_session.scalar(
        select(SmsMessage).where(
            SmsMessage.idempotency_key == f"atomic-audit-{audit_outcome}"
        )
    )
    assert message.status == expected_status
    assert message.delivery_certainty == "outcome_unknown"
    assert message.reconciliation_required_at is not None
    assert message.communication_log_id is not None
    communication = await db_session.get(
        CommunicationLog,
        message.communication_log_id,
    )
    assert communication.status == "unknown"
    terminal_audit = await db_session.scalar(
        select(OperatorAuditLog).where(
            OperatorAuditLog.resource_id == str(message.id),
            OperatorAuditLog.action == f"sms.dispatch.{audit_outcome}",
        )
    )
    assert terminal_audit is None
    unknown_audit = await db_session.scalar(
        select(OperatorAuditLog).where(
            OperatorAuditLog.resource_id == str(message.id),
            OperatorAuditLog.action == "sms.dispatch.outcome_unknown",
        )
    )
    assert unknown_audit is not None


@pytest.mark.asyncio
async def test_pre_provider_block_does_not_commit_without_its_atomic_audit(
    db_session, test_engine, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session,
        tenant=test_tenant,
        user=test_user,
        suffix="atomic-audit-block",
        phone="+15551234585",
    )
    suppression = await lock_sms_number_suppression(
        db_session,
        tenant_id=test_tenant.id,
        mobile_e164=seeded.contact.phone,
        initial_suppressed=True,
    )
    suppression.is_suppressed = True
    suppression.reason = "provider_stop"
    await db_session.commit()

    def fail_block_audit(*_args, **kwargs):
        if kwargs.get("outcome") == "blocked":
            raise RuntimeError("forced block audit failure")
        raise AssertionError("unexpected SMS outcome audit")

    monkeypatch.setattr(
        sms_service,
        "_append_sms_outcome_audit",
        fail_block_audit,
    )
    _install_provider(
        monkeypatch,
        _Provider(lambda **_kwargs: pytest.fail("blocked SMS must not dispatch")),
    )
    maker = async_sessionmaker(test_engine, expire_on_commit=False)
    async with maker() as send_db:
        await set_tenant_context(send_db, str(test_tenant.id))
        with pytest.raises(RuntimeError, match="forced block audit failure"):
            await send_sms(
                send_db,
                tenant_id=test_tenant.id,
                user_id=test_user.id,
                contact_id=seeded.contact.id,
                matter_id=seeded.matter.id,
                body="Atomic block",
                category="appointment",
                idempotency_key="atomic-audit-block",
            )

    await set_tenant_context(db_session, str(test_tenant.id))
    db_session.expire_all()
    message = await db_session.scalar(
        select(SmsMessage).where(SmsMessage.idempotency_key == "atomic-audit-block")
    )
    assert message.status == "dispatching"
    assert message.communication_log_id is None
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(OperatorAuditLog)
            .where(OperatorAuditLog.resource_id == str(message.id))
        )
        == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [429, 500, 502, 504])
async def test_provider_transient_failures_remain_unknown_not_rejected(
    status_code, db_session, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session,
        tenant=test_tenant,
        user=test_user,
        suffix=f"provider-{status_code}",
    )

    async def unavailable(**_kwargs):
        return _Response(status_code, {"code": status_code, "status": "failed"})

    provider = _Provider(unavailable)
    _install_provider(monkeypatch, provider)
    with pytest.raises(SmsError) as uncertain:
        await send_sms(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            contact_id=seeded.contact.id,
            matter_id=seeded.matter.id,
            body=f"Provider {status_code} must not prove rejection",
            category="appointment",
            idempotency_key=f"provider-server-{status_code}",
        )
    assert uncertain.value.delivery_certainty == "outcome_unknown"
    assert uncertain.value.reconciliation_required is True
    message = await db_session.scalar(
        select(SmsMessage).where(
            SmsMessage.idempotency_key == f"provider-server-{status_code}"
        )
    )
    assert message.status == "provider_unknown"
    assert message.provider_error_code == "RuntimeError"
    communication = await db_session.get(CommunicationLog, message.communication_log_id)
    assert communication.status == "unknown"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_reconciliation_requires_exact_provider_truth_and_is_audited(
    db_session, client, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="reconcile-truth"
    )
    seeded.config.messaging_service_sid = None
    seeded.config.from_number = "+15550001111"
    await db_session.commit()

    async def timeout(**_kwargs):
        raise TimeoutError("submission response lost")

    initial_provider = _Provider(timeout)
    _install_provider(monkeypatch, initial_provider)
    with pytest.raises(SmsError) as unknown:
        await send_sms(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            contact_id=seeded.contact.id,
            matter_id=seeded.matter.id,
            body="Reconcile exact provider truth",
            category="appointment",
            idempotency_key="reconcile-provider-truth",
        )
    message_id = unknown.value.sms_message_id
    message = await db_session.get(SmsMessage, message_id)
    created_at = message.provider_submission_started_at.isoformat()
    exact = {
        "sid": "SM-RECOVERED",
        "account_sid": "ACreconcile-truth",
        "to": seeded.contact.phone,
        "from": "+15550001111",
        "body": "Reconcile exact provider truth",
        "messaging_service_sid": None,
        "direction": "outbound-api",
        "date_created": created_at,
        "uri": "/2010-04-01/Accounts/ACreconcile-truth/Messages/SM-RECOVERED.json",
        "status": "delivered",
    }

    lookup_payloads = [
        {
            "sid": "SM-FOREIGN",
            "account_sid": "AC-FOREIGN",
            "to": seeded.contact.phone,
            "from": "+15550001111",
            "body": "Reconcile exact provider truth",
            "direction": "outbound-api",
            "date_created": created_at,
            "uri": "/2010-04-01/Accounts/AC-FOREIGN/Messages/SM-FOREIGN.json",
            "status": "delivered",
        },
        {**exact, "body": "Different message"},
        {**exact, "from": "+15550002222"},
        {**exact, "messaging_service_sid": "MG-WRONG"},
        {**exact, "direction": "inbound"},
        {
            **exact,
            "date_created": (
                message.provider_submission_started_at - timedelta(hours=1)
            ).isoformat(),
        },
        exact,
    ]

    async def lookup(**_kwargs):
        return _Response(200, lookup_payloads.pop(0))

    provider = _Provider(
        lambda **_kwargs: pytest.fail("reconciliation must not resend"),
        lookup_handler=lookup,
    )
    _install_provider(monkeypatch, provider)

    queue = await client.get("/api/sms/reconciliation")
    assert queue.status_code == 200
    assert message_id in {uuid.UUID(item["id"]) for item in queue.json()}
    detail = await client.get(f"/api/sms/reconciliation/{message_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "provider_unknown"

    mismatched = await client.post(
        f"/api/sms/messages/{message_id}/reconcile",
        json={
            "resolution": "provider_lookup",
            "provider_message_id": "SM-FOREIGN",
        },
    )
    assert mismatched.status_code == 409
    assert mismatched.json()["detail"]["code"] == "sms_provider_identity_mismatch"
    db_session.expire_all()
    unresolved = await db_session.get(SmsMessage, message_id)
    assert unresolved.reconciliation_resolved_at is None
    assert unresolved.status == "provider_unknown"

    for _ in range(5):
        unbound = await client.post(
            f"/api/sms/messages/{message_id}/reconcile",
            json={
                "resolution": "provider_lookup",
                "provider_message_id": "SM-RECOVERED",
            },
        )
        assert unbound.status_code == 409
        assert unbound.json()["detail"]["code"] == "sms_provider_identity_mismatch"
        db_session.expire_all()
        unresolved = await db_session.get(SmsMessage, message_id)
        assert unresolved.status == "provider_unknown"
        assert unresolved.reconciliation_resolved_at is None

    reconciled = await client.post(
        f"/api/sms/messages/{message_id}/reconcile",
        json={
            "resolution": "provider_lookup",
            "provider_message_id": "SM-RECOVERED",
        },
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["status"] == "delivered"
    assert reconciled.json()["provider_message_id"] == "SM-RECOVERED"
    db_session.expire_all()
    message = await db_session.get(SmsMessage, message_id)
    communication = await db_session.get(CommunicationLog, message.communication_log_id)
    assert communication.status == "delivered"
    assert message.reconciliation_resolution == "provider_lookup"
    assert provider.calls == []
    assert len(provider.lookup_calls) == 7

    replay = await client.post(
        f"/api/sms/messages/{message_id}/reconcile",
        json={
            "resolution": "provider_lookup",
            "provider_message_id": "SM-RECOVERED",
        },
    )
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "sms_reconciliation_not_required"
    assert len(provider.lookup_calls) == 7
    rejected_audit = await db_session.scalar(
        select(OperatorAuditLog).where(
            OperatorAuditLog.action == "sms.dispatch.reconciliation_rejected",
            OperatorAuditLog.resource_id == str(message_id),
        )
    )
    assert rejected_audit.metadata_json["tenant_id"] == str(test_tenant.id)
    assert rejected_audit.metadata_json["code"] in {
        "sms_provider_identity_mismatch",
        "sms_reconciliation_not_required",
    }
    success_audit = await db_session.scalar(
        select(OperatorAuditLog).where(
            OperatorAuditLog.action == "sms.dispatch.reconciled",
            OperatorAuditLog.resource_id == str(message_id),
        )
    )
    assert success_audit.metadata_json["resolution"] == "provider_lookup"


@pytest.mark.asyncio
async def test_signed_callback_wins_reconciliation_race_without_provider_poll(
    db_session, client, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="callback-race"
    )

    async def accepted(**_kwargs):
        return _Response(201, {"sid": "SM-CALLBACK-RACE", "status": "queued"})

    provider = _Provider(
        accepted,
        lookup_handler=lambda **_kwargs: pytest.fail(
            "resolved callback must prevent provider lookup"
        ),
    )
    _install_provider(monkeypatch, provider)
    message = await send_sms(
        db_session,
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        contact_id=seeded.contact.id,
        matter_id=seeded.matter.id,
        body="Callback race",
        category="appointment",
        idempotency_key="callback-reconcile-race",
    )
    message.reconciliation_required_at = datetime.now(timezone.utc)
    await db_session.commit()
    path = f"/api/sms/webhooks/{test_tenant.id}/status"
    params = {"MessageSid": "SM-CALLBACK-RACE", "MessageStatus": "delivered"}
    callback = await client.post(
        path,
        data=params,
        headers=_signed_headers(path=path, params=params, secret=seeded.auth_token),
    )
    assert callback.status_code == 200
    assert callback.json()["status"] == "delivered"
    rejected = await client.post(
        f"/api/sms/messages/{message.id}/reconcile",
        json={"resolution": "provider_lookup"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "sms_reconciliation_not_required"
    assert provider.lookup_calls == []


@pytest.mark.asyncio
async def test_provider_lookup_and_signed_callback_serialize_without_truth_regression(
    db_session, client, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session,
        tenant=test_tenant,
        user=test_user,
        suffix="lookup-callback-race",
    )
    seeded.config.messaging_service_sid = None
    seeded.config.from_number = "+15550001111"
    await db_session.commit()

    async def accepted(**_kwargs):
        return _Response(
            201,
            {
                "sid": "SM-LOOKUP-CALLBACK-RACE",
                "status": "queued",
                "from": "+15550001111",
            },
        )

    _install_provider(monkeypatch, _Provider(accepted))
    message = await send_sms(
        db_session,
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        contact_id=seeded.contact.id,
        matter_id=seeded.matter.id,
        body="Serialize provider truth",
        category="appointment",
        idempotency_key="lookup-callback-race",
    )
    message.status = "provider_unknown"
    message.delivery_certainty = "outcome_unknown"
    message.reconciliation_required_at = datetime.now(timezone.utc)
    communication = await db_session.get(
        CommunicationLog,
        message.communication_log_id,
    )
    communication.status = "unknown"
    await db_session.commit()

    lookup_entered = asyncio.Event()
    release_lookup = asyncio.Event()
    created_at = message.provider_submission_started_at.isoformat()

    async def lookup(**_kwargs):
        lookup_entered.set()
        await release_lookup.wait()
        return _Response(
            200,
            {
                "sid": "SM-LOOKUP-CALLBACK-RACE",
                "account_sid": seeded.config.account_sid,
                "to": seeded.contact.phone,
                "from": "+15550001111",
                "body": "Serialize provider truth",
                "messaging_service_sid": None,
                "direction": "outbound-api",
                "date_created": created_at,
                "uri": (
                    f"/2010-04-01/Accounts/{seeded.config.account_sid}/Messages/"
                    "SM-LOOKUP-CALLBACK-RACE.json"
                ),
                "status": "delivered",
            },
        )

    provider = _Provider(
        lambda **_kwargs: pytest.fail("reconciliation must not dispatch"),
        lookup_handler=lookup,
    )
    _install_provider(monkeypatch, provider)
    reconcile_task = asyncio.create_task(
        client.post(
            f"/api/sms/messages/{message.id}/reconcile",
            json={"resolution": "provider_lookup"},
        )
    )
    await asyncio.wait_for(lookup_entered.wait(), timeout=5)

    callback_path = f"/api/sms/webhooks/{test_tenant.id}/status"
    callback_params = {
        "MessageSid": "SM-LOOKUP-CALLBACK-RACE",
        "MessageStatus": "delivered",
    }
    callback_task = asyncio.create_task(
        client.post(
            callback_path,
            data=callback_params,
            headers=_signed_headers(
                path=callback_path,
                params=callback_params,
                secret=seeded.auth_token,
            ),
        )
    )
    await asyncio.sleep(0.1)
    assert not callback_task.done()
    release_lookup.set()
    reconciled = await asyncio.wait_for(reconcile_task, timeout=5)
    callback = await asyncio.wait_for(callback_task, timeout=5)
    assert reconciled.status_code == 200
    assert callback.status_code == 200
    assert reconciled.json()["status"] == "delivered"
    assert callback.json()["status"] == "delivered"
    assert len(provider.lookup_calls) == 1

    await set_tenant_context(db_session, str(test_tenant.id))
    db_session.expire_all()
    final = await db_session.get(SmsMessage, message.id)
    final_communication = await db_session.get(
        CommunicationLog,
        final.communication_log_id,
    )
    assert final.status == "delivered"
    assert final.delivery_certainty == "confirmed_sent"
    assert final.reconciliation_resolution == "provider_lookup"
    assert final_communication.status == "delivered"


@pytest.mark.asyncio
async def test_signed_callback_overrides_operator_attestation_without_regression(
    db_session, client, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="attestation-callback"
    )

    async def accepted(**_kwargs):
        return _Response(
            201,
            {
                "sid": "SM-ATTESTATION-CALLBACK",
                "status": "queued",
                "from": "+15550001111",
            },
        )

    provider = _Provider(accepted)
    _install_provider(monkeypatch, provider)
    message = await send_sms(
        db_session,
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        contact_id=seeded.contact.id,
        matter_id=seeded.matter.id,
        body="Attestation remains uncertain",
        category="appointment",
        idempotency_key="operator-attestation-callback",
    )
    message.reconciliation_required_at = datetime.now(timezone.utc)
    await db_session.commit()

    attested = await client.post(
        f"/api/sms/messages/{message.id}/reconcile",
        json={"resolution": "operator_attested_unknown"},
    )
    assert attested.status_code == 200
    assert attested.json()["status"] == "provider_unknown"
    assert attested.json()["reconciliation_resolved_at"] is None

    path = f"/api/sms/webhooks/{test_tenant.id}/status"
    for status in ("delivered", "delivered", "failed"):
        params = {
            "MessageSid": "SM-ATTESTATION-CALLBACK",
            "MessageStatus": status,
        }
        callback = await client.post(
            path,
            data=params,
            headers=_signed_headers(
                path=path,
                params=params,
                secret=seeded.auth_token,
            ),
        )
        assert callback.status_code == 200
        assert callback.json()["status"] == "delivered"

    db_session.expire_all()
    resolved = await db_session.get(SmsMessage, message.id)
    assert resolved.delivery_certainty == "confirmed_sent"
    assert (
        resolved.reconciliation_resolution
        == "signed_callback_overrode_operator_attestation"
    )
    assert resolved.reconciliation_resolved_at is not None
    assert resolved.operator_observed_absent_at is not None


@pytest.mark.asyncio
async def test_provider_config_is_revalidated_before_dispatch_without_global_lock(
    db_session, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="config-race"
    )

    async def accepted(**_kwargs):
        return _Response(201, {"sid": "SM-CONFIG-RACE", "status": "queued"})

    provider = _Provider(accepted)
    _install_provider(monkeypatch, provider)
    original_config = sms_service._config
    checks = 0

    async def deactivate_before_second_check(db, tenant_id):
        nonlocal checks
        checks += 1
        if checks == 2:
            config = await db.scalar(
                select(SmsProviderConfig).where(
                    SmsProviderConfig.tenant_id == tenant_id,
                    SmsProviderConfig.provider == "twilio",
                )
            )
            config.is_active = False
            await db.flush()
        return await original_config(db, tenant_id)

    monkeypatch.setattr(sms_service, "_config", deactivate_before_second_check)
    with pytest.raises(SmsError) as blocked:
        await send_sms(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            contact_id=seeded.contact.id,
            matter_id=seeded.matter.id,
            body="Do not submit after provider deactivation",
            category="appointment",
            idempotency_key="provider-config-race",
        )
    assert checks == 2
    assert blocked.value.delivery_certainty == "not_attempted"
    row = await db_session.scalar(
        select(SmsMessage).where(SmsMessage.idempotency_key == "provider-config-race")
    )
    assert row.status == "blocked_provider_config"
    assert row.provider_message_id is None
    assert provider.calls == []


@pytest.mark.asyncio
async def test_provider_config_update_is_not_blocked_by_unrelated_provider_io(
    db_session, test_engine, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="config-parallel"
    )
    provider_entered = asyncio.Event()
    provider_release = asyncio.Event()

    async def accepted(**_kwargs):
        provider_entered.set()
        await provider_release.wait()
        return _Response(201, {"sid": "SM-CONFIG-PARALLEL", "status": "queued"})

    provider = _Provider(accepted)
    _install_provider(monkeypatch, provider)
    maker = async_sessionmaker(test_engine, expire_on_commit=False)

    async def dispatch():
        async with maker() as send_db:
            await set_tenant_context(send_db, str(test_tenant.id))
            return await send_sms(
                send_db,
                tenant_id=test_tenant.id,
                user_id=test_user.id,
                contact_id=seeded.contact.id,
                matter_id=seeded.matter.id,
                body="Provider config does not serialize this destination",
                category="appointment",
                idempotency_key="provider-config-parallel",
            )

    send_task = asyncio.create_task(dispatch())
    await asyncio.wait_for(provider_entered.wait(), timeout=5)

    async def rotate_generation():
        async with maker() as config_db:
            await set_tenant_context(config_db, str(test_tenant.id))
            config = await config_db.scalar(
                select(SmsProviderConfig)
                .where(
                    SmsProviderConfig.tenant_id == test_tenant.id,
                    SmsProviderConfig.provider == "twilio",
                )
                .with_for_update()
            )
            config.generation += 1
            await config_db.commit()
            return config.generation

    assert await asyncio.wait_for(rotate_generation(), timeout=2) == 2
    provider_release.set()
    submitted = await asyncio.wait_for(send_task, timeout=5)
    assert submitted.status == "submitted"
    assert submitted.provider_config_generation == 1
    assert submitted.provider_account_sid == "ACconfig-parallel"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_task_automation_retains_unknown_sms_identity_for_reconciliation(
    db_session, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="automation-unknown"
    )
    task = Task(
        tenant_id=test_tenant.id,
        title="Review unknown SMS",
        matter_id=seeded.matter.id,
        contact_id=seeded.contact.id,
        status="in_progress",
        source="assistant",
        created_by_user_id=test_user.id,
    )
    db_session.add(task)
    await db_session.commit()
    action = SmsClientAction(
        type="sms_client",
        recipient_bindings=[
            ResolvedSmsRecipientBinding(
                party_id=seeded.party.id,
                contact_id=seeded.contact.id,
                phone=seeded.contact.phone,
            )
        ],
        body="Provider response may be lost",
        category="appointment",
        matter_id=seeded.matter.id,
        consent_evidence=[_consent_evidence(seeded)],
        idempotency_key="automation-unknown-sms",
    )

    async def uncertain(**_kwargs):
        raise TimeoutError("provider response lost")

    provider = _Provider(uncertain)
    _install_provider(monkeypatch, provider)
    result = await _run_sms_client(
        db_session,
        task,
        action.model_dump(mode="json"),
        test_user.id,
    )
    assert result.succeeded is False
    assert result.delivery_certainty == "outcome_unknown"
    assert result.reconciliation_required is True
    assert result.sms_message_id is not None
    await set_tenant_context(db_session, str(test_tenant.id))
    row = await db_session.get(SmsMessage, result.sms_message_id)
    assert row.status == "provider_unknown"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_stale_dispatch_lease_becomes_reconciliation_work_without_resend(
    db_session, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="stale-lease"
    )
    provider = _Provider(lambda **_kwargs: pytest.fail("provider must not be called"))
    _install_provider(monkeypatch, provider)
    stale = SmsMessage(
        tenant_id=test_tenant.id,
        contact_id=seeded.contact.id,
        matter_id=seeded.matter.id,
        idempotency_key="stale-dispatch-lease",
        request_digest="d" * 64,
        direction="outbound",
        status="dispatching",
        dispatch_attempt_id=uuid.uuid4(),
        dispatch_started_at=datetime.now(timezone.utc) - timedelta(minutes=3),
        to_number=seeded.contact.phone,
        body="Possibly submitted before a worker crash",
        category="appointment",
        created_by_user_id=test_user.id,
    )
    stale_submitted = SmsMessage(
        tenant_id=test_tenant.id,
        contact_id=seeded.contact.id,
        matter_id=seeded.matter.id,
        idempotency_key="stale-submitted-callback",
        request_digest="e" * 64,
        provider_message_id="SM-STALE-SUBMITTED",
        provider_account_sid=seeded.config.account_sid,
        provider_config_generation=seeded.config.generation,
        direction="outbound",
        status="submitted",
        provider_status="queued",
        dispatch_attempt_id=uuid.uuid4(),
        dispatch_started_at=datetime.now(timezone.utc) - timedelta(hours=1),
        to_number=seeded.contact.phone,
        body="Signed callback is overdue",
        category="appointment",
        created_by_user_id=test_user.id,
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add_all([stale, stale_submitted])
    await db_session.commit()
    assert await mark_stale_sms_dispatches_for_reconciliation() >= 2
    await set_tenant_context(db_session, str(test_tenant.id))
    await db_session.refresh(stale)
    assert stale.status == "provider_unknown"
    assert stale.reconciliation_required_at is not None
    await db_session.refresh(stale_submitted)
    assert stale_submitted.status == "submitted"
    assert stale_submitted.reconciliation_required_at is not None
    assert stale_submitted.raw_provider_event["reconciliation_reason"] == (
        "signed_status_callback_overdue"
    )
    assert provider.calls == []


@pytest.mark.asyncio
async def test_authorized_review_resolution_controls_timeline_and_audit(
    db_session, client, test_tenant, test_user
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="review-resolution"
    )
    second = Matter(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"review-second-{uuid.uuid4().hex[:8]}",
        matter_name="Second route candidate",
        client_contact_id=seeded.contact.id,
    )
    db_session.add(second)
    await db_session.commit()
    path = f"/api/sms/webhooks/{test_tenant.id}/inbound"
    params = {
        "MessageSid": "SM-REVIEW-RESOLVE",
        "From": seeded.contact.phone,
        "To": "+15550001111",
        "Body": "Please call about the appointment",
    }
    inbound = await client.post(
        path,
        data=params,
        headers=_signed_headers(path=path, params=params, secret=seeded.auth_token),
    )
    assert inbound.status_code == 200
    message_id = uuid.UUID(inbound.json()["id"])
    message = await db_session.get(SmsMessage, message_id)
    assert message.communication_log_id is None

    reviews = await client.get("/api/sms/review")
    assert reviews.status_code == 200
    review = next(
        row for row in reviews.json() if row["sms_message_id"] == str(message_id)
    )
    assert review["body"] == "Please call about the appointment"
    assert review["candidate_contacts"] == [
        {"id": str(seeded.contact.id), "label": seeded.contact.display_name}
    ]
    assert {row["label"] for row in review["candidate_matters"]} == {
        seeded.matter.matter_name,
        second.matter_name,
    }
    resolved = await client.post(
        f"/api/sms/review/{review['id']}",
        json={
            "decision": "resolve",
            "contact_id": str(seeded.contact.id),
            "matter_id": str(seeded.matter.id),
        },
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    await db_session.refresh(message)
    assert message.status == "received"
    assert message.communication_log_id is not None
    communication = await db_session.get(CommunicationLog, message.communication_log_id)
    assert communication.matter_id == seeded.matter.id
    audit = await db_session.scalar(
        select(OperatorAuditLog).where(
            OperatorAuditLog.action == "sms.inbound_route.resolve",
            OperatorAuditLog.resource_id == review["id"],
        )
    )
    assert audit.metadata_json["tenant_id"] == str(test_tenant.id)
    assert "body" not in audit.metadata_json


@pytest.mark.asyncio
async def test_duplicate_phone_review_includes_resolvable_matter_candidates(
    db_session, client, test_tenant, test_user
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="duplicate-route"
    )
    duplicate = Contact(
        tenant_id=test_tenant.id,
        first_name="Duplicate",
        last_name="Route",
        phone=seeded.contact.phone,
        created_by_user_id=test_user.id,
    )
    db_session.add(duplicate)
    await db_session.flush()
    duplicate_lead = Lead(
        tenant_id=test_tenant.id,
        contact_id=duplicate.id,
        status="qualified",
        source="website",
        created_by_user_id=test_user.id,
    )
    duplicate_matter = Matter(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"duplicate-route-{uuid.uuid4().hex[:8]}",
        matter_name="Duplicate phone route",
        client_contact_id=duplicate.id,
    )
    db_session.add_all([duplicate_lead, duplicate_matter])
    await db_session.commit()

    path = f"/api/sms/webhooks/{test_tenant.id}/inbound"
    params = {
        "MessageSid": "SM-DUPLICATE-ROUTE",
        "From": seeded.contact.phone,
        "To": "+15550001111",
        "Body": "Please attach this to the right matter",
    }
    inbound = await client.post(
        path,
        data=params,
        headers=_signed_headers(path=path, params=params, secret=seeded.auth_token),
    )
    assert inbound.status_code == 200
    assert inbound.json()["status"] == "review_required"
    reviews = await client.get("/api/sms/review")
    review = next(
        row for row in reviews.json() if row["sms_message_id"] == inbound.json()["id"]
    )
    assert set(review["candidate_contact_ids"]) == {
        str(seeded.contact.id),
        str(duplicate.id),
    }
    assert set(review["candidate_matter_ids"]) == {
        str(seeded.matter.id),
        str(duplicate_matter.id),
    }
    resolved = await client.post(
        f"/api/sms/review/{review['id']}",
        json={
            "decision": "resolve",
            "contact_id": str(duplicate.id),
            "matter_id": str(duplicate_matter.id),
        },
    )
    assert resolved.status_code == 200
    message = await db_session.get(SmsMessage, uuid.UUID(inbound.json()["id"]))
    await db_session.refresh(message)
    communication = await db_session.get(CommunicationLog, message.communication_log_id)
    assert communication.contact_id == duplicate.id
    assert communication.matter_id == duplicate_matter.id


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
    other_suppression = SmsNumberSuppression(
        tenant_id=other_tenant.id,
        mobile_e164=other.contact.phone,
        is_suppressed=True,
        reason="provider_stop",
    )
    db_session.add(other_suppression)
    await db_session.commit()

    await set_tenant_context(db_session, str(test_tenant.id))
    evidence = SmsConsentEvent(
        tenant_id=test_tenant.id,
        consent_id=primary.consent.id,
        lead_id=primary.lead.id,
        contact_id=primary.contact.id,
        action="staff_grant",
        sms_status="active",
        sms_allowed=True,
        phone_verified=True,
        mobile_e164=primary.contact.phone,
        consent_source="public_intake",
        disclosure_version="sms-v3",
        allowed_categories=["appointment"],
        actor_type="tenant_user",
        actor_user_id=test_user.id,
    )
    db_session.add(evidence)
    await db_session.commit()
    suppression = SmsNumberSuppression(
        tenant_id=test_tenant.id,
        mobile_e164=primary.contact.phone,
        is_suppressed=True,
        reason="provider_stop",
    )
    db_session.add(suppression)
    await db_session.flush()
    suppression_event = SmsNumberSuppressionEvent(
        tenant_id=test_tenant.id,
        suppression_id=suppression.id,
        mobile_e164=primary.contact.phone,
        action="provider_stop",
        keyword="STOP",
        is_suppressed=True,
    )
    inbound_message = SmsMessage(
        tenant_id=test_tenant.id,
        idempotency_key="rls-primary-message",
        request_digest="f" * 64,
        direction="inbound",
        status="review_required",
        from_number=primary.contact.phone,
        body="RLS evidence",
        category="customer_reply",
    )
    db_session.add_all([suppression_event, inbound_message])
    await db_session.flush()
    db_session.add(
        SmsReviewItem(
            tenant_id=test_tenant.id,
            sms_message_id=inbound_message.id,
            reason="unmatched_inbound_route",
        )
    )
    await db_session.commit()
    with pytest.raises(Exception, match="SMS evidence events are immutable"):
        await db_session.execute(
            text(
                "UPDATE sms_consent_events SET action='tampered' " "WHERE id=:event_id"
            ),
            {"event_id": str(evidence.id)},
        )
        await db_session.flush()
    await db_session.rollback()

    await set_tenant_context(db_session, str(test_tenant.id))
    with pytest.raises(Exception, match="SMS evidence events are immutable"):
        await db_session.execute(
            text("DELETE FROM sms_number_suppression_events WHERE id=:event_id"),
            {"event_id": str(suppression_event.id)},
        )
        await db_session.flush()
    await db_session.rollback()

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

    await set_tenant_context(db_session, str(test_tenant.id))
    db_session.add(
        SmsNumberSuppressionEvent(
            tenant_id=test_tenant.id,
            suppression_id=other_suppression.id,
            mobile_e164=other.contact.phone,
            action="provider_stop",
            keyword="STOP",
            is_suppressed=True,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()

    await set_tenant_context(db_session, str(test_tenant.id))
    db_session.add(
        LeadChannelConsent(
            tenant_id=test_tenant.id,
            lead_id=other.lead.id,
            disclosure_version="cross-tenant",
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
            for model, expected in {
                SmsProviderConfig: 1,
                LeadChannelConsent: 1,
                SmsConsentEvent: 1,
                SmsNumberSuppression: 1,
                SmsNumberSuppressionEvent: 1,
                SmsMessage: 1,
                SmsReviewItem: 1,
            }.items():
                assert (
                    await runtime_db.scalar(select(func.count()).select_from(model))
                    == expected
                )
            await runtime_db.rollback()
            await set_tenant_context(runtime_db, str(other_tenant.id))
            for model, expected in {
                SmsProviderConfig: 1,
                LeadChannelConsent: 1,
                SmsConsentEvent: 0,
                SmsNumberSuppression: 1,
                SmsNumberSuppressionEvent: 0,
                SmsMessage: 0,
                SmsReviewItem: 0,
            }.items():
                assert (
                    await runtime_db.scalar(select(func.count()).select_from(model))
                    == expected
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

    sms_tables = {
        "sms_provider_configs",
        "sms_messages",
        "sms_review_items",
        "sms_consent_events",
        "sms_number_suppressions",
        "sms_number_suppression_events",
    }
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
    contact = Contact(
        tenant_id=demo.id,
        first_name="Disposable",
        last_name="Prospect",
        phone="+15557770000",
    )
    db_session.add(contact)
    await db_session.flush()
    lead = Lead(tenant_id=demo.id, contact_id=contact.id, status="qualified")
    db_session.add(lead)
    await db_session.flush()
    consent = LeadChannelConsent(
        tenant_id=demo.id,
        lead_id=lead.id,
        sms_allowed=False,
        sms_status="opted_out",
        mobile_e164=contact.phone,
        sms_revoked_at=datetime.now(timezone.utc),
        disclosure_version="sms-v3",
    )
    db_session.add(consent)
    await db_session.flush()
    db_session.add(
        SmsConsentEvent(
            tenant_id=demo.id,
            consent_id=consent.id,
            lead_id=lead.id,
            contact_id=contact.id,
            action="provider_stop",
            sms_status="opted_out",
            sms_allowed=False,
            phone_verified=False,
            mobile_e164=contact.phone,
            disclosure_version="sms-v3",
            actor_type="provider_customer",
        )
    )
    db_session.add(
        SmsReviewItem(
            tenant_id=demo.id,
            sms_message_id=message.id,
            reason="unmatched_inbound_route",
        )
    )
    suppression = SmsNumberSuppression(
        tenant_id=demo.id,
        mobile_e164=contact.phone,
        is_suppressed=True,
        reason="provider_stop",
        provider_message_id="SM-DEMO-SENSITIVE",
    )
    db_session.add(suppression)
    await db_session.flush()
    db_session.add(
        SmsNumberSuppressionEvent(
            tenant_id=demo.id,
            suppression_id=suppression.id,
            mobile_e164=contact.phone,
            action="provider_stop",
            keyword="STOP",
            is_suppressed=True,
            provider_message_id="SM-DEMO-SENSITIVE",
        )
    )
    await db_session.commit()

    deleted = await purge_demo_tenant(db_session, demo.id)

    assert {name: deleted[name] for name in sms_tables} == {
        "sms_provider_configs": 1,
        "sms_messages": 1,
        "sms_review_items": 1,
        "sms_consent_events": 1,
        "sms_number_suppressions": 1,
        "sms_number_suppression_events": 1,
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
    assert "encrypted_" not in serialized
    assert "auth-token-credential-response" not in serialized
    assert "rotated-auth-token" not in serialized
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
        "generation": 2,
        "ownership_model": "firm-owned",
    }


@pytest.mark.asyncio
async def test_success_audit_failure_cannot_commit_fake_submitted_truth(
    db_session, client, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="audit-atomic"
    )

    async def accepted(**_kwargs):
        return _Response(201, {"sid": "SM-AUDIT-ATOMIC", "status": "queued"})

    provider = _Provider(accepted)
    _install_provider(monkeypatch, provider)
    original_audit = sms_router.record_operator_audit

    async def fail_success_audit(*args, action, **kwargs):
        if action == "sms.send.submitted":
            raise RuntimeError("audit store unavailable")
        return await original_audit(*args, action=action, **kwargs)

    monkeypatch.setattr(sms_router, "record_operator_audit", fail_success_audit)
    response = await client.post(
        "/api/sms/send",
        json={
            "contact_id": str(seeded.contact.id),
            "matter_id": str(seeded.matter.id),
            "body": "Audit and provider truth commit together",
            "category": "appointment",
            "idempotency_key": "sms-audit-atomic",
        },
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "sms_error"
    message = await db_session.scalar(
        select(SmsMessage).where(SmsMessage.idempotency_key == "sms-audit-atomic")
    )
    assert message.status == "provider_unknown"
    assert message.provider_message_id == "SM-AUDIT-ATOMIC"
    assert message.reconciliation_required_at is not None
    communication = await db_session.get(CommunicationLog, message.communication_log_id)
    assert communication.status == "unknown"
    submitted_audit = await db_session.scalar(
        select(OperatorAuditLog).where(
            OperatorAuditLog.action == "sms.send.submitted",
            OperatorAuditLog.resource_id == str(message.id),
        )
    )
    assert submitted_audit is None
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_sms_draft_edit_resets_staged_review_and_rotates_identity(
    db_session, client, test_tenant, test_user
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="draft-edit"
    )
    staff = User(
        tenant_id=test_tenant.id,
        email=f"sms-staff-{uuid.uuid4().hex}@example.invalid",
        full_name="SMS staff reviewer",
        role="user",
    )
    db_session.add(staff)
    await db_session.flush()
    action = SmsClientAction(
        type="sms_client",
        recipient_bindings=[
            ResolvedSmsRecipientBinding(
                party_id=seeded.party.id,
                contact_id=seeded.contact.id,
                phone=seeded.contact.phone,
            )
        ],
        body="Original reviewed reminder",
        category="appointment",
        matter_id=seeded.matter.id,
        consent_evidence=[_consent_evidence(seeded)],
        idempotency_key="sms-draft-before-edit",
    )
    now = datetime.now(timezone.utc)
    task = Task(
        tenant_id=test_tenant.id,
        title="Review SMS edit",
        matter_id=seeded.matter.id,
        contact_id=seeded.contact.id,
        status="review",
        source="assistant",
        pending_action=action.model_dump(mode="json"),
        review_policy="staff_then_attorney",
        review_stage="approved",
        reviewer_user_id=test_user.id,
        staff_reviewer_user_id=staff.id,
        attorney_reviewer_user_id=test_user.id,
        staff_reviewed_at=now,
        staff_reviewed_by_user_id=staff.id,
        attorney_approved_at=now,
        attorney_approved_by_user_id=test_user.id,
        created_by_user_id=test_user.id,
    )
    db_session.add(task)
    await db_session.commit()
    before_key = task.pending_action["idempotency_key"]
    before_recipient = task.pending_action["recipient_bindings"]

    edited = await client.patch(
        f"/api/tasks/{task.id}/pending-action",
        json={
            "body": "Updated reviewed reminder",
            "category": "appointment",
            "recipient_bindings": [{"phone": "+15550000000"}],
            "expected_version": task.version,
        },
    )
    assert edited.status_code == 200
    await db_session.refresh(task)
    assert task.pending_action["body"] == "Updated reviewed reminder"
    assert task.pending_action["recipient_bindings"] == before_recipient
    assert task.pending_action["idempotency_key"] != before_key
    assert task.pending_action["idempotency_key"].startswith(f"task-sms-{task.id}-")
    assert task.review_stage == "staff"
    assert task.reviewer_user_id == staff.id
    assert task.staff_reviewed_at is None
    assert task.attorney_approved_at is None

    rejected = await client.patch(
        f"/api/tasks/{task.id}/pending-action",
        json={
            "category": "billing",
            "expected_version": task.version,
        },
    )
    assert rejected.status_code == 422
    await db_session.refresh(task)
    assert task.pending_action["category"] == "appointment"


@pytest.mark.asyncio
async def test_sms_approval_requires_exact_action_and_consent_hash_on_both_apis(
    db_session, client, test_tenant, test_user
):
    seeded = await _seed_lifecycle(
        db_session,
        tenant=test_tenant,
        user=test_user,
        suffix="approval-ack",
        phone="+15551234586",
    )

    async def create_review_task(suffix: str) -> tuple[Task, SmsClientAction]:
        action = SmsClientAction(
            type="sms_client",
            recipient_bindings=[
                ResolvedSmsRecipientBinding(
                    party_id=seeded.party.id,
                    contact_id=seeded.contact.id,
                    phone=seeded.contact.phone,
                )
            ],
            body=f"Reviewed SMS {suffix}",
            category="appointment",
            matter_id=seeded.matter.id,
            consent_evidence=[_consent_evidence(seeded)],
            idempotency_key=f"sms-approval-ack-{suffix}",
        )
        task = Task(
            tenant_id=test_tenant.id,
            title=f"Review SMS acknowledgement {suffix}",
            matter_id=seeded.matter.id,
            contact_id=seeded.contact.id,
            status="review",
            source="assistant",
            pending_action=action.model_dump(mode="json"),
            created_by_user_id=test_user.id,
        )
        db_session.add(task)
        await db_session.commit()
        return task, action

    transition_task, transition_action = await create_review_task("transition")
    transition_body = (await client.get(f"/api/tasks/{transition_task.id}")).json()
    transition_hash = transition_body["pending_action_sha256"]
    assert len(transition_hash) == 64

    missing = await client.post(
        f"/api/tasks/{transition_task.id}/transition",
        json={
            "to_status": "in_progress",
            "expected_version": transition_task.version,
        },
    )
    assert missing.status_code == 409
    stale = await client.post(
        f"/api/tasks/{transition_task.id}/transition",
        json={
            "to_status": "in_progress",
            "expected_version": transition_task.version,
            "sms_acknowledgement": {
                "action_sha256": "0" * 64,
                "consent_snapshot_sha256": transition_action.consent_evidence[
                    0
                ].evidence_sha256,
            },
        },
    )
    assert stale.status_code == 409
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(TaskAutomationRun)
            .where(TaskAutomationRun.task_id == transition_task.id)
        )
        == 0
    )
    approved = await client.post(
        f"/api/tasks/{transition_task.id}/transition",
        json={
            "to_status": "in_progress",
            "expected_version": transition_task.version,
            "sms_acknowledgement": {
                "action_sha256": transition_hash,
                "consent_snapshot_sha256": transition_action.consent_evidence[
                    0
                ].evidence_sha256,
            },
        },
    )
    assert approved.status_code == 200
    transition_run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == transition_task.id)
    )
    assert transition_run.status == "queued"
    assert transition_run.action_sha256 == transition_hash

    patch_task, patch_action = await create_review_task("patch")
    patch_body = (await client.get(f"/api/tasks/{patch_task.id}")).json()
    missing_patch = await client.patch(
        f"/api/tasks/{patch_task.id}",
        json={
            "status": "in_progress",
            "expected_version": patch_task.version,
        },
    )
    assert missing_patch.status_code == 409
    approved_patch = await client.patch(
        f"/api/tasks/{patch_task.id}",
        json={
            "status": "in_progress",
            "expected_version": patch_task.version,
            "sms_acknowledgement": {
                "action_sha256": patch_body["pending_action_sha256"],
                "consent_snapshot_sha256": patch_action.consent_evidence[
                    0
                ].evidence_sha256,
            },
        },
    )
    assert approved_patch.status_code == 200

    edited_task, edited_action = await create_review_task("edited")
    before_edit = (await client.get(f"/api/tasks/{edited_task.id}")).json()
    edited = await client.patch(
        f"/api/tasks/{edited_task.id}/pending-action",
        json={
            "body": "A newly reviewed exact SMS body",
            "expected_version": edited_task.version,
        },
    )
    assert edited.status_code == 200
    after_edit = (await client.get(f"/api/tasks/{edited_task.id}")).json()
    assert after_edit["pending_action_sha256"] != before_edit["pending_action_sha256"]
    stale_after_edit = await client.post(
        f"/api/tasks/{edited_task.id}/transition",
        json={
            "to_status": "in_progress",
            "expected_version": after_edit["version"],
            "sms_acknowledgement": {
                "action_sha256": before_edit["pending_action_sha256"],
                "consent_snapshot_sha256": edited_action.consent_evidence[
                    0
                ].evidence_sha256,
            },
        },
    )
    assert stale_after_edit.status_code == 409
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(TaskAutomationRun)
            .where(TaskAutomationRun.task_id == edited_task.id)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_task_cancellation_conflicts_while_sms_provider_dispatch_is_claimed(
    db_session, client, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session,
        tenant=test_tenant,
        user=test_user,
        suffix="cancel-provider-race",
        phone="+15551234587",
    )
    settings = await db_session.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == test_tenant.id)
    )
    if settings is None:
        settings = TenantSettings(
            tenant_id=test_tenant.id,
            enable_chat_actions=True,
        )
        db_session.add(settings)
    else:
        settings.enable_chat_actions = True
    action = SmsClientAction(
        type="sms_client",
        recipient_bindings=[
            ResolvedSmsRecipientBinding(
                party_id=seeded.party.id,
                contact_id=seeded.contact.id,
                phone=seeded.contact.phone,
            )
        ],
        body="Provider-barrier cancellation rehearsal",
        category="appointment",
        matter_id=seeded.matter.id,
        consent_evidence=[_consent_evidence(seeded)],
        idempotency_key="sms-cancel-provider-race",
    )
    task = Task(
        tenant_id=test_tenant.id,
        title="SMS cancellation provider race",
        matter_id=seeded.matter.id,
        contact_id=seeded.contact.id,
        status="in_progress",
        source="assistant",
        pending_action=action.model_dump(mode="json"),
        created_by_user_id=test_user.id,
    )
    db_session.add(task)
    await db_session.flush()
    approval_key = task_automation.automation_idempotency_key(task, "review")
    await task_automation.enqueue_automation_run(
        db_session,
        task,
        from_status="review",
        actor_user_id=test_user.id,
        idempotency_key=approval_key,
    )
    await db_session.commit()

    provider_entered = asyncio.Event()
    provider_release = asyncio.Event()

    async def accepted(**_kwargs):
        provider_entered.set()
        await provider_release.wait()
        return _Response(
            201,
            {
                "sid": "SM-CANCEL-PROVIDER-RACE",
                "status": "queued",
                "from": "+15550001111",
            },
        )

    _install_provider(monkeypatch, _Provider(accepted))
    worker = asyncio.create_task(
        task_automation.run_task_automation(
            task.id,
            test_tenant.id,
            from_status="review",
            to_status="in_progress",
            actor_user_id=test_user.id,
            approval_idempotency_key=approval_key,
        )
    )
    await asyncio.wait_for(provider_entered.wait(), timeout=5)
    cancel = await client.post(
        f"/api/tasks/{task.id}/transition",
        json={
            "to_status": "cancelled",
            "expected_version": task.version,
            "reason": "Cancellation raced with provider submission",
        },
    )
    assert cancel.status_code == 409
    assert "already claimed or accepted" in cancel.json()["detail"]["message"]
    provider_release.set()
    await asyncio.wait_for(worker, timeout=5)

    await set_tenant_context(db_session, str(test_tenant.id))
    db_session.expire_all()
    final_task = await db_session.get(Task, task.id)
    final_run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert final_task.status == "in_progress"
    assert final_run.status == "submitted"
    assert final_run.delivery_certainty == "provider_accepted"


@pytest.mark.asyncio
async def test_failed_sms_attempt_requires_a_new_exact_reviewed_action_before_approval(
    db_session, test_tenant, test_user
):
    seeded = await _seed_lifecycle(
        db_session,
        tenant=test_tenant,
        user=test_user,
        suffix="failed-action-replacement",
        phone="+15551234588",
    )
    original = SmsClientAction(
        type="sms_client",
        recipient_bindings=[
            ResolvedSmsRecipientBinding(
                party_id=seeded.party.id,
                contact_id=seeded.contact.id,
                phone=seeded.contact.phone,
            )
        ],
        body="Original provider-accepted SMS",
        category="appointment",
        matter_id=seeded.matter.id,
        consent_evidence=[_consent_evidence(seeded)],
        idempotency_key="sms-failed-action-original",
    )
    task = Task(
        tenant_id=test_tenant.id,
        title="Replace failed SMS action",
        matter_id=seeded.matter.id,
        contact_id=seeded.contact.id,
        status="in_progress",
        source="assistant",
        pending_action=original.model_dump(mode="json"),
        created_by_user_id=test_user.id,
    )
    db_session.add(task)
    await db_session.flush()
    failed = TaskAutomationRun(
        tenant_id=test_tenant.id,
        task_id=task.id,
        action_type="sms_client",
        idempotency_key="prior-provider-accepted-attempt",
        action_snapshot=task.pending_action,
        action_sha256=action_payload_sha256(task.pending_action),
        status="failed",
        delivery_certainty="provider_failed_after_acceptance",
        triggered_by_user_id=test_user.id,
    )
    db_session.add(failed)
    await db_session.commit()
    original_ack = {
        "action_sha256": action_payload_sha256(task.pending_action),
        "consent_snapshot_sha256": original.consent_evidence[0].evidence_sha256,
    }
    with pytest.raises(ActionApprovalConflict, match="cannot be reused"):
        await enqueue_durable_automation(
            db_session,
            task,
            from_status="review",
            to_status="in_progress",
            actor_user_id=test_user.id,
            sms_acknowledgement=original_ack,
        )

    replacement = original.model_copy(
        update={
            "body": "New exact reviewed SMS after provider failure",
            "idempotency_key": "sms-failed-action-replacement",
        }
    )
    task.pending_action = replacement.model_dump(mode="json")
    replacement_hash = action_payload_sha256(task.pending_action)
    approval_key = await enqueue_durable_automation(
        db_session,
        task,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
        sms_acknowledgement={
            "action_sha256": replacement_hash,
            "consent_snapshot_sha256": replacement.consent_evidence[0].evidence_sha256,
        },
    )
    assert replacement_hash in approval_key
    replacement_run = await db_session.scalar(
        select(TaskAutomationRun).where(
            TaskAutomationRun.task_id == task.id,
            TaskAutomationRun.action_sha256 == replacement_hash,
        )
    )
    assert replacement_run.status == "queued"


@pytest.mark.asyncio
async def test_workspace_mcp_sms_proposal_is_request_idempotent_and_review_only(
    db_session, test_tenant, test_user
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="mcp-idempotency"
    )
    context = ChatToolContext(
        db=db_session,
        user=test_user,
        channel="workspace_mcp",
        request_id="transport-request-1",
        idempotency_key="sms-proposal-request-1",
    )
    replay_context = ChatToolContext(
        db=db_session,
        user=test_user,
        channel="workspace_mcp",
        request_id="transport-request-2",
        idempotency_key="sms-proposal-request-1",
    )
    tool = resolve_tool("propose_client_sms")
    arguments = {
        "matter_id": str(seeded.matter.id),
        "recipient_party_ids": [str(seeded.party.id)],
        "title": "Review MCP appointment SMS",
        "body": "Your appointment is tomorrow at 10:00.",
        "category": "appointment",
    }
    first = await tool.handler(context, tool.parse_arguments(arguments))
    replay = await tool.handler(replay_context, tool.parse_arguments(arguments))
    assert replay == first
    assert first["status"] == "review"
    assert first["pending_action"]["recipient_bindings"] == [
        {
            "party_id": str(seeded.party.id),
            "contact_id": str(seeded.contact.id),
            "phone": seeded.contact.phone,
        }
    ]
    assert first["pending_action"]["consent_evidence"][0]["evidence_sha256"]
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.tenant_id == test_tenant.id,
                Task.external_ref.like("workspace-mcp:sms:%"),
            )
        )
        == 1
    )
    binding = await db_session.scalar(
        select(DurableJob).where(
            DurableJob.tenant_id == test_tenant.id,
            DurableJob.kind == "workspace_mcp_sms_proposal",
        )
    )
    assert binding is not None
    assert binding.status == "completed"
    assert binding.result == {"task_id": first["task_id"]}
    assert binding.payload["request_sha256"]
    assert binding.idempotency_key != "sms-proposal-request-1"

    other_matter, other_party = await _create_second_sms_matter(
        db_session,
        tenant=test_tenant,
        user=test_user,
        contact=seeded.contact,
    )
    for changed in (
        {**arguments, "body": "Different request body"},
        {
            **arguments,
            "matter_id": str(other_matter.id),
            "recipient_party_ids": [str(other_party.id)],
        },
    ):
        with pytest.raises(ChatToolError) as mismatch:
            await tool.handler(
                replay_context,
                tool.parse_arguments(changed),
            )
        assert mismatch.value.code == "idempotency_conflict"

    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.tenant_id == test_tenant.id,
                Task.external_ref.like("workspace-mcp:sms:%"),
            )
        )
        == 1
    )

    external_source_id = "courtlistener:mutable-sms-authority"
    external_context = ChatToolContext(
        db=db_session,
        user=test_user,
        channel="workspace_mcp",
        request_id="sms-proposal-request-external",
        allowed_sources=[
            {
                "source_id": external_source_id,
                "case_name": "Mutable public authority",
                "url": "https://www.courtlistener.com/opinion/123/example/",
                "source_type": "public_authority",
            }
        ],
    )
    external = await tool.handler(
        external_context,
        tool.parse_arguments(
            {
                **arguments,
                "title": "Review externally sourced SMS",
                "source_ids": [external_source_id],
            }
        ),
    )
    external_action = SmsClientAction.model_validate(external["pending_action"])
    assert external_action.sources[0]["verification_state"] == ("unverified_external")
    assert external_action.sources[0]["snapshot_sha256"] is None
    external_task = await db_session.get(Task, uuid.UUID(external["task_id"]))
    assert not await _action_sources_are_current(
        db_session, external_task, external_action
    )

    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.tenant_id == test_tenant.id,
                Task.external_ref.like("workspace-mcp:sms:%"),
            )
        )
        == 2
    )


async def _create_second_sms_matter(db, *, tenant, user, contact):
    matter = Matter(
        tenant_id=tenant.id,
        user_id=user.id,
        slug=f"sms-idempotency-other-{uuid.uuid4().hex[:8]}",
        matter_name="SMS idempotency other matter",
        client_contact_id=contact.id,
    )
    db.add(matter)
    await db.flush()
    party = MatterParty(
        tenant_id=tenant.id,
        matter_id=matter.id,
        contact_id=contact.id,
        role="client",
        is_primary=True,
    )
    db.add(party)
    await db.commit()
    return matter, party


@pytest.mark.asyncio
async def test_workspace_mcp_sms_idempotency_is_race_safe_across_sessions(
    db_session, test_engine, test_tenant, test_user, monkeypatch
):
    seeded = await _seed_lifecycle(
        db_session, tenant=test_tenant, user=test_user, suffix="mcp-race"
    )
    tool = resolve_tool("propose_client_sms")
    arguments = {
        "matter_id": str(seeded.matter.id),
        "recipient_party_ids": [str(seeded.party.id)],
        "title": "Review concurrent MCP SMS",
        "body": "Your appointment is tomorrow at 10:00.",
        "category": "appointment",
    }
    parsed = tool.parse_arguments(arguments)
    original_resolve_sources = chat_handlers._resolve_source_chips
    both_ready = asyncio.Event()
    arrivals = 0

    async def synchronize_before_reservation(*args, **kwargs):
        nonlocal arrivals
        result = await original_resolve_sources(*args, **kwargs)
        arrivals += 1
        if arrivals == 2:
            both_ready.set()
        await asyncio.wait_for(both_ready.wait(), timeout=5)
        return result

    monkeypatch.setattr(
        chat_handlers, "_resolve_source_chips", synchronize_before_reservation
    )
    maker = async_sessionmaker(test_engine, expire_on_commit=False)

    async def propose(request_id: str):
        async with maker() as db:
            await set_tenant_context(db, str(test_tenant.id))
            user = await db.scalar(
                select(User).where(
                    User.tenant_id == test_tenant.id,
                    User.id == test_user.id,
                )
            )
            context = ChatToolContext(
                db=db,
                user=user,
                channel="workspace_mcp",
                request_id=request_id,
                idempotency_key="sms-proposal-concurrent-1",
            )
            result = await tool.handler(context, parsed)
            await db.commit()
            return result

    first, replay = await asyncio.gather(
        propose("transport-concurrent-1"),
        propose("transport-concurrent-2"),
    )

    assert replay == first
    assert arrivals == 2
    await set_tenant_context(db_session, str(test_tenant.id))
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.tenant_id == test_tenant.id,
                Task.external_ref.like("workspace-mcp:sms:%"),
            )
        )
        == 1
    )
    task = await db_session.get(Task, uuid.UUID(first["task_id"]))
    assert task is not None
    assert "sms-proposal-concurrent-1" not in task.external_ref
    bindings = list(
        (
            await db_session.scalars(
                select(DurableJob).where(
                    DurableJob.tenant_id == test_tenant.id,
                    DurableJob.kind == "workspace_mcp_sms_proposal",
                )
            )
        ).all()
    )
    assert len(bindings) == 1
    assert bindings[0].result == {"task_id": first["task_id"]}
    assert bindings[0].idempotency_key != "sms-proposal-concurrent-1"

    await db_session.delete(task)
    await db_session.commit()
    async with maker() as replay_db:
        await set_tenant_context(replay_db, str(test_tenant.id))
        replay_user = await replay_db.scalar(
            select(User).where(
                User.tenant_id == test_tenant.id,
                User.id == test_user.id,
            )
        )
        replay_context = ChatToolContext(
            db=replay_db,
            user=replay_user,
            channel="workspace_mcp",
            request_id="transport-after-delete",
            idempotency_key="sms-proposal-concurrent-1",
        )
        with pytest.raises(ChatToolError) as unavailable:
            await tool.handler(replay_context, parsed)
        assert unavailable.value.code == "idempotency_replay_unavailable"

    await set_tenant_context(db_session, str(test_tenant.id))
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(DurableJob)
            .where(
                DurableJob.tenant_id == test_tenant.id,
                DurableJob.kind == "workspace_mcp_sms_proposal",
            )
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.tenant_id == test_tenant.id,
                Task.external_ref.like("workspace-mcp:sms:%"),
            )
        )
        == 0
    )


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
    unassigned = User(
        tenant_id=test_tenant.id,
        email=f"sms-unassigned-{uuid.uuid4().hex}@example.invalid",
        full_name="SMS unassigned",
        role="user",
    )
    role = Role(
        tenant_id=test_tenant.id,
        name=f"SMS case staff {uuid.uuid4().hex[:8]}",
        capabilities=["manage_matters"],
    )
    db_session.add_all([allowed, denied, unassigned, role])
    await db_session.flush()
    db_session.add_all(
        [
            UserRole(
                tenant_id=test_tenant.id,
                user_id=allowed.id,
                role_id=role.id,
                source="manual",
            ),
            UserRole(
                tenant_id=test_tenant.id,
                user_id=unassigned.id,
                role_id=role.id,
                source="manual",
            ),
            MatterAssignment(
                tenant_id=test_tenant.id,
                matter_id=seeded.matter.id,
                user_id=allowed.id,
                role="associate",
            ),
        ]
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
    hidden = await client.post(
        "/api/sms/send",
        json={**payload, "idempotency_key": "sms-rbac-unassigned"},
        headers={"Authorization": f"Bearer {_user_token(unassigned)}"},
    )
    assert hidden.status_code == 404
    assert len(provider.calls) == 0
    consent_forbidden = await client.post(
        f"/api/intake/leads/{seeded.lead.id}/consent",
        json={
            "email_allowed": True,
            "sms_allowed": False,
            "phone_verified": False,
            "disclosure_version": "sms-v3",
        },
        headers={"Authorization": f"Bearer {_user_token(allowed)}"},
    )
    assert consent_forbidden.status_code == 403
    permitted = await client.post(
        "/api/sms/send",
        json=payload,
        headers={"Authorization": f"Bearer {_user_token(allowed)}"},
    )
    assert permitted.status_code == 200
    assert permitted.json()["status"] == "submitted"
    assert len(provider.calls) == 1
    sent_message = await db_session.get(SmsMessage, uuid.UUID(permitted.json()["id"]))
    sent_message.status = "provider_unknown"
    sent_message.delivery_certainty = "outcome_unknown"
    sent_message.reconciliation_required_at = datetime.now(timezone.utc)
    inbound_message = SmsMessage(
        tenant_id=test_tenant.id,
        idempotency_key="sms-rbac-review",
        request_digest="d" * 64,
        direction="inbound",
        status="review_required",
        delivery_certainty="confirmed_received",
        from_number=seeded.contact.phone,
        to_number="+15550001111",
        body="Route this reply",
        category="customer_reply",
    )
    db_session.add(inbound_message)
    await db_session.flush()
    review_item = SmsReviewItem(
        tenant_id=test_tenant.id,
        sms_message_id=inbound_message.id,
        reason="ambiguous_inbound_route",
        candidate_contact_ids=[str(seeded.contact.id)],
        candidate_matter_ids=[str(seeded.matter.id)],
    )
    db_session.add(review_item)
    await db_session.commit()
    denied_headers = {"Authorization": f"Bearer {_user_token(denied)}"}
    explicit_sms = await client.get(
        "/api/communications?channel=sms", headers=denied_headers
    )
    assert explicit_sms.status_code == 403
    general = await client.get("/api/communications", headers=denied_headers)
    assert general.status_code == 200
    assert all(item["channel"] != "sms" for item in general.json()["items"])
    detail = await client.get(
        f"/api/communications/{sent_message.communication_log_id}",
        headers=denied_headers,
    )
    assert detail.status_code == 404
    contact_history = await client.get(
        f"/api/contacts/{seeded.contact.id}/communications",
        headers=denied_headers,
    )
    assert contact_history.status_code == 200
    assert all(item["channel"] != "sms" for item in contact_history.json()["items"])
    unassigned_headers = {"Authorization": f"Bearer {_user_token(unassigned)}"}
    unassigned_timeline = await client.get(
        f"/api/contacts/{seeded.contact.id}/communications",
        headers=unassigned_headers,
    )
    assert unassigned_timeline.status_code == 200
    assert all(item["channel"] != "sms" for item in unassigned_timeline.json()["items"])
    unassigned_detail = await client.get(
        f"/api/communications/{sent_message.communication_log_id}",
        headers=unassigned_headers,
    )
    assert unassigned_detail.status_code == 404
    unassigned_review = await client.get(
        "/api/sms/review",
        headers=unassigned_headers,
    )
    assert unassigned_review.status_code == 200
    assert all(row["id"] != str(review_item.id) for row in unassigned_review.json())
    unassigned_reconciliation = await client.get(
        "/api/sms/reconciliation",
        headers=unassigned_headers,
    )
    assert unassigned_reconciliation.status_code == 200
    assert all(
        row["id"] != str(sent_message.id) for row in unassigned_reconciliation.json()
    )
    hidden_reconciliation_detail = await client.get(
        f"/api/sms/reconciliation/{sent_message.id}",
        headers=unassigned_headers,
    )
    assert hidden_reconciliation_detail.status_code == 404
    denied_review = await client.get("/api/sms/review", headers=denied_headers)
    assert denied_review.status_code == 403
    allowed_detail = await client.get(
        f"/api/communications/{sent_message.communication_log_id}",
        headers={"Authorization": f"Bearer {_user_token(allowed)}"},
    )
    assert allowed_detail.status_code == 200
    assert allowed_detail.json()["channel"] == "sms"
    allowed_headers = {"Authorization": f"Bearer {_user_token(allowed)}"}
    allowed_review = await client.get("/api/sms/review", headers=allowed_headers)
    assert str(review_item.id) in {row["id"] for row in allowed_review.json()}
    allowed_reconciliation = await client.get(
        "/api/sms/reconciliation",
        headers=allowed_headers,
    )
    assert str(sent_message.id) in {row["id"] for row in allowed_reconciliation.json()}
    allowed_reconciliation_detail = await client.get(
        f"/api/sms/reconciliation/{sent_message.id}",
        headers=allowed_headers,
    )
    assert allowed_reconciliation_detail.status_code == 200
    send_audit = await db_session.scalar(
        select(OperatorAuditLog).where(
            OperatorAuditLog.action == "sms.send.submitted",
            OperatorAuditLog.resource_id == permitted.json()["id"],
        )
    )
    assert send_audit.metadata_json["tenant_id"] == str(test_tenant.id)
    assert send_audit.metadata_json["category"] == "appointment"
    assert "body" not in send_audit.metadata_json
    assert "phone" not in send_audit.metadata_json
