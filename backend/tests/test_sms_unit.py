from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.sms import SmsMessage, SmsProviderConfig, SmsReviewItem
from app.services.sms import (
    consent_authorizes_sms,
    in_quiet_hours,
    normalize_e164,
    provider_status_transition_allowed,
    twilio_signature,
    verify_twilio_signature,
)
from app.services.sms import _request_digest
from app.schemas.chat_action import SmsClientAction, ResolvedSmsRecipientBinding
from app.schemas.conversion_loop import ConsentUpdate, IntakeSubmissionCreate
from app.schemas.sms import SmsProviderConfigUpdate


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


def test_sms_provider_activation_requires_explicit_compliance_evidence():
    with pytest.raises(ValueError, match="compliance evidence"):
        SmsProviderConfigUpdate(
            account_sid="AC123",
            auth_token="auth-token",
            webhook_secret="webhook-secret",
            sender_ready=True,
            compliance_snapshot={},
        )
    config = SmsProviderConfigUpdate(
        account_sid="AC123",
        auth_token="auth-token",
        webhook_secret="webhook-secret",
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


def test_sms_action_is_phone_bound_and_reviewable():
    party_id = uuid4()
    action = SmsClientAction(
        type="sms_client",
        recipient_bindings=[
            ResolvedSmsRecipientBinding(
                party_id=party_id, contact_id=uuid4(), phone="+15551234567"
            )
        ],
        body="Your appointment is confirmed.",
        matter_id=uuid4(),
        idempotency_key="chat-sms-123456",
    )
    assert action.type == "sms_client"
    with pytest.raises(ValueError):
        ResolvedSmsRecipientBinding(
            party_id=party_id, contact_id=uuid4(), phone="555-1234"
        )


def test_sms_records_have_tenant_composite_referential_guards():
    constraints = {
        constraint.name
        for table in (
            SmsProviderConfig.__table__,
            SmsMessage.__table__,
            SmsReviewItem.__table__,
        )
        for constraint in table.foreign_key_constraints
    }
    assert {
        "fk_sms_provider_configs_tenant_user",
        "fk_sms_messages_tenant_contact",
        "fk_sms_messages_tenant_matter",
        "fk_sms_messages_tenant_communication",
        "fk_sms_messages_tenant_user",
        "fk_sms_review_items_tenant_message",
        "fk_sms_review_items_tenant_user",
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
        )
    consent = ConsentUpdate(
        sms_allowed=True,
        phone_verified=True,
        mobile_e164="+15551234567",
        disclosure_version="sms-v3",
        allowed_categories=[" appointment ", "appointment", "lead_follow_up"],
        quiet_hours_start="21:00",
        quiet_hours_end="07:00",
    )
    assert consent.allowed_categories == ["appointment", "lead_follow_up"]
