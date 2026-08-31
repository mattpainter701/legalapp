from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.conversion_loop import LeadChannelConsent, SmsConsentEvent
from app.models.sms import (
    SmsMessage,
    SmsNumberSuppression,
    SmsNumberSuppressionEvent,
    SmsProviderConfig,
    SmsReviewItem,
)
from app.models.task import TaskAutomationRun
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
    } <= constraints


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
    assert provider_status_transition_allowed(current="delivered", incoming="delivered")
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
