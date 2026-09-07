"""Matter email dispatch/audit contract; provider transports have separate tests."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.routers import matters
from app.services.connected_mail import ConnectedMailDelivery
from app.services.email import EmailDeliveryResult


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result,provider,certainty,http_status,log_status",
    [
        (EmailDeliveryResult.SENT, "microsoft", "confirmed_sent", None, "sent"),
        (EmailDeliveryResult.SENT, "google", "confirmed_sent", None, "sent"),
        (
            EmailDeliveryResult.REAUTHORIZATION_REQUIRED,
            None,
            "not_attempted",
            503,
            "failed",
        ),
        (EmailDeliveryResult.DISABLED, None, "not_attempted", 503, "failed"),
        (EmailDeliveryResult.FAILED, "microsoft", "not_attempted", 502, "failed"),
        (
            EmailDeliveryResult.FAILED,
            "microsoft",
            "outcome_unknown",
            409,
            "delivery_unknown",
        ),
    ],
)
async def test_connected_delivery_and_audit(
    monkeypatch, result, provider, certainty, http_status, log_status
):
    tenant_id, actor_id, matter_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    user = SimpleNamespace(tenant_id=tenant_id, id=actor_id)
    matter = SimpleNamespace(
        id=matter_id,
        client=None,
        client_contact_id=None,
        matter_name="A & B",
        case_number="<case>",
    )
    events = []

    class DB:
        def add(self, row):
            self.row = row

        async def commit(self):
            events.append("commit")

        async def refresh(self, row):
            assert events[-1] == "scope"
            row.id = uuid.uuid4()
            row.occurred_at = datetime.now(timezone.utc)

    db = DB()

    async def scope(*_):
        events.append("scope")

    async def dispatch(*args, **kwargs):
        # Model a provider token refresh that invalidates ORM attributes.
        user.__dict__.clear()
        matter.__dict__.clear()
        return ConnectedMailDelivery(
            result,
            "Reconnect Microsoft to send email",
            provider,
            delivery_certainty=certainty,
        )

    sender = AsyncMock(side_effect=dispatch)
    monkeypatch.setattr(matters, "get_current_user", AsyncMock(return_value=user))
    monkeypatch.setattr(matters, "_get_matter_or_404", AsyncMock(return_value=matter))
    monkeypatch.setattr(matters, "send_client_email", sender)
    monkeypatch.setattr(matters, "set_tenant_context", scope)
    monkeypatch.setattr(matters, "_invalidate_matter_context_cache", AsyncMock())
    call = matters.email_matter_client(
        str(matter_id),
        {
            "to_email": "self@example.com",
            "subject": "Test",
            "body": "<script>alert(1)</script>\nSynthetic",
        },
        None,
        db,
    )
    if http_status:
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == http_status
        if certainty == "outcome_unknown":
            assert "Sent Items" in exc.value.detail
            assert "may have accepted" in exc.value.detail
    else:
        response = await call
        assert response["sent"] is True and response["provider"] == provider
        assert response["matter_id"] == str(matter_id)

    assert sender.await_count == 1
    payload = sender.call_args.kwargs
    assert payload["actor_user_id"] == actor_id
    assert payload["tenant_id"] == tenant_id
    assert payload["to"] == ["self@example.com"]
    assert "<script>" not in payload["html_body"]
    assert "&lt;script&gt;" in payload["html_body"]
    assert "A &amp; B" in payload["html_body"]
    assert db.row.status == log_status
    assert db.row.tenant_id == tenant_id and db.row.matter_id == matter_id
    assert db.row.participants["to"] == ["self@example.com"]
    assert events == ["scope", "commit", "scope"]
