"""Connected Microsoft/Google delivery for attorney-approved correspondence."""

import base64
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser

import httpx
import pytest

from app.models.tenant_credential import TenantCredential
from app.models.user_oauth_token import UserOAuthToken
from app.services import connected_mail
from app.services.email import EmailDeliveryResult
from app.services.provider_http import ProviderError
from app.services.token_vault import encrypt_token


class _SMTPRecorder:
    def __init__(self, result=EmailDeliveryResult.SENT):
        self.result = result
        self.calls = []

    async def send_email(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def _fresh_token(*, tenant, user, provider: str, scopes: str) -> UserOAuthToken:
    return UserOAuthToken(
        tenant_id=tenant.id,
        user_id=user.id,
        provider=provider,
        encrypted_access_token=encrypt_token(f"{provider}-access-token"),
        encrypted_refresh_token=encrypt_token(f"{provider}-refresh-token"),
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=scopes,
    )


def _send_args(db_session, tenant, user, smtp):
    return {
        "db": db_session,
        "tenant_id": tenant.id,
        "actor_user_id": user.id,
        "to": ["client@example.test"],
        "subject": "Approved draft",
        "html_body": "<p>Please review the attached request.</p>",
        "text_body": "Please review the attached request.",
        "smtp_service": smtp,
    }


def test_oauth_connect_flows_request_least_privilege_send_scopes():
    from app.routers.integrations import (
        GOOGLE_ADMIN_SCOPES,
        GOOGLE_USER_SCOPES,
        MICROSOFT_ADMIN_SCOPES,
        MICROSOFT_USER_SCOPES,
    )

    assert "Mail.Send" in MICROSOFT_USER_SCOPES.split()
    assert "Mail.Send" in MICROSOFT_ADMIN_SCOPES.split()
    assert "https://www.googleapis.com/auth/gmail.send" in GOOGLE_USER_SCOPES.split()
    assert "https://www.googleapis.com/auth/gmail.send" in GOOGLE_ADMIN_SCOPES.split()


@pytest.mark.asyncio
async def test_approver_microsoft_mailbox_is_preferred_and_saved_to_sent_items(
    db_session, test_tenant, test_user, monkeypatch
):
    test_user.oauth_provider = "microsoft"
    db_session.add(
        _fresh_token(
            tenant=test_tenant,
            user=test_user,
            provider="microsoft",
            scopes="offline_access Mail.Read Mail.Send",
        )
    )
    await db_session.commit()
    request = {}

    async def graph_request(method, path, *, token, **kwargs):
        request.update(method=method, path=path, token=token, **kwargs)
        return httpx.Response(202)

    monkeypatch.setattr(connected_mail, "graph_request", graph_request)
    smtp = _SMTPRecorder()

    delivery = await connected_mail.send_client_email(
        **_send_args(db_session, test_tenant, test_user, smtp)
    )

    assert delivery.result == EmailDeliveryResult.SENT
    assert delivery.provider == "microsoft"
    assert request["path"] == "/me/sendMail"
    assert request["max_retries"] == 0
    assert request["json"]["saveToSentItems"] is True
    assert (
        request["json"]["message"]["toRecipients"][0]["emailAddress"]["address"]
        == "client@example.test"
    )
    assert smtp.calls == []


@pytest.mark.asyncio
async def test_approver_google_mailbox_sends_multipart_message(
    db_session, test_tenant, test_user, monkeypatch
):
    test_user.oauth_provider = "google"
    db_session.add(
        _fresh_token(
            tenant=test_tenant,
            user=test_user,
            provider="google",
            scopes=(
                "https://www.googleapis.com/auth/gmail.readonly "
                "https://www.googleapis.com/auth/gmail.send"
            ),
        )
    )
    await db_session.commit()
    request = {}

    async def gmail_request(method, path, *, token, **kwargs):
        request.update(method=method, path=path, token=token, **kwargs)
        return httpx.Response(200, json={"id": "gmail-message-1"})

    monkeypatch.setattr(connected_mail, "gmail_request", gmail_request)
    smtp = _SMTPRecorder()

    delivery = await connected_mail.send_client_email(
        **_send_args(db_session, test_tenant, test_user, smtp)
    )

    raw = base64.urlsafe_b64decode(request["json"]["raw"])
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert delivery.result == EmailDeliveryResult.SENT
    assert delivery.provider == "google"
    assert delivery.provider_message_id == "gmail-message-1"
    assert request["path"] == "/users/me/messages/send"
    assert request["max_retries"] == 0
    assert message["To"] == "client@example.test"
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == (
        "Please review the attached request."
    )
    assert smtp.calls == []


@pytest.mark.asyncio
async def test_existing_read_only_grant_requires_reconnect_instead_of_smtp(
    db_session, test_tenant, test_user
):
    db_session.add(
        _fresh_token(
            tenant=test_tenant,
            user=test_user,
            provider="microsoft",
            scopes="offline_access Mail.Read",
        )
    )
    await db_session.commit()
    smtp = _SMTPRecorder()

    delivery = await connected_mail.send_client_email(
        **_send_args(db_session, test_tenant, test_user, smtp)
    )

    assert delivery.result == EmailDeliveryResult.REAUTHORIZATION_REQUIRED
    assert "Reconnect Microsoft 365" in delivery.detail
    assert "send email" in delivery.detail
    assert smtp.calls == []


@pytest.mark.asyncio
async def test_ambiguous_cloud_failure_never_falls_through_to_smtp(
    db_session, test_tenant, test_user, monkeypatch
):
    test_user.oauth_provider = "microsoft"
    db_session.add(
        _fresh_token(
            tenant=test_tenant,
            user=test_user,
            provider="microsoft",
            scopes="offline_access Mail.Send",
        )
    )
    await db_session.commit()

    async def graph_request(*args, **kwargs):
        raise ProviderError("connection ended without a response")

    monkeypatch.setattr(connected_mail, "graph_request", graph_request)
    smtp = _SMTPRecorder()

    delivery = await connected_mail.send_client_email(
        **_send_args(db_session, test_tenant, test_user, smtp)
    )

    assert delivery.result == EmailDeliveryResult.FAILED
    assert delivery.provider == "microsoft"
    assert delivery.delivery_certainty == "outcome_unknown"
    assert "did not confirm delivery" in delivery.detail
    assert smtp.calls == []


@pytest.mark.asyncio
async def test_provider_5xx_remains_outcome_unknown_and_never_auto_falls_back(
    db_session, test_tenant, test_user, monkeypatch
):
    test_user.oauth_provider = "microsoft"
    db_session.add(
        _fresh_token(
            tenant=test_tenant,
            user=test_user,
            provider="microsoft",
            scopes="offline_access Mail.Send",
        )
    )
    await db_session.commit()

    async def graph_request(*args, **kwargs):
        raise ProviderError("provider returned a terminal 503", status_code=503)

    monkeypatch.setattr(connected_mail, "graph_request", graph_request)
    smtp = _SMTPRecorder()

    delivery = await connected_mail.send_client_email(
        **_send_args(db_session, test_tenant, test_user, smtp)
    )

    assert delivery.result == EmailDeliveryResult.FAILED
    assert delivery.delivery_certainty == "outcome_unknown"
    assert smtp.calls == []


@pytest.mark.asyncio
async def test_tenant_firm_mailbox_is_used_when_approver_has_no_personal_grant(
    db_session, test_tenant, test_user, monkeypatch
):
    db_session.add(
        TenantCredential(
            tenant_id=test_tenant.id,
            provider="google",
            encrypted_access_token=encrypt_token("firm-google-access-token"),
            encrypted_refresh_token=encrypt_token("firm-google-refresh-token"),
            token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scopes="https://www.googleapis.com/auth/gmail.send",
            service_account_email="firm@example.test",
            granted_by_user_id=test_user.id,
            is_active=True,
        )
    )
    await db_session.commit()
    calls = []

    async def gmail_request(method, path, *, token, **kwargs):
        calls.append((method, path, token, kwargs))
        return httpx.Response(200, json={"id": "firm-message-1"})

    monkeypatch.setattr(connected_mail, "gmail_request", gmail_request)
    smtp = _SMTPRecorder()

    delivery = await connected_mail.send_client_email(
        **_send_args(db_session, test_tenant, test_user, smtp)
    )

    assert delivery.provider == "google"
    assert calls[0][2] == "firm-google-access-token"
    assert smtp.calls == []


@pytest.mark.asyncio
async def test_smtp_remains_available_when_tenant_has_no_cloud_mail_grant(
    db_session, test_tenant, test_user
):
    smtp = _SMTPRecorder()

    delivery = await connected_mail.send_client_email(
        **_send_args(db_session, test_tenant, test_user, smtp)
    )

    assert delivery.result == EmailDeliveryResult.SENT
    assert delivery.provider == "smtp"
    assert len(smtp.calls) == 1


@pytest.mark.asyncio
async def test_smtp_rejects_a_contact_value_containing_multiple_mailboxes(
    db_session, test_tenant, test_user
):
    smtp = _SMTPRecorder()
    args = _send_args(db_session, test_tenant, test_user, smtp)
    args["to"] = ["client@example.com, hidden@example.com"]

    delivery = await connected_mail.send_client_email(**args)

    assert delivery.result == EmailDeliveryResult.INVALID_RECIPIENT
    assert smtp.calls == []


@pytest.mark.asyncio
async def test_google_rejects_a_multi_mailbox_value_before_api_submission(
    db_session, test_tenant, test_user, monkeypatch
):
    test_user.oauth_provider = "google"
    db_session.add(
        _fresh_token(
            tenant=test_tenant,
            user=test_user,
            provider="google",
            scopes="https://www.googleapis.com/auth/gmail.send",
        )
    )
    await db_session.commit()
    calls = []

    async def gmail_request(*args, **kwargs):
        calls.append((args, kwargs))
        return httpx.Response(200, json={"id": "must-not-send"})

    monkeypatch.setattr(connected_mail, "gmail_request", gmail_request)
    smtp = _SMTPRecorder()
    args = _send_args(db_session, test_tenant, test_user, smtp)
    args["to"] = ["client@example.com, hidden@example.com"]

    delivery = await connected_mail.send_client_email(**args)

    assert delivery.result == EmailDeliveryResult.INVALID_RECIPIENT
    assert calls == []
    assert smtp.calls == []
