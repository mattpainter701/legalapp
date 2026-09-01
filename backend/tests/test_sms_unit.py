from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.services import google_calendar, microsoft_calendar, task_automation
from app.models.conversion_loop import LeadChannelConsent, SmsConsentEvent
from app.models.sms import (
    SmsMessage,
    SmsNumberSuppression,
    SmsNumberSuppressionEvent,
    SmsProviderConfig,
    SmsProviderCredential,
    SmsReviewItem,
)
from app.models.task import Task, TaskAutomationRun
from app.schemas.chat_action import (
    ProposeClientSmsArgs,
    ResolvedSmsRecipientBinding,
    SmsConsentEvidenceBinding,
    SmsClientAction,
    SourceDocumentBinding,
)
from app.schemas.conversion_loop import ConsentUpdate, IntakeSubmissionCreate
from app.schemas.sms import SmsProviderConfigUpdate
from app.services.automation_capabilities import capability_catalog
from app.services.sms import (
    consent_authorizes_sms,
    in_quiet_hours,
    normalize_e164,
    provider_status_transition_allowed,
    quiet_hours_configuration_valid,
    twilio_signature,
    verify_twilio_signature,
)
from app.services.sms import _request_digest


def _consent_evidence(contact_id, *, categories=None):
    values = {
        "consent_id": uuid4(),
        "contact_id": contact_id,
        "mobile_e164": "+15551234567",
        "phone_verified": True,
        "consent_source": "public_intake",
        "disclosure_version": "sms-v3",
        "consented_at": datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        "consent_expires_at": datetime(2027, 8, 1, 12, 0, tzinfo=timezone.utc),
        "consent_timezone": "America/Chicago",
        "quiet_hours_start": "21:00",
        "quiet_hours_end": "08:00",
        "allowed_categories": categories or ["staff_authored"],
    }
    provisional = SmsConsentEvidenceBinding.model_construct(
        **values, evidence_sha256=""
    )
    return SmsConsentEvidenceBinding(**values, evidence_sha256=provisional.digest())


def test_sms_destination_normalizes_only_e164_compatible_numbers():
    assert normalize_e164("+1 (555) 123-4567") == "+15551234567"
    assert normalize_e164("0015551234567") == "+15551234567"


def test_sms_destination_rejects_ambiguous_or_invalid_numbers():
    for value in (None, "555-1234", "+00012345678", "+15551234567890123"):
        try:
            normalize_e164(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid destination: {value!r}")


def test_task_delivery_certainty_dual_writes_and_reads_legacy_alias():
    run = TaskAutomationRun(
        tenant_id=uuid4(),
        task_id=uuid4(),
        action_type="sms_client",
        idempotency_key="certainty-v2",
        delivery_certainty="provider_failed_after_acceptance",
    )
    assert run.delivery_certainty == "provider_failed_after_acceptance"
    assert run._delivery_certainty_v2 == "provider_failed_after_acceptance"
    assert run._delivery_certainty_legacy == "failed_after_acceptance"

    legacy = TaskAutomationRun(
        tenant_id=uuid4(),
        task_id=uuid4(),
        action_type="email_client",
        idempotency_key="certainty-legacy",
    )
    legacy._delivery_certainty_legacy = "failed_after_acceptance"
    legacy._delivery_certainty_v2 = None
    assert legacy.delivery_certainty == "provider_failed_after_acceptance"


@pytest.mark.asyncio
async def test_task_delivery_certainty_bulk_paths_compile_against_physical_columns(
    monkeypatch,
):
    class StatementRecorder:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)

        async def scalar(self, statement):
            self.statements.append(statement)
            return None

        async def commit(self):
            return None

    async def allow_approval(_db, _actor_user_id):
        return True

    monkeypatch.setattr(
        task_automation, "_actor_can_approve_legal_work", allow_approval
    )
    recorder = StatementRecorder()
    tenant_id, task_id, actor_id = uuid4(), uuid4(), uuid4()
    task = Task(
        id=task_id,
        tenant_id=tenant_id,
        created_by_user_id=actor_id,
        title="Compile expand-contract automation statements",
        status="in_progress",
        pending_action={"type": "email_client", "to": ["client@example.test"]},
    )

    await task_automation.enqueue_automation_run(
        recorder,
        task,
        from_status="review",
        actor_user_id=actor_id,
        idempotency_key="enqueue-certainty",
    )
    await task_automation._claim_run(
        recorder,
        task,
        action_type="email_client",
        idempotency_key="claim-certainty",
        actor_user_id=actor_id,
    )
    await task_automation._record_terminal_no_send(
        recorder,
        task,
        action_type="email_client",
        idempotency_key="terminal-certainty",
        actor_user_id=actor_id,
        detail="Provider dispatch is intentionally blocked",
    )

    assert len(recorder.statements) == 3
    compiled = [
        str(statement.compile(dialect=postgresql.dialect()))
        for statement in recorder.statements
    ]
    assert all("delivery_certainty" in statement for statement in compiled)
    assert all("delivery_certainty_v2" in statement for statement in compiled)
    assert all("_delivery_certainty" not in statement for statement in compiled)


@pytest.mark.asyncio
async def test_revocation_calendar_cleanup_uses_exact_user_and_verifies_absence(
    monkeypatch,
):
    class SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    tenant_fallbacks: list[str] = []

    async def tenant_token(*_args):
        tenant_fallbacks.append("wrong-principal")
        return "tenant-token-must-not-be-used"

    async def missing_user_token(*_args):
        return None

    async def refresh_failure(*_args):
        raise RuntimeError("refresh failed")

    for calendar, user_token in (
        (google_calendar, missing_user_token),
        (microsoft_calendar, refresh_failure),
    ):
        monkeypatch.setattr(calendar, "async_session_maker", SessionContext)
        monkeypatch.setattr(calendar, "get_fresh_user_token", user_token)
        monkeypatch.setattr(calendar, "get_fresh_token", tenant_token)
        assert (
            await calendar._get_token("tenant-id", "revoked-user-id", exact_user=True)
            is None
        )
        with pytest.raises(RuntimeError, match="exact-user token"):
            await calendar.delete_task_event(
                "tenant-id",
                "task-id",
                "revoked-user-id",
                require_exact_user=True,
            )
    assert tenant_fallbacks == []

    class EmptyResponse:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class EmptyCalendarClient:
        def __init__(self, payload):
            self._payload = payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *_args, **_kwargs):
            return EmptyResponse(self._payload)

        async def delete(self, *_args, **_kwargs):
            raise AssertionError("verified absence must not issue a delete")

    async def exact_user_token(*_args, **kwargs):
        assert kwargs == {"exact_user": True}
        return "exact-user-token"

    for calendar, payload in (
        (google_calendar, {"items": []}),
        (microsoft_calendar, {"value": []}),
    ):
        monkeypatch.setattr(calendar, "_get_token", exact_user_token)
        monkeypatch.setattr(
            calendar.httpx,
            "AsyncClient",
            lambda payload=payload: EmptyCalendarClient(payload),
        )
        assert await calendar.delete_task_event(
            "tenant-id",
            "task-id",
            "revoked-user-id",
            require_exact_user=True,
        )


def test_twilio_signature_is_exact_and_rejects_tampering():
    params = {"Body": "STOP", "From": "+15551234567", "MessageSid": "SM123"}
    signature = twilio_signature(
        auth_token="secret", url="https://example.test/api/sms/webhook", params=params
    )
    assert verify_twilio_signature(
        auth_token="secret",
        url="https://example.test/api/sms/webhook",
        params=params,
        supplied=signature,
    )
    assert not verify_twilio_signature(
        auth_token="secret",
        url="https://example.test/api/sms/webhook",
        params={**params, "Body": "send money"},
        supplied=signature,
    )


def test_quiet_hours_supports_overnight_window_and_invalid_timezone_fails_closed():
    consent = SimpleNamespace(
        quiet_hours_start="21:00",
        quiet_hours_end="07:00",
        consent_timezone="America/Chicago",
    )
    assert in_quiet_hours(
        consent=consent, now=datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc)
    )
    assert not in_quiet_hours(
        consent=consent, now=datetime(2026, 9, 1, 16, 0, tzinfo=timezone.utc)
    )
    consent.consent_timezone = "not-a-real-zone"
    assert in_quiet_hours(
        consent=consent, now=datetime(2026, 9, 1, 16, 0, tzinfo=timezone.utc)
    )
    assert not quiet_hours_configuration_valid(consent)
    consent.consent_timezone = "America/Chicago"
    consent.quiet_hours_start = "25:00"
    assert in_quiet_hours(
        consent=consent, now=datetime(2026, 9, 1, 16, 0, tzinfo=timezone.utc)
    )


def test_sms_provider_activation_requires_explicit_compliance_evidence():
    with pytest.raises(ValueError, match="ownership_model"):
        SmsProviderConfigUpdate(
            account_sid="AC123",
            auth_token="auth-token",
            sender_ready=True,
            compliance_snapshot={},
        )
    config = SmsProviderConfigUpdate(
        account_sid="AC123",
        auth_token="auth-token",
        messaging_service_sid="MG123",
        sender_ready=True,
        is_active=True,
        compliance_snapshot={
            "ownership_model": "firm-owned",
            "consent_policy": "documented-opt-in",
            "quiet_hours_policy": "recipient-timezone",
        },
    )
    assert config.is_active
    with pytest.raises(ValueError, match="consent_policy"):
        SmsProviderConfigUpdate(
            account_sid="AC123",
            auth_token="auth-token",
            messaging_service_sid="MG123",
            is_active=True,
            sender_ready=True,
            compliance_snapshot={
                "ownership_model": "firm-owned",
                "consent_policy": "   ",
                "quiet_hours_policy": "recipient-timezone",
            },
        )
    with pytest.raises(ValueError, match="ready sender"):
        SmsProviderConfigUpdate(
            account_sid="AC123",
            auth_token="auth-token",
            messaging_service_sid="MG123",
            is_active=True,
            sender_ready=False,
            compliance_snapshot={
                "ownership_model": "firm-owned",
                "consent_policy": "documented-opt-in",
                "quiet_hours_policy": "recipient-timezone",
            },
        )


def test_sms_action_is_phone_bound_and_reviewable():
    party_id = uuid4()
    contact_id = uuid4()
    action = SmsClientAction(
        type="sms_client",
        recipient_bindings=[
            ResolvedSmsRecipientBinding(
                party_id=party_id, contact_id=contact_id, phone="+15551234567"
            )
        ],
        consent_evidence=[_consent_evidence(contact_id)],
        body="Your appointment is confirmed.",
        matter_id=uuid4(),
        idempotency_key="chat-sms-123456",
    )
    assert action.type == "sms_client"
    with pytest.raises(ValueError):
        ResolvedSmsRecipientBinding(
            party_id=party_id, contact_id=uuid4(), phone="555-1234"
        )
    with pytest.raises(ValueError, match="at most 1 item"):
        ProposeClientSmsArgs(
            matter_id=uuid4(),
            recipient_party_ids=[uuid4(), uuid4()],
            title="Unsafe multi-recipient SMS",
            body="One review must bind one recipient.",
        )
    sms_tool = next(
        tool for tool in capability_catalog() if tool["name"] == "propose_client_sms"
    )
    assert (
        sms_tool["input_schema"]["properties"]["recipient_party_ids"]["maxItems"] == 1
    )


def test_sms_action_binds_every_local_source_to_exact_bytes():
    document_id = uuid4()
    contact_id = uuid4()
    action = SmsClientAction(
        type="sms_client",
        recipient_bindings=[
            ResolvedSmsRecipientBinding(
                party_id=uuid4(), contact_id=contact_id, phone="+15551234567"
            )
        ],
        body="Source-bound update",
        matter_id=uuid4(),
        source_document_ids=[document_id],
        source_document_bindings=[
            SourceDocumentBinding(document_id=document_id, sha256="a" * 64)
        ],
        sources=[
            {
                "source_id": "document:1",
                "label": "Exact source",
                "snapshot_sha256": "a" * 64,
                "verification_state": "exact",
            }
        ],
        consent_evidence=[_consent_evidence(contact_id)],
        idempotency_key="chat-sms-source-123",
    )
    assert action.source_document_bindings[0].sha256 == "a" * 64
    with pytest.raises(ValueError, match="exact content binding"):
        SmsClientAction(
            type="sms_client",
            recipient_bindings=action.recipient_bindings,
            body=action.body,
            matter_id=action.matter_id,
            source_document_ids=[document_id],
            source_document_bindings=[],
            consent_evidence=action.consent_evidence,
            idempotency_key="chat-sms-source-456",
        )


def test_sms_records_have_tenant_composite_referential_guards():
    constraints = {
        constraint.name
        for table in (
            LeadChannelConsent.__table__,
            SmsConsentEvent.__table__,
            SmsProviderConfig.__table__,
            SmsProviderCredential.__table__,
            SmsNumberSuppression.__table__,
            SmsNumberSuppressionEvent.__table__,
            SmsMessage.__table__,
            SmsReviewItem.__table__,
            TaskAutomationRun.__table__,
        )
        for constraint in table.foreign_key_constraints
    }
    assert {
        "fk_sms_provider_configs_tenant_user",
        "fk_sms_provider_credentials_tenant_user",
        "fk_sms_messages_tenant_provider_credential",
        "fk_sms_number_suppression_events_tenant_suppression",
        "fk_sms_messages_tenant_contact",
        "fk_sms_messages_tenant_matter",
        "fk_sms_messages_tenant_communication",
        "fk_sms_messages_tenant_user",
        "fk_sms_messages_tenant_reconciler",
        "fk_sms_review_items_tenant_message",
        "fk_sms_review_items_tenant_user",
        "fk_lead_channel_consents_tenant_lead",
        "fk_sms_consent_events_tenant_consent",
        "fk_sms_consent_events_tenant_lead",
        "fk_sms_consent_events_tenant_contact",
        "fk_sms_consent_events_tenant_user",
        "fk_task_automation_runs_tenant_sms_message",
        "fk_task_automation_runs_tenant_task",
    } <= constraints


def test_sms_evidence_models_expose_database_coherence_guards():
    constraints = {
        constraint.name
        for table in (
            SmsConsentEvent.__table__,
            SmsProviderConfig.__table__,
            SmsProviderCredential.__table__,
            SmsNumberSuppressionEvent.__table__,
            SmsMessage.__table__,
            SmsReviewItem.__table__,
        )
        for constraint in table.constraints
    }
    assert {
        "ck_sms_consent_events_active_evidence",
        "ck_sms_provider_configs_active_evidence",
        "ck_sms_provider_credentials_retirement",
        "ck_sms_number_suppression_events_state",
        "ck_sms_messages_status_certainty",
        "ck_sms_review_items_review_evidence",
    } <= constraints
    provider_indexes = {index.name for index in SmsProviderConfig.__table__.indexes}
    assert {
        "uq_sms_provider_configs_active_account_service",
        "uq_sms_provider_configs_active_account_number",
    } <= provider_indexes
    assert TaskAutomationRun.__table__.c.delivery_certainty.type.length == 30
    assert TaskAutomationRun.__table__.c.delivery_certainty_v2.type.length == 50


def test_sms_request_digest_is_stable_and_request_bound():
    args = {
        "contact_id": uuid4(),
        "matter_id": uuid4(),
        "to_number": "+15551234567",
        "body": "Appointment confirmed",
        "category": "appointment",
    }
    assert _request_digest(**args) == _request_digest(**args)
    assert _request_digest(**args) != _request_digest(**{**args, "body": "Changed"})


def _active_consent(**overrides):
    values = {
        "sms_allowed": True,
        "sms_status": "active",
        "phone_verified": True,
        "mobile_e164": "+15551234567",
        "revoked_at": None,
        "sms_revoked_at": None,
        "consented_at": datetime.now(timezone.utc) - timedelta(minutes=5),
        "consent_expires_at": datetime.now(timezone.utc) + timedelta(days=30),
        "consent_source": "public_intake",
        "disclosure_version": "sms-v3",
        "allowed_categories": ["appointment", "lead_follow_up"],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_sms_consent_requires_provenance_expiry_and_explicit_category_grant():
    assert consent_authorizes_sms(
        consent=_active_consent(),
        to_number="+15551234567",
        category="appointment",
    )
    for override in (
        {"consented_at": None},
        {"consent_source": None},
        {"disclosure_version": None},
        {"revoked_at": datetime.now(timezone.utc)},
        {"sms_revoked_at": datetime.now(timezone.utc)},
        {"consent_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
        {"allowed_categories": []},
        {"allowed_categories": ["billing"]},
    ):
        assert not consent_authorizes_sms(
            consent=_active_consent(**override),
            to_number="+15551234567",
            category="appointment",
        )


def test_provider_status_replay_and_out_of_order_events_cannot_regress_truth():
    assert provider_status_transition_allowed(current="queued", incoming="sent")
    assert provider_status_transition_allowed(current="sent", incoming="delivered")
    assert provider_status_transition_allowed(current="delivered", incoming="read")
    assert not provider_status_transition_allowed(
        current="delivered", incoming="delivered"
    )
    assert not provider_status_transition_allowed(current="delivered", incoming="sent")
    assert not provider_status_transition_allowed(
        current="delivered", incoming="failed"
    )
    assert not provider_status_transition_allowed(
        current="failed", incoming="delivered"
    )
    assert not provider_status_transition_allowed(
        current="sent", incoming="provider-made-this-up"
    )


def test_active_sms_consent_contract_requires_disclosure_mobile_and_categories():
    with pytest.raises(ValueError, match="disclosure version"):
        IntakeSubmissionCreate(
            answers={},
            idempotency_key="intake-sms-consent",
            sms_consent=True,
        )
    with pytest.raises(ValueError, match="allowed categories"):
        ConsentUpdate(
            sms_allowed=True,
            phone_verified=True,
            mobile_e164="+15551234567",
            disclosure_version="sms-v3",
            allowed_categories=[],
            consent_timezone="America/Chicago",
            quiet_hours_start="21:00",
            quiet_hours_end="08:00",
        )
    consent = ConsentUpdate(
        sms_allowed=True,
        phone_verified=True,
        mobile_e164="+15551234567",
        disclosure_version="sms-v3",
        allowed_categories=[" appointment ", "appointment", "lead_follow_up"],
        consent_timezone="America/Chicago",
        quiet_hours_start="21:00",
        quiet_hours_end="07:00",
    )
    assert consent.allowed_categories == ["appointment", "lead_follow_up"]
    with pytest.raises(ValueError, match="HH:MM"):
        ConsentUpdate(
            sms_allowed=True,
            phone_verified=True,
            mobile_e164="+15551234567",
            disclosure_version="sms-v3",
            allowed_categories=["appointment"],
            consent_timezone="America/Chicago",
            quiet_hours_start="9pm",
            quiet_hours_end="07:00",
        )
    with pytest.raises(ValueError, match="must be different"):
        ConsentUpdate(
            sms_allowed=True,
            phone_verified=True,
            mobile_e164="+15551234567",
            disclosure_version="sms-v3",
            allowed_categories=["appointment"],
            consent_timezone="America/Chicago",
            quiet_hours_start="07:00",
            quiet_hours_end="07:00",
        )
