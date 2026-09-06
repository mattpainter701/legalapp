"""Executable intake invariants with controlled mail, storage and clock adapters."""

import io
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError

from app.routers import matter_intake as r
from app.schemas.matter_intake import (
    IntakeAnswers,
    IntakeMeeting,
    IntakeReceipt,
    IntakeRetry,
    IntakeStart,
)
from app.services import matter_intake as s

TIME = datetime(2026, 9, 6, 14, tzinfo=timezone.utc)


class DB:
    def __init__(self, rows):
        self.rows = rows
        self.tasks = {}
        self.added = []
        self.commits = 0

    async def scalar(self, query):
        description = query.column_descriptions[0]
        entity = description["entity"]
        if entity is s.Task:
            value = self.tasks.get(query.compile().params.get("id_1"))
        else:
            value = self.rows.get(entity)
        return value if description["expr"] is entity or value is None else value.id

    async def get(self, model, key):
        return self.tasks.get(key) if model is s.Task else self.rows.get(model)

    async def scalars(self, query):
        return SimpleNamespace(all=lambda: [])

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass

    def add(self, row):
        self.added.append(row)
        if isinstance(row, s.Task):
            row.version = 1
            self.tasks[row.id] = row
        else:
            self.rows[type(row)] = row


@pytest.fixture
def ctx(monkeypatch):
    tenant_id = uuid.uuid4()
    user = s.User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        full_name="Staff",
        email="staff@firm.example",
        is_active=True,
        role="user",
    )
    contact = s.Contact(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",
        phone="+13125550123",
    )
    matter = s.Matter(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user.id,
        slug="smith",
        matter_name="Smith case",
        client_contact_id=contact.id,
        status="open",
        is_closed=False,
        cloud_folder={"google_drive": {"matter_folder_id": "folder"}},
    )
    signature = s.SignatureRequest(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        matter_id=matter.id,
        status="sent",
        source_document_sha256="a" * 64,
    )
    invite = s.ClientPortalInvite(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        matter_id=matter.id,
        contact_id=contact.id,
        email=contact.email,
        expires_at=TIME + timedelta(days=30),
        revoked=False,
    )
    packet = s.MatterIntake(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        matter_id=matter.id,
        contact_id=contact.id,
        owner_id=user.id,
        created_by=user.id,
        signature_id=signature.id,
        invite_id=invite.id,
        status="awaiting_documents",
        encrypted_invite="sealed",
        config={
            "email": contact.email,
            "channels": ["email", "sms"],
            "timezone": "America/Chicago",
            "source_sha256": signature.source_document_sha256,
            "questions": [
                {"key": "summary", "label": "Describe your matter", "required": True}
            ],
        },
        requirements={
            "fee_agreement": {"completed": False},
            "questionnaire": {"completed": False},
        },
        delivery={},
        answers={},
    )
    doc = s.MatterDocument(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        matter_id=matter.id,
        filename="executed.pdf",
    )
    db = DB(
        {
            s.User: user,
            s.Contact: contact,
            s.Matter: matter,
            s.SignatureRequest: signature,
            s.ClientPortalInvite: invite,
            s.MatterIntake: packet,
            s.MatterDocument: doc,
        }
    )
    monkeypatch.setattr(s, "now", lambda: TIME)
    monkeypatch.setattr(s, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(r, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(s, "can_access_matter", AsyncMock(return_value=True))
    monkeypatch.setattr(r, "can_access_matter", AsyncMock(return_value=True))
    monkeypatch.setattr(
        s,
        "get_user_capabilities",
        AsyncMock(return_value={"manage_matters", "manage_intake"}),
    )
    monkeypatch.setattr(s, "decrypt_token", lambda value: "secret-invite")
    monkeypatch.setattr(s, "encrypt_token", lambda value: "sealed")
    monkeypatch.setattr(
        s,
        "get_settings",
        lambda: SimpleNamespace(FRONTEND_URL="https://portal.example"),
    )
    monkeypatch.setattr(s, "load_sms_consents", AsyncMock(return_value=[]))
    stored = SimpleNamespace(
        succeeded=True,
        storage_path="cloud/item",
        provider="google_drive",
        backend="google_drive",
        provider_item_id="item",
        drive_id="drive",
        parent_id="parent",
    )
    monkeypatch.setattr(s, "store_file", AsyncMock(return_value=stored))
    monkeypatch.setattr(
        s,
        "send_client_email",
        AsyncMock(
            return_value=SimpleNamespace(
                delivery_certainty="confirmed_sent", provider="google"
            )
        ),
    )
    monkeypatch.setattr(
        s,
        "send_sms",
        AsyncMock(return_value=SimpleNamespace(delivery_certainty="provider_accepted")),
    )

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(s, "async_session_maker", session)
    portal_ctx = SimpleNamespace(
        tenant_id=str(tenant_id), contact_id=str(contact.id), email=contact.email
    )
    return SimpleNamespace(
        db=db,
        user=user,
        contact=contact,
        matter=matter,
        signature=signature,
        invite=invite,
        packet=packet,
        doc=doc,
        resolved=(portal_ctx, matter),
        stored=stored,
    )


def start_body(c, **overrides):
    return IntakeStart(
        email=c.contact.email,
        channels=["email"],
        questions=c.packet.config["questions"],
        confirm_send=True,
        **overrides,
    )


def complete(c, key, at=TIME):
    c.packet.requirements = {
        **c.packet.requirements,
        key: {"completed": True, "completed_at": at.isoformat()},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["fee_agreement", "questionnaire"])
async def test_both_required_in_either_order_and_completion_replays(ctx, first):
    c = ctx
    c.packet.sent_at = TIME - timedelta(days=8)
    complete(c, first, TIME - timedelta(hours=1))
    await s.reconcile(c.db, c.packet)
    assert c.packet.completed_at is None
    task = c.db.tasks[uuid.uuid5(c.packet.id, "documents")]
    assert task.due_date.isoformat() == "2026-09-05"
    assert len(c.packet.delivery) == 2  # one reminder per selected channel
    complete(c, "questionnaire" if first == "fee_agreement" else "fee_agreement")
    await s.reconcile(c.db, c.packet)
    assert c.packet.completed_at == TIME and task.status == "cancelled"
    schedule = c.db.tasks[uuid.uuid5(c.packet.id, "scheduling")]
    assert schedule.due_date.isoformat() == "2026-09-07"
    assert schedule.due_time.hour == 9  # 14:00 UTC in Chicago
    event_count = len([e for e in c.db.added if isinstance(e, s.MatterEvent)])
    await s.reconcile(c.db, c.packet)
    assert len(c.db.tasks) == 2
    assert len([e for e in c.db.added if isinstance(e, s.MatterEvent)]) == event_count


@pytest.mark.asyncio
async def test_signature_requires_artifact_and_bound_source(ctx):
    c = ctx
    c.signature.status = "completed"
    c.signature.completed_at = TIME
    c.signature.completion_artifact_sha256 = "b" * 64
    c.signature.provider_envelope_id = "invalid"
    await s.reconcile(c.db, c.packet)
    assert not c.packet.requirements["fee_agreement"]["completed"]
    c.signature.provider_envelope_id = str(c.doc.id)
    c.signature.source_document_sha256 = "changed"
    await s.reconcile(c.db, c.packet)
    assert not c.packet.requirements["fee_agreement"]["completed"]
    c.signature.source_document_sha256 = "a" * 64
    await s.reconcile(c.db, c.packet)
    assert c.packet.requirements["fee_agreement"]["completed"]
    assert c.packet.completed_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["microsoft", "google"])
async def test_connected_email_claim_before_io_and_followup_time(
    ctx, monkeypatch, provider
):
    c = ctx
    s.queue(c.packet, "welcome")

    async def send(*args, **kwargs):
        assert c.db.commits and c.packet.delivery["welcome:email"]["state"] == "sending"
        assert kwargs["actor_user_id"] == c.user.id
        assert kwargs["to"] == [c.contact.email]
        return SimpleNamespace(delivery_certainty="confirmed_sent", provider=provider)

    monkeypatch.setattr(s, "send_client_email", AsyncMock(side_effect=send))
    await s.deliver(c.db, c.packet, "welcome:email")
    assert c.packet.sent_at == TIME
    assert c.packet.delivery["welcome:email"]["provider"] == provider
    assert (
        c.db.tasks[uuid.uuid5(c.packet.id, "documents")].due_date.isoformat()
        == "2026-09-13"
    )
    assert all(
        "secret-invite" not in (e.body or "")
        for e in c.db.added
        if isinstance(e, s.CommunicationLog)
    )
    await s.deliver(c.db, c.packet, "welcome:email")
    assert s.send_client_email.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "certainty,expected", [("not_attempted", "failed"), ("outcome_unknown", "unknown")]
)
async def test_failure_creates_staff_work_without_false_sent(
    ctx, monkeypatch, certainty, expected
):
    c = ctx
    s.queue(c.packet, "welcome")
    monkeypatch.setattr(
        s,
        "send_client_email",
        AsyncMock(
            return_value=SimpleNamespace(
                delivery_certainty=certainty, provider="microsoft"
            )
        ),
    )
    await s.deliver(c.db, c.packet, "welcome:email")
    assert (
        c.packet.sent_at is None
        and c.packet.delivery["welcome:email"]["state"] == expected
    )
    assert uuid.uuid5(c.packet.id, "delivery") in c.db.tasks
    assert uuid.uuid5(c.packet.id, "documents") not in c.db.tasks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blocked", ["actor", "contact", "invite", "revoked", "expired"]
)
async def test_recheck_before_send(ctx, monkeypatch, blocked):
    c = ctx
    s.queue(c.packet, "welcome")
    if blocked == "actor":
        monkeypatch.setattr(s, "get_user_capabilities", AsyncMock(return_value=set()))
    if blocked == "contact":
        c.contact.email = "changed@example.com"
    if blocked == "invite":
        c.db.rows[s.ClientPortalInvite] = None
    if blocked == "revoked":
        c.invite.revoked = True
    if blocked == "expired":
        c.invite.expires_at = TIME - timedelta(seconds=1)
    await s.deliver(c.db, c.packet, "welcome:email")
    assert c.packet.delivery["welcome:email"]["state"] == "blocked"
    s.send_client_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_sms_quiet_hours_defer_without_duplicate_send(ctx, monkeypatch):
    c = ctx
    s.queue(c.packet, "welcome")
    monkeypatch.setattr(
        s,
        "send_sms",
        AsyncMock(side_effect=s.SmsError("quiet", code="sms_quiet_hours")),
    )
    await s.deliver(c.db, c.packet, "welcome:sms")
    state = c.packet.delivery["welcome:sms"]
    assert state["state"] == "queued" and state["attempt"] == 1
    assert not c.db.tasks
    await s.deliver(c.db, c.packet, "welcome:sms")
    assert s.send_sms.await_count == 1
    c.packet.delivery["welcome:sms"].pop("not_before")
    monkeypatch.setattr(
        s,
        "send_sms",
        AsyncMock(return_value=SimpleNamespace(delivery_certainty="provider_accepted")),
    )
    await s.deliver(c.db, c.packet, "welcome:sms")
    assert c.packet.delivery["welcome:sms"]["state"] == "sent"
    assert s.send_sms.call_args.kwargs["idempotency_key"].endswith(":1")


@pytest.mark.asyncio
async def test_send_exception_becomes_unknown_and_requires_explicit_retry(
    ctx, monkeypatch
):
    c = ctx
    s.queue(c.packet, "welcome")
    monkeypatch.setattr(s, "send_client_email", AsyncMock(side_effect=TimeoutError()))
    await s.deliver(c.db, c.packet, "welcome:email")
    assert c.packet.delivery["welcome:email"]["state"] == "unknown"
    await r.retry(
        c.matter.id,
        IntakeRetry(delivery_key="welcome:email", confirm_not_sent=True),
        c.db,
        c.user,
    )
    assert c.packet.delivery["welcome:email"] == {"state": "queued", "attempt": 1}
    with pytest.raises(HTTPException):
        await r.retry(
            c.matter.id,
            IntakeRetry(delivery_key="welcome:email", confirm_not_sent=True),
            c.db,
            c.user,
        )


@pytest.mark.asyncio
async def test_completed_and_cancelled_suppress_document_reminders(ctx):
    c = ctx
    s.queue(c.packet, "reminder")
    c.packet.completed_at = TIME
    await s.deliver(c.db, c.packet, "reminder:email")
    assert c.packet.delivery["reminder:email"]["state"] == "cancelled"
    await r.cancel(c.matter.id, c.db, c.user)
    assert c.packet.status == "cancelled" and c.signature.status == "voided"
    await s.deliver(c.db, c.packet, "reminder:sms")
    s.send_sms.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["awaiting_documents", "scheduled"])
async def test_closed_matter_cancels_even_scheduled_packets(ctx, status):
    c = ctx
    c.packet.status = status
    c.matter.is_closed = True
    await s.reconcile(c.db, c.packet)
    assert c.packet.status == "cancelled"


@pytest.mark.asyncio
async def test_questionnaire_validation_receipt_and_replay(ctx):
    c = ctx
    for answers in ({}, {"unknown": "x"}, {"summary": "x" * 20001}):
        with pytest.raises(HTTPException):
            await r.submit(
                IntakeAnswers(answers=answers, confirm_complete=True), c.resolved, c.db
            )
    payload = IntakeAnswers(answers={"summary": "My matter"}, confirm_complete=True)
    result = await r.submit(payload, c.resolved, c.db)
    assert result["requirements"]["questionnaire"]["completed"]
    assert not result["requirements"]["fee_agreement"]["completed"]
    assert s.store_file.await_count == 1
    await r.submit(payload, c.resolved, c.db)
    assert s.store_file.await_count == 1
    with pytest.raises(HTTPException):
        await r.submit(
            IntakeAnswers(answers={"summary": "Changed"}, confirm_complete=True),
            c.resolved,
            c.db,
        )
    await r.receipt(
        c.matter.id,
        IntakeReceipt(
            requirement="fee_agreement",
            document_id=c.doc.id,
            note="Verified external signature",
        ),
        c.db,
        c.user,
    )
    assert c.packet.completed_at == TIME
    public = s.public_packet(c.packet, client=True)
    assert (
        "note" not in public["requirements"]["fee_agreement"]
        and "delivery" not in public
    )
    assert (await r.submit(payload, c.resolved, c.db))["status"] == "documents_complete"


@pytest.mark.asyncio
async def test_portal_recipient_isolation(ctx):
    c = ctx
    c.resolved[0].contact_id = str(uuid.uuid4())
    with pytest.raises(HTTPException):
        await r.client_read(c.resolved, c.db)
    c.resolved[0].contact_id = str(c.contact.id)
    c.resolved[0].email = "different@example.com"
    with pytest.raises(HTTPException):
        await r.submit(
            IntakeAnswers(answers={"summary": "x"}, confirm_complete=True),
            c.resolved,
            c.db,
        )


@pytest.mark.asyncio
async def test_meeting_requires_both_and_closes_scheduling_task(ctx):
    c = ctx
    body = IntakeMeeting(
        kind="conference_call",
        starts_at=TIME + timedelta(days=2),
        details="Call office",
    )
    with pytest.raises(HTTPException):
        await r.meeting(c.matter.id, body, c.db, c.user)
    complete(c, "fee_agreement")
    complete(c, "questionnaire")
    result = await r.meeting(c.matter.id, body, c.db, c.user)
    assert result["status"] == "scheduled"
    assert c.db.tasks[uuid.uuid5(c.packet.id, "scheduling")].status == "cancelled"
    assert (await r.meeting(c.matter.id, body, c.db, c.user)) == result
    with pytest.raises(HTTPException):
        await r.meeting(
            c.matter.id,
            IntakeMeeting(kind="in_person", starts_at=TIME, details="Office"),
            c.db,
            c.user,
        )


@pytest.mark.asyncio
async def test_start_creates_packet_and_signature_once(ctx):
    c = ctx
    body = start_body(c)
    c.db.rows[s.MatterIntake] = None
    packet = await s.start_packet(
        c.db, c.user, c.matter, body, "Fee agreement.pdf", b"%PDF-reviewed"
    )
    assert packet.status == "awaiting_documents"
    assert c.matter.portal_enabled and c.matter.stage == "Intake / Awaiting Documents"
    assert packet.delivery["welcome:email"]["state"] == "queued"
    count = len(c.db.added)
    assert (
        await s.start_packet(
            c.db, c.user, c.matter, body, "Fee agreement.pdf", b"%PDF-reviewed"
        )
        is packet
    )
    assert len(c.db.added) == count
    with pytest.raises(HTTPException):
        await s.start_packet(
            c.db, c.user, c.matter, body, "Fee agreement.pdf", b"%PDF-changed"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        "closed",
        "no_client",
        "email_changed",
        "owner",
        "pdf",
        "storage",
        "sms_permission",
    ],
)
async def test_start_failures_do_not_send(ctx, monkeypatch, invalid):
    c = ctx
    body = start_body(c)
    c.db.rows[s.MatterIntake] = None
    content = b"%PDF-reviewed"
    if invalid == "closed":
        c.matter.is_closed = True
    if invalid == "no_client":
        c.db.rows[s.Contact] = None
    if invalid == "email_changed":
        c.contact.email = "other@example.com"
    if invalid == "owner":
        monkeypatch.setattr(s, "can_access_matter", AsyncMock(return_value=False))
    if invalid == "pdf":
        content = b"not pdf"
    if invalid == "storage":
        c.stored.succeeded = False
    if invalid == "sms_permission":
        body.channels = ["sms"]
    with pytest.raises(HTTPException):
        await s.start_packet(c.db, c.user, c.matter, body, "Fee agreement.pdf", content)
    s.send_sms.assert_not_awaited()
    s.send_client_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_sms_consent_is_audited_and_existing_consent_is_not_overwritten(ctx):
    c = ctx
    c.db.rows[s.MatterIntake] = None
    body = start_body(c)
    body.channels = ["sms"]
    body.sms_permission_verified = True
    await s.start_packet(c.db, c.user, c.matter, body, "fee.pdf", b"%PDF-reviewed")
    consent = c.db.rows[s.LeadChannelConsent]
    assert consent.sms_status == "active" and consent.allowed_categories == ["intake"]
    assert c.contact.sms_opt_in
    assert any(type(row).__name__ == "SmsConsentEvent" for row in c.db.added)


@pytest.mark.asyncio
async def test_read_and_start_endpoints_reject_invalid_input(ctx, monkeypatch):
    c = ctx
    with pytest.raises(HTTPException):
        await r.start(c.matter.id, "{}", UploadFile(io.BytesIO(b"x")), c.db, c.user)
    assert (await r.read(c.matter.id, c.db, c.user))["status"] == "awaiting_documents"
    monkeypatch.setattr(r, "can_access_matter", AsyncMock(return_value=False))
    with pytest.raises(HTTPException):
        await r.read(c.matter.id, c.db, c.user)


@pytest.mark.parametrize(
    "changes",
    [
        {"timezone": "bad/zone"},
        {"channels": ["email", "email"]},
        {"questions": [{"key": "x", "label": "x"}, {"key": "x", "label": "y"}]},
    ],
)
def test_schema_rejects_invalid_configuration(changes):
    values = dict(
        email="a@example.com",
        channels=["email"],
        questions=[{"key": "x", "label": "x"}],
        confirm_send=True,
    )
    with pytest.raises(ValidationError):
        IntakeStart(**{**values, **changes})


def test_meeting_requires_timezone_and_messages_only_reference_missing_items(ctx):
    with pytest.raises(ValidationError):
        IntakeMeeting(
            kind="in_person", starts_at=datetime(2026, 9, 7), details="Office"
        )
    complete(ctx, "fee_agreement")
    subject, body = s.message(ctx.packet, "reminder", "https://portal.example")
    assert "questionnaire" in body and "fee agreement" not in body
    assert "meeting" in s.message(ctx.packet, "meeting", "url")[0]
    assert "complete" in s.message(ctx.packet, "complete", "url")[0]


@pytest.mark.asyncio
async def test_worker_recovery_never_resends_ambiguous_send(ctx, monkeypatch):
    from app.services import task_notifications as notifications

    c = ctx
    c.packet.delivery = {
        "welcome:email": {
            "state": "sending",
            "attempt": 0,
            "started_at": (TIME - timedelta(minutes=11)).isoformat(),
        }
    }
    monkeypatch.setattr(notifications, "notify_task_created", AsyncMock())
    await s.process_packet(c.user.tenant_id, c.matter.id)
    await s.process_packet(c.user.tenant_id, c.matter.id)
    assert c.packet.delivery["welcome:email"]["state"] == "unknown"
    assert uuid.uuid5(c.packet.id, "delivery") in c.db.tasks
    s.send_client_email.assert_not_awaited()
    notifications.notify_task_created.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_closes_calendar_and_notifies_scheduling_once(ctx, monkeypatch):
    from app.services import task_notifications as notifications

    c = ctx
    c.packet.sent_at = TIME
    await s.reconcile(c.db, c.packet)
    complete(c, "fee_agreement")
    complete(c, "questionnaire")
    monkeypatch.setattr(notifications, "notify_task_created", AsyncMock())
    monkeypatch.setattr(
        notifications,
        "remove_task_from_calendars_now",
        AsyncMock(return_value=[True, True]),
    )
    await s.process_packet(c.user.tenant_id, c.matter.id)
    await s.process_packet(c.user.tenant_id, c.matter.id)
    notifications.remove_task_from_calendars_now.assert_awaited_once()
    notifications.notify_task_created.assert_awaited_once()
    assert c.db.tasks[uuid.uuid5(c.packet.id, "documents")].status == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "certainty", ["confirmed_sent", "provider_failed_after_acceptance"]
)
async def test_worker_projects_sms_callback_and_delivery_attention(
    ctx, monkeypatch, certainty
):
    from app.services import task_notifications as notifications

    c = ctx
    sms = s.SmsMessage(
        id=uuid.uuid4(),
        tenant_id=c.user.tenant_id,
        delivery_certainty=certainty,
        last_event_at=TIME,
    )
    c.db.rows[s.SmsMessage] = sms
    c.packet.delivery = {
        "welcome:sms": {"state": "unknown", "attempt": 0, "sms_message_id": str(sms.id)}
    }
    monkeypatch.setattr(notifications, "notify_task_created", AsyncMock())
    await s.process_packet(c.user.tenant_id, c.matter.id)
    if certainty == "confirmed_sent":
        assert c.packet.sent_at == TIME
        assert uuid.uuid5(c.packet.id, "documents") in c.db.tasks
    else:
        assert c.packet.delivery["welcome:sms"]["state"] == "failed"
        assert uuid.uuid5(c.packet.id, "delivery") in c.db.tasks
    s.send_sms.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("signature_status", ["sent", "expired"])
async def test_renewal_revokes_old_portal_invite_and_preserves_deadlines(
    ctx, signature_status
):
    c = ctx
    c.signature.status = signature_status
    c.packet.sent_at = TIME - timedelta(days=8)
    old = c.packet.invite_id
    await r.renew_invitation(c.matter.id, c.db, c.user)
    assert c.invite.revoked and c.packet.invite_id != old
    assert c.packet.sent_at == TIME - timedelta(days=8)
    assert c.packet.delivery["welcome:email"]["state"] == "queued"
    assert c.signature.status == "sent"
    assert c.signature.expires_at == TIME + timedelta(days=30)
    c.packet.delivery = {"welcome:email": {"state": "sending"}}
    with pytest.raises(HTTPException):
        await r.renew_invitation(c.matter.id, c.db, c.user)


@pytest.mark.asyncio
async def test_recording_external_signature_voids_pending_portal_request(ctx):
    c = ctx
    await r.receipt(
        c.matter.id,
        IntakeReceipt(
            requirement="fee_agreement",
            document_id=c.doc.id,
            note="Verified signed original",
        ),
        c.db,
        c.user,
    )
    assert c.signature.status == "voided"
    assert c.packet.requirements["fee_agreement"]["completed"]
    assert c.packet.completed_at is None


@pytest.mark.asyncio
async def test_invalid_mobile_is_a_reviewable_validation_error(ctx):
    c = ctx
    c.db.rows[s.MatterIntake] = None
    c.contact.phone = "not a number"
    body = start_body(c)
    body.channels = ["sms"]
    body.sms_permission_verified = True
    with pytest.raises(HTTPException) as exc:
        await s.start_packet(c.db, c.user, c.matter, body, "fee.pdf", b"%PDF-reviewed")
    assert exc.value.status_code == 422
    s.send_sms.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_followup_is_unassigned_when_owner_loses_matter_access(
    ctx, monkeypatch
):
    c = ctx
    monkeypatch.setattr(s, "can_access_matter", AsyncMock(return_value=False))
    c.packet.sent_at = TIME
    await s.reconcile(c.db, c.packet)
    task = c.db.tasks[uuid.uuid5(c.packet.id, "documents")]
    assert task.assigned_to_user_id is None
    assert task.matter_id == c.matter.id
