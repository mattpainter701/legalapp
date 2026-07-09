"""Email correspondence capture — archive full .eml messages onto matters.

For a matter, finds emails involving its listed parties (or mentioning its case
number) and stores the full ``.eml`` into the matter's file directory under the
``correspondence`` category, plus a ``CommunicationLog`` audit row linked to the
stored document.

Capture rules (per matter, ``matter.correspondence_rules`` JSON):
    {
      "enabled": true,
      "match_parties": true,
      "case_numbers": ["2024-CV-1234"],
      "keywords": [],          # reserved — keyword matching is deferred
      "directions": ["inbound", "outbound"]  # reserved — v1 captures both
    }

Keyword matching (beyond the ~2000-char preview the list readers return) and
direction filtering are intentionally deferred; the rules JSON already carries
``keywords``/``directions`` so they can be enabled later without a migration.
"""

import logging
import re
import uuid as uuid_mod
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import set_tenant_context
from app.models.communication_log import CommunicationLog
from app.models.contact import Contact
from app.models.matter_document import MatterDocument
from app.models.matter_party import MatterParty
from app.models.plugin import Matter
from app.services.email_agent import _extract_email_addresses
from app.services.matter_file_store import MatterFileStore

settings = get_settings()
logger = logging.getLogger(__name__)

matter_file_store = MatterFileStore()


def _default_rules() -> dict:
    return {
        "enabled": True,
        "match_parties": True,
        "case_numbers": [],
        "keywords": [],
        "directions": ["inbound", "outbound"],
    }


def _resolve_rules(matter: Matter, *, force_enabled: bool = False) -> dict:
    """Merge a matter's stored rules over the defaults.

    ``force_enabled`` is used for manual ("Scan now") scans so capture works
    out-of-the-box before any rules are configured.
    """
    rules = _default_rules()
    stored = matter.correspondence_rules or {}
    if isinstance(stored, dict):
        rules.update(stored)
    if force_enabled:
        rules["enabled"] = True
    return rules


def _normalize_addr_field(value) -> list[str]:
    """Normalize a provider 'to'/'cc' field (list or header string) to addresses."""
    if not value:
        return []
    if isinstance(value, list):
        text = ", ".join(str(v) for v in value)
    else:
        text = str(value)
    return [a.lower() for a in _extract_email_addresses(text)]


def _email_addresses(email: dict) -> dict:
    """Return {'from', 'to', 'cc', 'all'} with lowercased addresses."""
    from_candidates = _extract_email_addresses(email.get("from", "") or "")
    from_addr = from_candidates[0].lower() if from_candidates else ""
    to_list = _normalize_addr_field(email.get("to"))
    cc_list = _normalize_addr_field(email.get("cc"))
    all_set = set(filter(None, [from_addr, *to_list, *cc_list]))
    return {"from": from_addr, "to": to_list, "cc": cc_list, "all": all_set}


def _email_text(email: dict) -> str:
    """Searchable text for keyword/case-number matching (subject + preview)."""
    return f"{email.get('subject', '')}\n{email.get('body_preview', '')}".lower()


def _email_occurred_at(email: dict) -> datetime:
    raw = email.get("received") or email.get("date")
    if raw:
        try:
            from dateutil import parser as dateutil_parser

            dt = dateutil_parser.parse(str(raw))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass
    return datetime.now(timezone.utc)


def _slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip()).strip("-").lower()
    return (slug[:max_len] or "message").strip("-")


def _eml_filename(email: dict, message_id: str) -> str:
    occurred = _email_occurred_at(email).strftime("%Y-%m-%d")
    subject = _slugify(email.get("subject") or "no-subject")
    short = re.sub(r"[^a-zA-Z0-9]+", "", str(message_id))[-10:] or "msg"
    return f"{occurred}_{subject}_{short}.eml"


async def _matter_party_addresses(
    db: AsyncSession, tenant_id: uuid_mod.UUID, matter: Matter
) -> set[str]:
    """All party + client contact email addresses for a matter, lowercased."""
    addresses: set[str] = set()

    party_q = await db.execute(
        select(Contact.email)
        .join(MatterParty, MatterParty.contact_id == Contact.id)
        .where(
            MatterParty.tenant_id == tenant_id,
            MatterParty.matter_id == matter.id,
            Contact.email.isnot(None),
        )
    )
    for (email_addr,) in party_q.all():
        if email_addr:
            addresses.add(email_addr.lower())

    if matter.client_contact_id:
        client_q = await db.execute(
            select(Contact.email).where(
                Contact.tenant_id == tenant_id,
                Contact.id == matter.client_contact_id,
                Contact.email.isnot(None),
            )
        )
        client_email = client_q.scalar_one_or_none()
        if client_email:
            addresses.add(client_email.lower())

    return addresses


def _matter_case_numbers(matter: Matter, rules: dict) -> list[str]:
    """Case numbers to match — explicit rule list, else the matter's case_number."""
    case_numbers = [str(c).strip() for c in (rules.get("case_numbers") or []) if c]
    if not case_numbers and matter.case_number:
        case_numbers = [matter.case_number.strip()]
    return [c for c in case_numbers if c]


def evaluate_matter_rules(
    matter: Matter,
    email: dict,
    party_addresses: set[str],
    rules: dict,
) -> bool:
    """Decide whether ``email`` should be captured for ``matter``.

    Capture when the matter is enabled and either a listed party address appears
    on the email OR a case number is mentioned in the subject/preview.
    """
    if not rules.get("enabled", False):
        return False

    addrs = _email_addresses(email)["all"]

    if (
        rules.get("match_parties", True)
        and party_addresses
        and (addrs & party_addresses)
    ):
        return True

    text = _email_text(email)
    for case_number in _matter_case_numbers(matter, rules):
        if case_number.lower() in text:
            return True

    return False


async def _already_captured(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    matter_id: uuid_mod.UUID,
    refs: list[str],
) -> bool:
    if not refs:
        return False
    existing = await db.execute(
        select(CommunicationLog.id).where(
            CommunicationLog.tenant_id == tenant_id,
            CommunicationLog.matter_id == matter_id,
            CommunicationLog.channel == "email",
            CommunicationLog.external_ref.in_(refs),
        )
    )
    return existing.scalar_one_or_none() is not None


async def capture_email_for_matter(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID | None,
    matter: Matter,
    email: dict,
    provider: str,
    mailbox_address: str | None = None,
) -> bool:
    """Archive a single email into a matter. Returns True if newly captured."""
    # Each prior capture commits, which clears the SET LOCAL tenant GUC, so
    # re-establish tenant context before any RLS-scoped query (mirrors
    # email_agent._auto_log_and_task).
    await set_tenant_context(db, str(tenant_id))

    message_id = email.get("id")
    if not message_id:
        return False

    external_ref = email.get("external_ref") or f"{provider}:{message_id}"
    possible_refs = [external_ref]
    if message_id != external_ref:
        possible_refs.append(message_id)

    if await _already_captured(db, tenant_id, matter.id, possible_refs):
        logger.info(
            "Correspondence already captured for matter %s: %s",
            matter.id,
            external_ref,
        )
        return False

    # Fetch the full .eml from the provider.
    try:
        if provider == "microsoft":
            from app.services.microsoft_mail import ms_read_mail_raw

            eml_bytes = await ms_read_mail_raw(
                db, str(tenant_id), str(user_id), message_id
            )
        elif provider == "google":
            from app.services.google_mail import gmail_read_raw

            eml_bytes = await gmail_read_raw(
                db, str(tenant_id), str(user_id), message_id
            )
        else:
            raise ValueError(f"Unknown email provider: {provider}")
    except Exception as exc:
        logger.warning(
            "Failed to fetch raw email %s for matter %s: %s",
            message_id,
            matter.id,
            exc,
        )
        return False

    addrs = _email_addresses(email)
    is_outbound = bool(mailbox_address and addrs["from"] == mailbox_address.lower())
    filename = _eml_filename(email, message_id)

    # Load tenant cloud preference for storage routing.
    from app.models.tenant import TenantSettings

    ts_result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )
    ts = ts_result.scalar_one_or_none()
    preferred_provider = ts.primary_cloud_provider if ts else None

    try:
        storage_path = await matter_file_store.store_matter_file(
            db=db,
            tenant_id=str(tenant_id),
            matter_slug=matter.slug,
            category="correspondence",
            filename=filename,
            content=eml_bytes,
            content_type="message/rfc822",
            matter_cloud_folder=matter.cloud_folder,
            preferred_provider=preferred_provider,
        )
    except Exception as exc:
        logger.warning(
            "Failed to store .eml for matter %s message %s: %s",
            matter.id,
            message_id,
            exc,
        )
        return False

    doc = MatterDocument(
        tenant_id=tenant_id,
        matter_id=matter.id,
        uploaded_by_user_id=user_id,
        filename=filename,
        content_type="message/rfc822",
        file_size=len(eml_bytes),
        storage_path=storage_path,
        description=f"Email: {(email.get('subject') or '(no subject)')[:400]}",
        document_category="correspondence",
    )
    db.add(doc)
    await db.flush()  # populate doc.id for the FK below

    log = CommunicationLog(
        tenant_id=tenant_id,
        direction="outbound" if is_outbound else "inbound",
        channel="email",
        status="sent" if is_outbound else "received",
        subject=(email.get("subject") or "(no subject)")[:500],
        body=email.get("body_preview"),
        matter_id=matter.id,
        created_by_user_id=user_id,
        occurred_at=_email_occurred_at(email),
        external_ref=external_ref,
        document_id=doc.id,
        thread_ref=email.get("conversation_id") or email.get("thread_id"),
        participants={
            "from": addrs["from"],
            "to": addrs["to"],
            "cc": addrs["cc"],
        },
    )
    db.add(log)
    await db.commit()
    logger.info(
        "Captured correspondence for matter %s: %s (%s)",
        matter.id,
        external_ref,
        filename,
    )
    return True


async def scan_and_capture(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    provider: str,
    *,
    matter_id: str | None = None,
    max_emails: int | None = None,
    mailbox_address: str | None = None,
) -> dict:
    """Read recent emails and capture matching ones into matters.

    When ``matter_id`` is provided (manual "Scan now"), only that matter is
    considered and its rules are force-enabled. Otherwise every matter with
    ``correspondence_rules.enabled`` is considered (party + case-number sweep).
    """
    tid = uuid_mod.UUID(tenant_id)
    uid = uuid_mod.UUID(user_id)
    limit = max_emails or settings.CORRESPONDENCE_CAPTURE_MAX_EMAILS

    # Ensure tenant context is set for the candidate-matter / party queries below
    # (a prior mailbox's captures may have committed and cleared the GUC).
    await set_tenant_context(db, tenant_id)

    if provider == "microsoft":
        from app.services.microsoft_mail import ms_read_mail_user

        emails = await ms_read_mail_user(db, tenant_id, user_id, max_results=limit)
    elif provider == "google":
        from app.services.google_mail import gmail_read_mail

        emails = await gmail_read_mail(db, tenant_id, user_id, max_results=limit)
    else:
        raise ValueError(f"Unknown email provider: {provider}")

    # Build the candidate matter set.
    if matter_id:
        result = await db.execute(
            select(Matter).where(
                Matter.id == uuid_mod.UUID(matter_id),
                Matter.tenant_id == tid,
            )
        )
        candidate_matters = [m for m in [result.scalar_one_or_none()] if m]
        force_enabled = True
    else:
        result = await db.execute(
            select(Matter).where(
                Matter.tenant_id == tid,
                Matter.is_closed.is_(False),
                Matter.correspondence_rules.isnot(None),
            )
        )
        candidate_matters = [
            m
            for m in result.scalars().all()
            if (m.correspondence_rules or {}).get("enabled")
        ]
        force_enabled = False

    # Precompute per-matter rules + party addresses once.
    matter_ctx: list[tuple[Matter, dict, set[str]]] = []
    for matter in candidate_matters:
        rules = _resolve_rules(matter, force_enabled=force_enabled)
        if not rules.get("enabled"):
            continue
        addresses = await _matter_party_addresses(db, tid, matter)
        matter_ctx.append((matter, rules, addresses))

    scanned = len(emails)
    captured = 0
    skipped = 0

    for email in emails:
        if email.get("id") and not email.get("external_ref"):
            email["external_ref"] = f"{provider}:{email['id']}"
        for matter, rules, addresses in matter_ctx:
            if not evaluate_matter_rules(matter, email, addresses, rules):
                continue
            try:
                did_capture = await capture_email_for_matter(
                    db,
                    tid,
                    uid,
                    matter,
                    email,
                    provider,
                    mailbox_address=mailbox_address,
                )
            except Exception as exc:
                logger.warning(
                    "Capture failed for matter %s email %s: %s",
                    matter.id,
                    email.get("id"),
                    exc,
                )
                await db.rollback()
                continue
            if did_capture:
                captured += 1
            else:
                skipped += 1

    return {"scanned": scanned, "captured": captured, "skipped": skipped}
