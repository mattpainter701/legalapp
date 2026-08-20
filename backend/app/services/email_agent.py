import json
import logging
import uuid as uuid_mod
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.utils.guardrails import prepare_provider_messages, prepare_provider_text

settings = get_settings()
logger = logging.getLogger(__name__)


async def _auto_log_and_task(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    email: dict,
    classification: dict,
    matched_matter_ids: list[uuid_mod.UUID] | None = None,
) -> None:
    """Persist an email only when it is tied to a matter contact.

    A mailbox scan may classify every message it can see, but classification is
    transient user-facing triage.  It must not turn newsletters, firewall
    notices, or other unrelated inbox traffic into tenant records.  Durable
    communications, notes, and deadline tasks require a contact that is linked
    to an active matter.
    """
    try:
        from app.database import set_tenant_context
        from app.models.communication_log import CommunicationLog
        from app.models.matter_note import MatterNote
        from app.models.task import Task
        from app.services.task_workflow import append_task_event

        await set_tenant_context(db, tenant_id)
        tid = uuid_mod.UUID(tenant_id)
        uid = uuid_mod.UUID(user_id)

        if matched_matter_ids is None:
            matched_matter_ids = await _match_email_to_matters(db, tid, email)
        if not matched_matter_ids:
            logger.debug(
                "Keeping unmatched mailbox message out of durable records: %s",
                email.get("id") or email.get("subject") or "unknown",
            )
            return

        message_id = email.get("id")
        provider = email.get("provider") or email.get("source")
        external_ref = email.get("external_ref") or (
            f"{provider}:{message_id}" if provider and message_id else message_id
        )

        if external_ref:
            possible_refs = [external_ref]
            if message_id and message_id != external_ref:
                possible_refs.append(message_id)
            existing = await db.execute(
                select(CommunicationLog.id).where(
                    CommunicationLog.tenant_id == tid,
                    CommunicationLog.channel == "email",
                    CommunicationLog.external_ref.in_(possible_refs),
                )
            )
            if existing.scalar_one_or_none():
                logger.info("Skipping already logged email %s", external_ref)
                return

        log = CommunicationLog(
            tenant_id=tid,
            direction="inbound",
            channel="email",
            status="received",
            subject=email.get("subject") or "(no subject)",
            summary=classification.get("summary"),
            body=email.get("body_preview"),
            created_by_user_id=uid,
            occurred_at=datetime.now(timezone.utc),
            external_ref=external_ref,
            matter_id=matched_matter_ids[0] if matched_matter_ids else None,
        )
        db.add(log)

        # Create MatterNote for each matched matter
        for matter_id in matched_matter_ids:
            note = MatterNote(
                tenant_id=tid,
                matter_id=matter_id,
                author_id=uid,
                note_type="email",
                title=f"Email: {email.get('subject', '(no subject)')[:500]}",
                content=(
                    f"**From:** {email.get('from', 'Unknown')}\n"
                    f"**To:** {email.get('to', 'Unknown')}\n"
                    f"**Received:** {email.get('receivedDateTime', '')}\n\n"
                    f"{email.get('body_preview', '')}"
                ),
                is_billable=False,
            )
            db.add(note)

        # Create task for deadlines
        deadline_str = classification.get("deadline_mentioned")
        if deadline_str:
            try:
                from dateutil import parser as dateutil_parser

                due_date = dateutil_parser.parse(str(deadline_str), fuzzy=True).date()
                task = Task(
                    tenant_id=tid,
                    title=f"Deadline from email: {email.get('subject', '')[:200]}",
                    description=classification.get("action_needed")
                    or classification.get("summary"),
                    task_type="deadline",
                    priority="high"
                    if classification.get("urgency") in ("critical", "high")
                    else "medium",
                    due_date=due_date,
                    created_by_user_id=uid,
                    assigned_to_user_id=uid,
                    source="email_agent",
                    external_ref=external_ref,
                    matter_id=matched_matter_ids[0] if matched_matter_ids else None,
                )
                db.add(task)
                await db.flush()
                append_task_event(
                    db,
                    task,
                    event_type="created",
                    actor_user_id=uid,
                    to_status="pending",
                )
                append_task_event(
                    db,
                    task,
                    event_type="assigned",
                    actor_user_id=uid,
                    metadata={"assigned_to_user_id": str(uid)},
                )
            except Exception as parse_err:
                logger.debug(
                    "Could not parse deadline '%s': %s", deadline_str, parse_err
                )

        await db.commit()
    except Exception as exc:
        logger.warning("Auto log/task creation failed: %s", exc)


async def _match_email_to_matters(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    email: dict,
) -> list[uuid_mod.UUID]:
    """Find matters linked to a contact who *sent* the message.

    This path only ever handles inbound mailbox sync.  Matching to/cc/bcc as
    well would archive mail from an unknown sender whenever a known contact was
    merely copied — or whenever the firm's own address was a recipient — which
    turns copied and misaddressed traffic into matter correspondence.
    """
    from sqlalchemy import func, select

    from app.models.contact import Contact
    from app.models.matter_party import MatterParty
    from app.models.plugin import Matter

    # Sender only: see the docstring.  Recipients are deliberately not matched.
    addresses = set()
    sender = email.get("from", "") or ""
    for addr in _extract_email_addresses(sender):
        addresses.add(addr.lower())

    if not addresses:
        return []

    # Find contacts matching any of these emails
    contact_q = await db.execute(
        select(Contact.id, Contact.email).where(
            Contact.tenant_id == tenant_id,
            Contact.is_active.is_(True),
            func.lower(Contact.email).in_(addresses),
        )
    )
    contacts = contact_q.all()
    contact_ids = [c[0] for c in contacts]

    if not contact_ids:
        return []

    # Find active matters linked to these contacts via MatterParty or client_contact_id
    party_q = await db.execute(
        select(MatterParty.matter_id)
        .join(Matter, Matter.id == MatterParty.matter_id)
        .where(
            MatterParty.tenant_id == tenant_id,
            MatterParty.contact_id.in_(contact_ids),
            Matter.tenant_id == tenant_id,
            Matter.is_closed.is_(False),
        )
    )
    matter_ids_from_parties = [row[0] for row in party_q.all()]

    client_q = await db.execute(
        select(Matter.id).where(
            Matter.tenant_id == tenant_id,
            Matter.client_contact_id.in_(contact_ids),
            Matter.is_closed.is_(False),
        )
    )
    matter_ids_from_client = [row[0] for row in client_q.all()]

    # Combine and deduplicate
    all_ids = list(set(matter_ids_from_parties + matter_ids_from_client))
    return all_ids


def _extract_email_addresses(text: str) -> list[str]:
    """Extract email addresses from a header string like 'Name <email@domain.com>'."""
    import re

    if not text:
        return []
    # Match email patterns
    pattern = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    return re.findall(pattern, str(text))


class EmailAgent:
    """
    Orchestrates email reading + LLM classification + draft response generation.
    Uses per-user delegated OAuth tokens.
    """

    LLM_CLASSIFICATION_PROMPT = """You are a legal email triage assistant for a law firm. Analyze the following email and classify it with a structured JSON response.

EMAIL SUBJECT: {subject}
FROM: {sender}
BODY PREVIEW: {body}

Respond with a JSON object only (no markdown, no explanation):
{{
  "category": "legal_query|client_communication|court_filing|billing|administrative|spam|other",
  "urgency": "critical|high|medium|low",
  "summary": "1-2 sentence summary of the email",
  "action_needed": "specific action the attorney needs to take, or null",
  "deadline_mentioned": "any deadline date mentioned, or null",
  "requires_response": true/false,
  "suggested_response": "concise suggested reply text, or null if no reply needed"
}}"""

    LLM_DRAFT_PROMPT = """You are a legal assistant drafting an email response for an attorney. Use the attorney's practice profile for context. Be professional, concise, and legally precise.

PRACTICE CONTEXT: {practice_context}

ORIGINAL EMAIL:
Subject: {subject}
From: {sender}
Body: {body}

CLASSIFICATION: {classification}

Draft a professional response email. The attorney will review before sending. Do not include placeholder greetings like "[Your Name]". End with the attorney's standard signature block."""

    async def classify_email(
        self,
        email: dict,
        llm_service: Any,
        tenant_name: str,
        model: str | None = None,
        privacy_mode: bool = False,
    ) -> dict:
        prompt = self.LLM_CLASSIFICATION_PROMPT.format(
            subject=email.get("subject", ""),
            sender=email.get("from", ""),
            body=email.get("body_preview", "")[:3000],
        )
        messages = prepare_provider_messages(
            [{"role": "user", "content": prompt}], privacy_mode
        )
        try:
            response_text, _, _ = await llm_service.complete(
                messages,
                prepare_provider_text(tenant_name, privacy_mode),
                context="",
                use_premium=False,
                provider="litellm",
                model=model,
                response_format={"type": "json_object"},
            )
            return json.loads(response_text)
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("Email classification failed: %s", exc)
            return {
                "category": "other",
                "urgency": "low",
                "summary": email.get("subject", "No subject"),
                "action_needed": None,
                "deadline_mentioned": None,
                "requires_response": False,
                "suggested_response": None,
            }

    async def draft_response(
        self,
        email: dict,
        classification: dict,
        llm_service: Any,
        tenant_name: str,
        practice_context: str = "General legal practice",
        model: str | None = None,
        privacy_mode: bool = False,
    ) -> str:
        prompt = self.LLM_DRAFT_PROMPT.format(
            subject=email.get("subject", ""),
            sender=email.get("from", ""),
            body=email.get("body_preview", "")[:3000],
            classification=json.dumps(classification, indent=2),
            practice_context=practice_context,
        )
        messages = prepare_provider_messages(
            [{"role": "user", "content": prompt}], privacy_mode
        )
        try:
            response_text, _, _ = await llm_service.complete(
                messages,
                prepare_provider_text(tenant_name, privacy_mode),
                context="",
                use_premium=True,
                provider="litellm",
                model=model,
            )
            return response_text.strip()
        except Exception as exc:
            logger.warning("Draft response failed: %s", exc)
            return ""

    async def process_emails(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        provider: str,
        llm_service: Any,
        tenant_name: str,
        max_emails: int = 20,
        standard_model: str | None = None,
        premium_model: str | None = None,
        privacy_mode: bool = False,
    ) -> list[dict]:
        results = []

        try:
            if provider == "microsoft":
                from app.services.microsoft_mail import ms_read_mail_user

                emails = await ms_read_mail_user(
                    db, tenant_id, user_id, max_results=max_emails
                )
            elif provider == "google":
                from app.services.google_mail import gmail_read_mail

                emails = await gmail_read_mail(
                    db, tenant_id, user_id, max_results=max_emails
                )
            else:
                raise ValueError(f"Unknown email provider: {provider}")
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        for email in emails:
            if email.get("id") and not email.get("external_ref"):
                email["external_ref"] = f"{provider}:{email['id']}"
            email.setdefault("provider", provider)
            matched_matter_ids = await _match_email_to_matters(
                db, uuid_mod.UUID(tenant_id), email
            )
            if not matched_matter_ids:
                logger.debug("Ignoring email with no matter-linked contact")
                continue
            classification = await self.classify_email(
                email,
                llm_service,
                tenant_name,
                model=standard_model,
                privacy_mode=privacy_mode,
            )

            draft_response = None
            if classification.get("requires_response"):
                draft_response = await self.draft_response(
                    email,
                    classification,
                    llm_service,
                    tenant_name,
                    model=premium_model,
                    privacy_mode=privacy_mode,
                )

            await _auto_log_and_task(
                db,
                tenant_id,
                user_id,
                email,
                classification,
                matched_matter_ids=matched_matter_ids,
            )

            results.append(
                {
                    "email_id": email.get("id"),
                    "subject": email.get("subject"),
                    "from": email.get("from"),
                    "received": email.get("received") or email.get("date"),
                    "classification": classification,
                    "draft_response": draft_response,
                }
            )

        return results


email_agent = EmailAgent()
