from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.signature import SignatureRequest, SignatureSigner
from app.services.connected_mail import ConnectedMailDelivery
from app.services.email import EmailDeliveryResult
from app.services.esign import notifications


def _connected_mail(result, provider="google", detail=""):
    """Stand in for send_client_email: records each call, answers ``result``."""
    calls = []

    async def send(
        db, *, tenant_id, actor_user_id, to, subject, html_body, text_body, smtp_service
    ):
        calls.append((to, subject, html_body, text_body, actor_user_id))
        return ConnectedMailDelivery(result, detail, provider=provider)

    return send, calls


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
    send, delivered = _connected_mail(EmailDeliveryResult.SENT)
    monkeypatch.setattr(notifications, "send_client_email", send)
    request = _request()
    request.created_by_user_id = "user-1"

    results = await notifications.notify_actionable_signers(None, request)

    assert results == [EmailDeliveryResult.SENT]
    assert delivered[0][0] == ["first@example.com"]
    assert "Engagement Letter.pdf" in delivered[0][1]
    # Sent as the requesting user, so it leaves their own mailbox.
    assert delivered[0][4] == "user-1"
    assert request.signers[0].audit["invitation_delivery_status"] == "sent"
    assert request.signers[0].audit["invitation_provider"] == "google"
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
    send, _calls = _connected_mail(
        EmailDeliveryResult.REAUTHORIZATION_REQUIRED,
        provider=None,
        detail="Reconnect Google Workspace mail",
    )
    monkeypatch.setattr(notifications, "send_client_email", send)
    request = _request()
    await notifications.notify_actionable_signers(None, request)
    audit = request.signers[0].audit
    assert audit["invitation_delivery_status"] == "reauthorization_required"
    assert audit["invitation_delivery_detail"] == "Reconnect Google Workspace mail"
    assert "invitation_sent_at" not in audit
    assert "invitation_provider" not in audit


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
    send, delivered = _connected_mail(EmailDeliveryResult.SENT)
    monkeypatch.setattr(notifications, "send_client_email", send)
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

    monkeypatch.setattr(notifications, "send_client_email", unexpected)
    assert (
        await notifications.process_due_reminders(
            _Db([request]), now=datetime(2026, 8, 27, tzinfo=timezone.utc)
        )
        == 0
    )


class _LookupDb:
    """Answers scalar() with the rows in order: the user, then the matter."""

    def __init__(self, *rows):
        self.rows = list(rows)

    async def scalar(self, statement):
        return self.rows.pop(0)


@pytest.mark.asyncio
async def test_requester_is_told_when_every_signer_has_signed(monkeypatch):
    send, delivered = _connected_mail(EmailDeliveryResult.SENT)
    monkeypatch.setattr(notifications, "send_client_email", send)
    request = _request()
    request.created_by_user_id = "user-1"
    request.status = "partially_signed"
    for signer in request.signers:
        signer.status = "signed"
    user = SimpleNamespace(id="user-1", email="attorney@firm.example")
    matter = SimpleNamespace(matter_name="Smith v. Jones")

    result = await notifications.notify_requester_signed(
        _LookupDb(user, matter), request
    )

    assert result is EmailDeliveryResult.SENT
    to, subject, html, text, actor = delivered[0]
    assert to == ["attorney@firm.example"]
    assert actor == "user-1"
    assert subject == "Signed: Engagement Letter.pdf — Smith v. Jones"
    assert "First Client, Second Client signed" in text
    # Not filed yet: the note says so instead of promising a filed copy.
    assert "being filed" in text
    assert "/matters/" in html


@pytest.mark.asyncio
async def test_requester_notice_is_skipped_without_a_sender(monkeypatch):
    async def unexpected(*args, **kwargs):
        pytest.fail("nobody to notify")

    monkeypatch.setattr(notifications, "send_client_email", unexpected)
    request = _request()
    request.created_by_user_id = None
    assert await notifications.notify_requester_signed(None, request) is None
