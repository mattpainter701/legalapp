import base64
import hashlib
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.services import connected_mail, task_automation, work_artifact_reviews
from app.services.mail_attachment import MailAttachment, MAX_ATTACHMENT_BYTES
from app.services.email import EmailDeliveryResult, EmailService
from app.schemas.chat_action import EmailClientAction, ApprovedArtifactAttachment


def action():
    party_id, contact_id = uuid4(), uuid4()
    binding = ApprovedArtifactAttachment(
        artifact_id=uuid4(),
        revision_id=uuid4(),
        approval_id=uuid4(),
        document_id=uuid4(),
        document_sha256=hashlib.sha256(b"exact document").hexdigest(),
        filename="Reviewed.docx",
    )
    return EmailClientAction(
        type="email_client",
        to=["client@example.test"],
        subject="Reviewed document",
        body="Please review.",
        matter_id=uuid4(),
        recipient_bindings=[
            dict(
                party_id=party_id, contact_id=contact_id, address="client@example.test"
            )
        ],
        artifact_attachment=binding,
    )


def test_gmail_attachment_contains_exact_bytes_and_filename():
    attachment = MailAttachment("Reviewed.docx", b"exact document")
    raw = connected_mail._gmail_message(
        to=["client@example.test"],
        subject="Reviewed",
        html_body="<p>Hello</p>",
        text_body="Hello",
        attachment=attachment,
    )
    message = BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(raw)
    )
    parts = list(message.iter_attachments())
    assert len(parts) == 1
    assert parts[0].get_filename() == attachment.filename
    assert parts[0].get_payload(decode=True) == attachment.content
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == "Hello"


@pytest.mark.asyncio
async def test_graph_attachment_is_bounded_and_send_is_never_retried(monkeypatch):
    request = AsyncMock(return_value=httpx.Response(202))
    monkeypatch.setattr(connected_mail, "graph_request", request)
    attachment = MailAttachment("Reviewed.docx", b"exact document")
    result = await connected_mail._provider_send(
        "microsoft",
        "fake",
        to=["client@example.test"],
        subject="Reviewed",
        html_body="Hi",
        text_body="Hi",
        attachment=attachment,
    )
    assert result.result == EmailDeliveryResult.SENT
    kwargs = request.call_args.kwargs
    assert kwargs["max_retries"] == 0
    assert kwargs["json"]["saveToSentItems"] is True
    part = kwargs["json"]["message"]["attachments"][0]
    assert base64.b64decode(part["contentBytes"]) == attachment.content
    assert part["name"] == attachment.filename


@pytest.mark.asyncio
async def test_google_provider_preserves_attachment(monkeypatch):
    request = AsyncMock(return_value=httpx.Response(200, json={"id": "message-1"}))
    monkeypatch.setattr(connected_mail, "gmail_request", request)
    await connected_mail._provider_send(
        "google",
        "fake",
        to=["client@example.test"],
        subject="Reviewed",
        html_body="Hi",
        text_body="Hi",
        attachment=MailAttachment("Reviewed.docx", b"exact document"),
    )
    msg = BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(request.call_args.kwargs["json"]["raw"])
    )
    assert next(msg.iter_attachments()).get_payload(decode=True) == b"exact document"
    assert request.call_args.kwargs["max_retries"] == 0


@pytest.mark.asyncio
async def test_smtp_attachment_has_mixed_outer_and_alternative_inner(monkeypatch):
    service = EmailService()
    monkeypatch.setattr(service, "configuration_status", lambda: None)
    send = AsyncMock()
    monkeypatch.setattr("app.services.email.aiosmtplib.send", send)
    result = await service.send_email(
        ["client@example.test"],
        "Reviewed",
        "<p>Hello</p>",
        "Hello",
        attachment=MailAttachment("Reviewed.docx", b"exact document"),
    )
    assert result == EmailDeliveryResult.SENT
    message = BytesParser(policy=policy.default).parsebytes(
        send.call_args.args[0].as_bytes()
    )
    assert message.get_content_type() == "multipart/mixed"
    assert message.get_payload(0).get_content_type() == "multipart/alternative"
    assert (
        next(message.iter_attachments()).get_payload(decode=True) == b"exact document"
    )


@pytest.mark.parametrize(
    "filename,content",
    [
        ("../file.docx", b"a"),
        ("bad\nname", b"a"),
        ("good.docx", b""),
        ("good.docx", b"x" * (MAX_ATTACHMENT_BYTES + 1)),
    ],
    ids=["path", "header", "empty", "oversize"],
)
def test_attachment_rejects_unsafe_names_and_unbounded_bytes(filename, content):
    with pytest.raises(ValueError):
        MailAttachment(filename, content)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [True, False, "unavailable"])
async def test_worker_never_sends_substituted_attachment(monkeypatch, changed):
    value = action()
    task = NS(id=uuid4(), tenant_id=uuid4(), matter_id=value.matter_id)
    monkeypatch.setattr(
        task_automation, "_recipient_bindings_are_current", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        task_automation, "_action_sources_are_current", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        work_artifact_reviews,
        "resolve_approved_attachment",
        AsyncMock(return_value=(value.artifact_attachment, NS())),
    )
    read = AsyncMock(
        return_value=b"changed" if changed else b"exact document",
        side_effect=OSError("unavailable") if changed == "unavailable" else None,
    )
    monkeypatch.setattr(
        "app.services.cloud_artifact_materialization.cloud_artifact_materializer.read_current_cloud_bytes",
        read,
    )
    session = AsyncMock()
    monkeypatch.setattr(task_automation, "async_session_maker", lambda: session)
    monkeypatch.setattr(task_automation, "set_tenant_context", AsyncMock())
    send = AsyncMock(
        return_value=NS(
            result=EmailDeliveryResult.SENT,
            detail="Sent",
            provider="google",
            provider_message_id="1",
            delivery_certainty="confirmed_sent",
        )
    )
    monkeypatch.setattr(task_automation, "send_client_email", send)
    result = await task_automation._run_email_client(
        AsyncMock(), task, value.model_dump(mode="json"), uuid4()
    )
    if changed:
        assert result.delivery_certainty == "not_attempted"
        send.assert_not_awaited()
    else:
        assert result.succeeded
        assert send.call_args.kwargs["attachment"].content == b"exact document"


@pytest.mark.asyncio
async def test_receipt_uses_original_snapshot_even_after_task_changes():
    value = action()
    run = NS(
        id=uuid4(),
        tenant_id=uuid4(),
        task_id=uuid4(),
        triggered_by_user_id=uuid4(),
        action_snapshot=value.model_dump(mode="json"),
        provider_message_id=None,
        action_sha256="a" * 64,
        delivery_certainty="outcome_unknown",
    )
    db = AsyncMock()
    db.scalar.return_value = None
    db.add = lambda row: None
    receipt = await work_artifact_reviews.append_delivery_receipt(
        db, run, status="outcome_unknown"
    )
    assert receipt.approval_id == value.artifact_attachment.approval_id
    assert receipt.recipient_bindings == run.action_snapshot["recipient_bindings"]
    assert receipt.status == "outcome_unknown"
    db.scalar.return_value = receipt
    assert (
        await work_artifact_reviews.append_delivery_receipt(
            db, run, status="outcome_unknown"
        )
        is receipt
    )
    run.action_snapshot = {}
    assert (
        await work_artifact_reviews.append_delivery_receipt(db, run, status="sent")
        is None
    )
