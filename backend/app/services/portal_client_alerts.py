"""Tell a client that something is waiting for them in the portal.

Two things the firm does mid-case used to be silent. A document shared to the
portal flipped a bit and nothing told the client. A firm reply could not be
written at all. Both now send the same shape of nudge.

The nudge deliberately carries no content -- matter name and a link, never the
message body or the document itself. The portal is where privileged material
belongs; an email that reproduced it would quietly create a second, less
protected copy in whatever mailbox the client happens to use.
"""

import logging
from html import escape

from sqlalchemy import select

from app.config import get_settings
from app.models.contact import Contact
from app.services.connected_mail import send_client_email
from app.services.email import email_service

logger = logging.getLogger(__name__)


async def client_email_for_matter(db, tenant_id, matter):
    if not matter.client_contact_id:
        return None
    contact = await db.scalar(
        select(Contact).where(
            Contact.id == matter.client_contact_id, Contact.tenant_id == tenant_id
        )
    )
    email = (contact.email or "").strip() if contact else ""
    return email or None


async def notify_client_portal_update(
    db, *, tenant_id, actor_user_id, matter, subject, headline
):
    """Best-effort heads-up that there is something new in the portal.

    Returns True when an email was handed to a provider. A failure is logged
    and swallowed: whatever prompted the nudge is already durable, and the
    client will see it on their next sign-in regardless.
    """
    email = await client_email_for_matter(db, tenant_id, matter)
    if not email:
        return False
    url = f"{get_settings().FRONTEND_URL.rstrip('/')}/portal/client/matter"
    try:
        await send_client_email(
            db,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            to=[email],
            subject=subject,
            html_body=(
                f"<p>{escape(headline)}</p>"
                f'<p><a href="{escape(url, quote=True)}">Open your client portal</a></p>'
            ),
            text_body=f"{headline}\n\nOpen your client portal: {url}\n",
            smtp_service=email_service,
        )
        return True
    except Exception:
        logger.exception(
            "Portal notification could not be delivered for matter %s", matter.id
        )
        return False


def shared_document_headline(matter, filename):
    name = matter.matter_name or "your matter"
    return (
        f"Your legal team has shared a new document with you on {name}: {filename}. "
        "It is available in your secure client portal."
    )


def new_message_headline(matter):
    name = matter.matter_name or "your matter"
    return f"Your legal team has sent you a secure message about {name}."
