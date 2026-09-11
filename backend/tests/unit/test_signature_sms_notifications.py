"""Texting a signer about a document waiting for signature.

Signature notifications were email-only, even for a client who had asked to be
texted. The fix is not to reuse the consent intake already records: a client
who agreed to onboarding texts has not agreed to be texted for the life of the
matter. So these go out under `case_updates`, and an intake-only consent
declines here rather than being stretched to fit.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.services.esign import notifications
from app.services.sms_categories import (
    SMS_CATEGORY_CASE_UPDATES,
    SMS_CATEGORY_INTAKE,
)


def signer(**overrides):
    values = dict(
        id=uuid.uuid4(),
        contact_id=uuid.uuid4(),
        audit=None,
        status="pending",
        order_index=0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def signature_request(**overrides):
    values = dict(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        matter_id=uuid.uuid4(),
        created_by_user_id=uuid.uuid4(),
        source_document_filename="Fee agreement.pdf",
        signers=[],
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def sms(monkeypatch):
    """Stand in for the SMS service, recording what it was asked to send."""
    calls = []

    consents = {"allowed": [SMS_CATEGORY_INTAKE, SMS_CATEGORY_CASE_UPDATES]}

    class SmsError(Exception):
        def __init__(self, code=None):
            super().__init__(code or "failed")
            self.code = code

    async def load_sms_consents(_db, _tenant_id, _contact_id):
        if consents["allowed"] is None:
            return []
        return [SimpleNamespace(allowed_categories=consents["allowed"])]

    async def send_sms(_db, **kwargs):
        calls.append(kwargs)
        if consents.get("raises"):
            raise consents["raises"]
        return SimpleNamespace(delivery_certainty="delivered")

    module = SimpleNamespace(
        SmsError=SmsError, load_sms_consents=load_sms_consents, send_sms=send_sms
    )
    monkeypatch.setitem(__import__("sys").modules, "app.services.sms", module)
    return SimpleNamespace(calls=calls, consents=consents, SmsError=SmsError)


@pytest.mark.asyncio
async def test_a_case_update_consent_gets_the_text(sms):
    who = signer()
    request = signature_request()

    result = await notifications.notify_signer_sms(None, who, request)

    assert result is not None
    assert len(sms.calls) == 1
    call = sms.calls[0]
    assert call["category"] == SMS_CATEGORY_CASE_UPDATES
    assert call["contact_id"] == who.contact_id
    assert "Fee agreement.pdf" in call["body"]
    assert call["idempotency_key"] == f"signature:{request.id}:{who.id}:invitation"
    assert who.audit["invitation_sms_status"] == "delivered"
    assert who.audit["invitation_sms_attempted_at"]


@pytest.mark.asyncio
async def test_an_intake_only_consent_is_never_stretched_to_fit(sms):
    """The whole point: onboarding permission is not case-long permission."""
    sms.consents["allowed"] = [SMS_CATEGORY_INTAKE]

    result = await notifications.notify_signer_sms(None, signer(), signature_request())

    assert result is None and sms.calls == []


@pytest.mark.asyncio
async def test_no_consent_at_all_sends_nothing(sms):
    sms.consents["allowed"] = None

    result = await notifications.notify_signer_sms(None, signer(), signature_request())

    assert result is None and sms.calls == []


@pytest.mark.asyncio
async def test_a_signer_with_no_contact_is_skipped(sms):
    result = await notifications.notify_signer_sms(
        None, signer(contact_id=None), signature_request()
    )

    assert result is None and sms.calls == []


@pytest.mark.asyncio
async def test_a_reminder_reads_as_a_reminder(sms):
    who = signer()

    await notifications.notify_signer_sms(
        None, who, signature_request(), kind="reminder"
    )

    assert sms.calls[0]["body"].startswith("Reminder:")
    assert who.audit["reminder_sms_status"] == "delivered"


@pytest.mark.asyncio
async def test_an_unnamed_document_still_reads_sensibly(sms):
    await notifications.notify_signer_sms(
        None, signer(), signature_request(source_document_filename=None)
    )

    assert "a document" in sms.calls[0]["body"]


@pytest.mark.asyncio
async def test_a_send_failure_is_recorded_on_the_signer_not_raised(sms):
    sms.consents["raises"] = sms.SmsError("undeliverable")
    who = signer()

    result = await notifications.notify_signer_sms(None, who, signature_request())

    assert result is None
    assert who.audit["invitation_sms_status"] == "undeliverable"
    assert who.audit["invitation_sms_attempted_at"]


@pytest.mark.asyncio
async def test_a_failure_with_no_code_still_records_something(sms):
    sms.consents["raises"] = sms.SmsError(None)
    who = signer()

    await notifications.notify_signer_sms(None, who, signature_request())

    assert who.audit["invitation_sms_status"] == "failed"


@pytest.mark.asyncio
async def test_an_existing_audit_is_added_to_rather_than_replaced(sms):
    who = signer(audit={"viewed_at": "2026-09-01T00:00:00Z"})

    await notifications.notify_signer_sms(None, who, signature_request())

    assert who.audit["viewed_at"] == "2026-09-01T00:00:00Z"
    assert who.audit["invitation_sms_status"] == "delivered"


@pytest.mark.asyncio
async def test_every_signer_whose_turn_it_is_gets_one_text(sms, monkeypatch):
    first, second = signer(), signer()
    monkeypatch.setattr(
        notifications, "next_pending_signers", lambda _request: [first, second]
    )

    sent = await notifications.notify_actionable_signers_sms(
        None, signature_request()
    )

    assert len(sent) == 2 and len(sms.calls) == 2


@pytest.mark.asyncio
async def test_one_signers_failure_never_stops_the_others(sms, monkeypatch):
    """A text is an extra channel on top of an email that already went."""
    broken, fine = signer(), signer()

    async def explode_for_broken(db, who, request, kind="invitation"):
        if who is broken:
            raise RuntimeError("the provider exploded")
        return SimpleNamespace(delivery_certainty="delivered")

    monkeypatch.setattr(
        notifications, "next_pending_signers", lambda _request: [broken, fine]
    )
    monkeypatch.setattr(notifications, "notify_signer_sms", explode_for_broken)

    sent = await notifications.notify_actionable_signers_sms(
        None, signature_request()
    )

    assert len(sent) == 1


@pytest.mark.asyncio
async def test_a_signer_who_declines_contributes_nothing_to_the_result(
    sms, monkeypatch
):
    sms.consents["allowed"] = [SMS_CATEGORY_INTAKE]
    monkeypatch.setattr(
        notifications, "next_pending_signers", lambda _request: [signer()]
    )

    assert await notifications.notify_actionable_signers_sms(
        None, signature_request()
    ) == []
