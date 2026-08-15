"""Deliver client correspondence through a tenant's connected mailbox.

Attorney-approved email should normally come from the attorney who approved it,
not from a platform-wide SMTP identity. This module prefers that user's
delegated Microsoft or Google grant, then a tenant-wide firm mailbox grant. It
falls back to SMTP only when the tenant has no cloud-mail grant at all.

Once a cloud provider is selected, provider failure is terminal for that
attempt. Falling through to a second transport after an ambiguous response can
send a duplicate message from a different identity.
"""

from __future__ import annotations

import base64
import logging
import uuid
from dataclasses import dataclass
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import set_tenant_context
from app.models.tenant_credential import TenantCredential
from app.models.user import User
from app.models.user_oauth_token import UserOAuthToken
from app.schemas.chat_action import normalize_recipient_mailboxes
from app.services.email import EmailDeliveryResult, EmailService
from app.services.google_client import gmail_request
from app.services.graph_client import graph_request
from app.services.provider_http import ProviderAuthError, ProviderError
from app.services.token_vault import get_fresh_token, get_fresh_user_token

logger = logging.getLogger(__name__)

MICROSOFT_MAIL_SEND_SCOPE = "Mail.Send"
GOOGLE_MAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"

DELIVERY_CONFIRMED_SENT = "confirmed_sent"
DELIVERY_NOT_ATTEMPTED = "not_attempted"
DELIVERY_OUTCOME_UNKNOWN = "outcome_unknown"

_PROVIDERS = ("microsoft", "google")
_SEND_SCOPE = {
    "microsoft": MICROSOFT_MAIL_SEND_SCOPE,
    "google": GOOGLE_MAIL_SEND_SCOPE,
}


@dataclass(frozen=True, slots=True)
class ConnectedMailDelivery:
    result: EmailDeliveryResult
    detail: str
    provider: str | None = None
    provider_message_id: str | None = None
    delivery_certainty: str | None = None


def _has_send_scope(provider: str, scopes: str | None) -> bool:
    granted = {value.strip() for value in (scopes or "").split() if value.strip()}
    required = _SEND_SCOPE[provider]
    if provider == "microsoft":
        return required.casefold() in {value.casefold() for value in granted}
    return required in granted


def _provider_order(login_provider: str | None) -> tuple[str, ...]:
    if login_provider in _PROVIDERS:
        return (login_provider, *[p for p in _PROVIDERS if p != login_provider])
    return _PROVIDERS


async def _send_microsoft(
    token: str,
    *,
    to: list[str],
    subject: str,
    html_body: str,
) -> ConnectedMailDelivery:
    response = await graph_request(
        "POST",
        "/me/sendMail",
        token=token,
        # Email APIs do not expose an idempotency key. Retrying a POST after an
        # ambiguous transport failure can deliver a duplicate, so the durable
        # task stays failed for human review instead of replaying automatically.
        max_retries=0,
        json={
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": html_body},
                "toRecipients": [
                    {"emailAddress": {"address": address}} for address in to
                ],
            },
            # The approved message must appear in the mailbox that sent it.
            "saveToSentItems": True,
        },
    )
    if response.status_code != 202:
        raise ProviderError(
            f"Microsoft Graph send returned HTTP {response.status_code}",
            status_code=response.status_code,
        )
    return ConnectedMailDelivery(
        EmailDeliveryResult.SENT,
        "Email sent through the connected Microsoft 365 mailbox",
        provider="microsoft",
        delivery_certainty=DELIVERY_CONFIRMED_SENT,
    )


def _gmail_message(
    *, to: list[str], subject: str, html_body: str, text_body: str
) -> str:
    message = EmailMessage()
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    message.set_content(text_body or "This message contains an HTML version.")
    message.add_alternative(html_body, subtype="html")
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


async def _send_google(
    token: str,
    *,
    to: list[str],
    subject: str,
    html_body: str,
    text_body: str,
) -> ConnectedMailDelivery:
    response = await gmail_request(
        "POST",
        "/users/me/messages/send",
        token=token,
        max_retries=0,
        json={
            "raw": _gmail_message(
                to=to,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
            )
        },
    )
    payload = response.json()
    return ConnectedMailDelivery(
        EmailDeliveryResult.SENT,
        "Email sent through the connected Google mailbox",
        provider="google",
        provider_message_id=str(payload.get("id") or "") or None,
        delivery_certainty=DELIVERY_CONFIRMED_SENT,
    )


async def _provider_send(
    provider: str,
    token: str,
    *,
    to: list[str],
    subject: str,
    html_body: str,
    text_body: str,
) -> ConnectedMailDelivery:
    if provider == "microsoft":
        return await _send_microsoft(token, to=to, subject=subject, html_body=html_body)
    return await _send_google(
        token,
        to=to,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )


def _reconnect_detail(providers: list[str]) -> str:
    labels = ["Microsoft 365" if p == "microsoft" else "Google" for p in providers]
    joined = " or ".join(dict.fromkeys(labels))
    return (
        f"Reconnect {joined} in Integrations to grant permission to send email "
        "from the connected mailbox"
    )


async def _attempt_provider(
    provider: str,
    token: str,
    *,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    firm_mailbox: bool,
    to: list[str],
    subject: str,
    html_body: str,
    text_body: str,
) -> ConnectedMailDelivery:
    try:
        return await _provider_send(
            provider,
            token,
            to=to,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )
    except ProviderAuthError:
        logger.warning(
            "Connected mailbox authorization failed tenant_id=%s user_id=%s provider=%s firm_mailbox=%s",
            tenant_id,
            actor_user_id,
            provider,
            firm_mailbox,
        )
        return ConnectedMailDelivery(
            EmailDeliveryResult.REAUTHORIZATION_REQUIRED,
            _reconnect_detail([provider]),
            provider=provider,
            delivery_certainty=DELIVERY_NOT_ATTEMPTED,
        )
    except ProviderError as exc:
        logger.warning(
            "Connected mailbox send failed tenant_id=%s user_id=%s provider=%s firm_mailbox=%s status=%s",
            tenant_id,
            actor_user_id,
            provider,
            firm_mailbox,
            exc.status_code,
        )
        mailbox = "firm mailbox" if firm_mailbox else "mailbox"
        return ConnectedMailDelivery(
            EmailDeliveryResult.FAILED,
            f"The connected {provider.title()} {mailbox} did not confirm delivery",
            provider=provider,
            delivery_certainty=(
                DELIVERY_NOT_ATTEMPTED
                if exc.status_code is not None and 400 <= exc.status_code < 500
                else DELIVERY_OUTCOME_UNKNOWN
            ),
        )


async def send_client_email(
    db: AsyncSession,
    *,
    tenant_id: str | uuid.UUID,
    actor_user_id: str | uuid.UUID | None,
    to: list[str],
    subject: str,
    html_body: str,
    text_body: str,
    smtp_service: EmailService,
) -> ConnectedMailDelivery:
    """Send from the approver's mailbox, a firm mailbox, or legacy SMTP."""
    try:
        to = normalize_recipient_mailboxes(to)
    except ValueError:
        return ConnectedMailDelivery(
            EmailDeliveryResult.INVALID_RECIPIENT,
            "Email delivery requires valid, individual recipient addresses",
            delivery_certainty=DELIVERY_NOT_ATTEMPTED,
        )

    tenant_uuid = uuid.UUID(str(tenant_id))
    actor_uuid = uuid.UUID(str(actor_user_id)) if actor_user_id else None
    user = None
    user_rows: dict[str, UserOAuthToken] = {}
    if actor_uuid:
        user = await db.scalar(
            select(User).where(User.id == actor_uuid, User.tenant_id == tenant_uuid)
        )
        rows = (
            (
                await db.execute(
                    select(UserOAuthToken).where(
                        UserOAuthToken.user_id == actor_uuid,
                        UserOAuthToken.tenant_id == tenant_uuid,
                        UserOAuthToken.provider.in_(_PROVIDERS),
                    )
                )
            )
            .scalars()
            .all()
        )
        user_rows = {row.provider: row for row in rows}

    reconnect: list[str] = []
    provider_order = _provider_order(user.oauth_provider if user else None)
    for provider in provider_order:
        row = user_rows.get(provider)
        if not row:
            continue
        if not _has_send_scope(provider, row.scopes):
            reconnect.append(provider)
            continue
        token = await get_fresh_user_token(
            db, str(tenant_uuid), str(actor_uuid), provider
        )
        # Token refresh may commit. SET LOCAL tenant context must be restored
        # before this shared session performs any further tenant-scoped work.
        await set_tenant_context(db, str(tenant_uuid))
        if not token:
            reconnect.append(provider)
            continue
        return await _attempt_provider(
            provider,
            token,
            tenant_id=tenant_uuid,
            actor_user_id=actor_uuid,
            firm_mailbox=False,
            to=to,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )

    tenant_rows = (
        (
            await db.execute(
                select(TenantCredential).where(
                    TenantCredential.tenant_id == tenant_uuid,
                    TenantCredential.provider.in_(_PROVIDERS),
                    TenantCredential.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    tenant_by_provider = {row.provider: row for row in tenant_rows}
    for provider in provider_order:
        row = tenant_by_provider.get(provider)
        if not row:
            continue
        if not _has_send_scope(provider, row.scopes):
            reconnect.append(provider)
            continue
        token = await get_fresh_token(db, str(tenant_uuid), provider)
        await set_tenant_context(db, str(tenant_uuid))
        if not token:
            reconnect.append(provider)
            continue
        return await _attempt_provider(
            provider,
            token,
            tenant_id=tenant_uuid,
            actor_user_id=actor_uuid,
            firm_mailbox=True,
            to=to,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )

    if reconnect:
        return ConnectedMailDelivery(
            EmailDeliveryResult.REAUTHORIZATION_REQUIRED,
            _reconnect_detail(reconnect),
            delivery_certainty=DELIVERY_NOT_ATTEMPTED,
        )

    # Backward compatibility for firms that intentionally configured SMTP and
    # have no Microsoft/Google mail grant. Cloud failure never reaches here.
    result = await smtp_service.send_email(
        to=to,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
    return ConnectedMailDelivery(
        result,
        (
            "Email sent through the configured SMTP mailbox"
            if result
            else f"Email delivery did not complete ({result.value})"
        ),
        provider="smtp" if result else None,
        delivery_certainty=(
            DELIVERY_CONFIRMED_SENT
            if result == EmailDeliveryResult.SENT
            else DELIVERY_OUTCOME_UNKNOWN
            if result == EmailDeliveryResult.FAILED
            else DELIVERY_NOT_ATTEMPTED
        ),
    )
