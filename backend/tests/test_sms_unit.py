from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.sms import (
    in_quiet_hours,
    normalize_e164,
    twilio_signature,
    verify_twilio_signature,
)
from app.schemas.chat_action import SmsClientAction, ResolvedSmsRecipientBinding
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
