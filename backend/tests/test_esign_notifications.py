from datetime import datetime, timedelta, timezone

import pytest

from app.models.signature import SignatureRequest, SignatureSigner
from app.services.email import EmailDeliveryResult
from app.services.esign import notifications


def _request(*, ordered=True):
    request = SignatureRequest(
        status="sent",
        provider="internal",
        enforce_signing_order=ordered,
        source_document_filename="Engagement Letter.pdf",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        reminders={"days_before_expiration": [7, 1]},
    )
    request.signers = [
        SignatureSigner(
            name="First Client",
            email="first@example.com",
            sign_order=0,
            status="pending",
        ),
        SignatureSigner(
            name="Second Client",
            email="second@example.com",
            sign_order=1,
            status="pending",
        ),
    ]
    return request


@pytest.mark.asyncio
async def test_invitation_notifies_only_actionable_signer_and_records_delivery(
    monkeypatch,
):
    delivered = []

    async def send_email(to, subject, html_body, text_body):
        delivered.append((to, subject, html_body, text_body))
        return EmailDeliveryResult.SENT

    monkeypatch.setattr(notifications.email_service, "send_email", send_email)
    request = _request()

    results = await notifications.notify_actionable_signers(request)

    assert results == [EmailDeliveryResult.SENT]
    assert delivered[0][0] == ["first@example.com"]
    assert "Engagement Letter.pdf" in delivered[0][1]
    assert request.signers[0].audit["invitation_delivery_status"] == "sent"
    assert request.signers[0].audit["invitation_sent_at"]
    assert request.signers[1].audit is None


def test_mark_signer_viewed_preserves_first_view_timestamp():
    signer = SignatureSigner(name="Client", email="client@example.com", audit={})
    notifications.mark_signer_viewed(signer)
    first = signer.audit["viewed_at"]
    notifications.mark_signer_viewed(signer)
    assert signer.audit["viewed_at"] == first


@pytest.mark.asyncio
async def test_delivery_failure_is_visible_in_audit(monkeypatch):
    async def send_email(*args, **kwargs):
        return EmailDeliveryResult.UNCONFIGURED

    monkeypatch.setattr(notifications.email_service, "send_email", send_email)
    request = _request()
    await notifications.notify_actionable_signers(request)
    assert request.signers[0].audit["invitation_delivery_status"] == "unconfigured"
    assert "invitation_sent_at" not in request.signers[0].audit


class _Scalars:
    def __init__(self, rows):
        self.rows = rows

    def unique(self):
        return self.rows


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return _Scalars(self.rows)


class _Db:
    def __init__(self, rows):
        self.rows = rows
        self.commits = 0

    async def execute(self, statement):
        return _Result(self.rows)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_due_reminders_send_once_and_expire_overdue_requests(monkeypatch):
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    due = _request()
    due.expires_at = now + timedelta(days=7)
    expired = _request()
    expired.expires_at = now - timedelta(minutes=1)
    delivered = []

    async def send_email(*args, **kwargs):
        delivered.append(args)
        return EmailDeliveryResult.SENT

    monkeypatch.setattr(notifications.email_service, "send_email", send_email)
    db = _Db([due, expired])

    assert await notifications.process_due_reminders(db, now=now) == 1
    assert due.signers[0].audit["reminder_7_days_sent_at"]
    assert expired.status == "expired"
    assert db.commits == 1

    assert await notifications.process_due_reminders(db, now=now) == 0
    assert len(delivered) == 1


@pytest.mark.asyncio
async def test_reminder_skips_unconfigured_day(monkeypatch):
    request = _request()
    request.expires_at = datetime(2026, 9, 5, tzinfo=timezone.utc)

    async def unexpected(*args, **kwargs):
        pytest.fail("email should not be sent")

    monkeypatch.setattr(notifications.email_service, "send_email", unexpected)
    assert (
        await notifications.process_due_reminders(
            _Db([request]), now=datetime(2026, 8, 27, tzinfo=timezone.utc)
        )
        == 0
    )
