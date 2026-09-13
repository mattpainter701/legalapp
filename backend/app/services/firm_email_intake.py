"""Firm intake suggestions. Email content never directly executes workflow actions."""

from __future__ import annotations

import asyncio
import re
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models.plugin import Matter
from app.models.user import User
from app.services.email_agent import _match_email_to_matters
from app.services.email_task_tags import parse_email_task_tag


def verify_staff_signature(raw: bytes, sender: str) -> bool:
    """Verify aligned DKIM ourselves; never trust supplied Authentication-Results.

    Require signed From and Subject, the entire body, and an exact signing
    domain match. Bound signature count and DNS time. SPF-only senders must
    configure DKIM before using the firm-wide address.
    """
    import dkim

    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        addresses = getaddresses([str(v) for v in message.get_all("from", [])])
        if len(message.get_all("from", [])) != 1 or len(addresses) != 1:
            return False
        if addresses[0][1].lower() != sender.lower() or "@" not in sender:
            return False
        if len(message.get_all("subject", [])) != 1:
            return False
        domain = sender.rsplit("@", 1)[1].lower().encode("ascii")
        for index, value in enumerate(message.get_all("dkim-signature", [])[:3]):
            tags = dkim.util.parse_tag_value(str(value).encode("ascii"))
            signed = {v.strip().lower() for v in tags.get(b"h", b"").split(b":")}
            if (
                tags.get(b"d", b"").lower() != domain
                or b"l" in tags
                or not {b"from", b"subject"}.issubset(signed)
                or tags.get(b"a") != b"rsa-sha256"
            ):
                continue
            if dkim.DKIM(raw, timeout=3).verify(idx=index):
                return True
    except (ValueError, UnicodeError, dkim.DKIMException):
        return False
    return False


async def active_staff(db, tenant_id):
    result = await db.execute(
        select(User)
        .where(
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
            User.principal_type == "human",
            User.role.notin_(["client", "portal"]),
        )
        .order_by(User.full_name, User.email)
    )
    return list(result.scalars().all())


async def authenticated_submitter(db, tenant_id, raw, sender):
    staff = await active_staff(db, tenant_id)
    matches = [u for u in staff if u.email.lower() == sender.lower()]
    if len(matches) != 1:
        return None
    if not await asyncio.to_thread(verify_staff_signature, raw, sender):
        return None
    return matches[0]


def todo_suggestion(subject, received_at, timezone_name, staff, submitter_id):
    """Only leading [TASK]; comma-separated name is an optional assignee hint."""
    suggestion = parse_email_task_tag(
        subject, received_at=received_at.astimezone(ZoneInfo(timezone_name)).date()
    )
    if suggestion is None or suggestion.tag != "task":
        return None
    title = suggestion.title
    assignee_id = submitter_id
    assignee_hint = None
    if "," in title:
        assignee_hint, title = (s.strip() for s in title.split(",", 1))
        needle = assignee_hint.casefold()
        matches = [
            u
            for u in staff
            if needle
            in {
                u.email.casefold(),
                (u.full_name or "").casefold(),
                (u.full_name or "").split(" ")[0].casefold(),
            }
        ]
        assignee_id = str(matches[0].id) if len(matches) == 1 else None
    if not title:
        return None
    # These are ordinary to-dos, even when their title contains "file" or "call".
    return {
        "title": title,
        "due_date": suggestion.due_date.isoformat() if suggestion.due_date else None,
        "assigned_to_user_id": str(assignee_id) if assignee_id else None,
        "assignee_hint": assignee_hint,
    }


def forwarded_senders(raw):
    """Extract original-sender hints; these are suggestions, never authentication."""
    message = BytesParser(policy=policy.default).parsebytes(raw)
    hints = []
    for part in message.walk():
        if part is not message and part.get_content_type() == "message/rfc822":
            for original in part.get_payload()[:5]:
                hints.extend(str(v) for v in original.get_all("from", []))
        elif (
            part.get_content_type() in {"text/plain", "text/html"}
            and part.get_content_disposition() != "attachment"
        ):
            try:
                text = part.get_content()[:32000]
            except (LookupError, UnicodeError):
                continue
            if part.get_content_type() == "text/html":
                from app.services.inbound_email import _HTMLTextExtractor

                extractor = _HTMLTextExtractor()
                extractor.feed(text)
                text = " ".join(extractor.parts)
                hints.extend(
                    re.findall(
                        r"(?i)\bFrom:\s*(.{1,512}?)(?=\s+(?:Sent|Date|To|Subject):|$)",
                        text,
                    )[:10]
                )
            else:
                hints.extend(re.findall(r"(?im)^\s*>?\s*From:\s*(.+)$", text)[:10])
    return sorted(
        {address.lower() for _, address in getaddresses(hints) if "@" in address}
    )[:20]


async def suggest_matters(db, tenant_id, raw, preview):
    ids = set()
    for sender in forwarded_senders(raw):
        ids.update(await _match_email_to_matters(db, tenant_id, {"from": sender}))
    result = await db.execute(
        select(Matter).where(Matter.tenant_id == tenant_id, Matter.is_closed.is_(False))
    )
    matters = list(result.scalars().all())
    case_ids = {
        m.id
        for m in matters
        if m.case_number
        and re.search(r"(?<!\w)" + re.escape(m.case_number) + r"(?!\w)", preview, re.I)
    }
    if case_ids:
        # Conflicting evidence stays ambiguous; never choose an arbitrary first match.
        ids = ids & case_ids if ids & case_ids else ids | case_ids
    return [{"id": str(m.id), "title": m.matter_name} for m in matters if m.id in ids]
