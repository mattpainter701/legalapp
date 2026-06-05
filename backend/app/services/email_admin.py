"""
Admin notification email helper.

Routes through connected Microsoft/Google integration when available,
falling back to platform SMTP. This lets admin alerts piggyback on the
firm's existing cloud connection rather than requiring separate SMTP config.
"""

import logging
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant_credential import TenantCredential
from app.services.email import email_service
from app.services.token_vault import get_fresh_token

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


async def _send_via_microsoft(
    token: str,
    from_email: str,
    to_emails: list[str],
    subject: str,
    html_body: str,
) -> bool:
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": e}} for e in to_emails],
        },
        "saveToSentItems": False,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/users/{from_email}/sendMail",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            return resp.status_code == 202
    except Exception as exc:
        logger.warning("Microsoft mail send failed: %s", exc)
        return False


async def _send_via_google(
    token: str,
    to_emails: list[str],
    subject: str,
    html_body: str,
) -> bool:
    import base64
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart("alternative")
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                GMAIL_SEND_URL,
                json={"raw": raw},
                headers={"Authorization": f"Bearer {token}"},
            )
            return resp.status_code == 200
    except Exception as exc:
        logger.warning("Google mail send failed: %s", exc)
        return False


async def send_admin_notification(
    db: AsyncSession,
    tenant_id: str,
    to_emails: list[str],
    subject: str,
    html_body: str,
) -> None:
    """Send an admin notification, routing via connected cloud if available."""
    if not to_emails:
        return

    tid = uuid.UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id

    # Try Microsoft first
    ms_cred = await db.scalar(
        select(TenantCredential).where(
            TenantCredential.tenant_id == tid,
            TenantCredential.provider == "microsoft",
            TenantCredential.is_active.is_(True),
        )
    )
    if ms_cred and ms_cred.service_account_email:
        token = await get_fresh_token(db, tenant_id, "microsoft")
        if token:
            sent = await _send_via_microsoft(
                token, ms_cred.service_account_email, to_emails, subject, html_body
            )
            if sent:
                logger.info("Admin notification sent via Microsoft Graph to %s", to_emails)
                return

    # Try Google
    google_cred = await db.scalar(
        select(TenantCredential).where(
            TenantCredential.tenant_id == tid,
            TenantCredential.provider == "google",
            TenantCredential.is_active.is_(True),
        )
    )
    if google_cred:
        token = await get_fresh_token(db, tenant_id, "google")
        if token:
            sent = await _send_via_google(token, to_emails, subject, html_body)
            if sent:
                logger.info("Admin notification sent via Gmail to %s", to_emails)
                return

    # SMTP fallback
    try:
        await email_service.send_email(to=to_emails, subject=subject, html_body=html_body)
        logger.info("Admin notification sent via SMTP to %s", to_emails)
    except Exception as exc:
        logger.error("Failed to send admin notification: %s", exc)
