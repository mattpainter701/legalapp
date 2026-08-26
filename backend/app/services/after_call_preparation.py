"""Bounded, human-reviewed preparation for the After-call Concierge."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.communication_log import CommunicationLog
from app.models.contact import Contact, Lead
from app.models.prospect_follow_through import ProspectFollowThrough
from app.services.ai_request_broker import (
    AIDataClass,
    AIRequest,
    AIRequestBroker,
    AIRequestError,
)
from app.services.llm_routing import RouteTier


logger = logging.getLogger(__name__)

HANDOFF_SCHEMA = {
    "type": "object",
    "properties": {
        "brief": {"type": "string", "maxLength": 1800},
        "missing_information": {
            "type": "array",
            "items": {"type": "string", "maxLength": 240},
            "maxItems": 8,
        },
        "outreach_draft": {"type": "string", "maxLength": 2400},
        "suggested_next_action": {"type": "string", "maxLength": 300},
        "needs_attorney_review": {"type": "boolean"},
    },
    "required": [
        "brief",
        "missing_information",
        "outreach_draft",
        "suggested_next_action",
        "needs_attorney_review",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You prepare a saved law-firm intake for the assigned attorney.
Use only the supplied facts. Never decide conflicts, accept representation, give
legal advice, promise an outcome, or invent a deadline. Produce one concise brief,
a short list of facts the firm still needs, a courteous prospect outreach DRAFT,
and one next action. The attorney must review every output before it has effect.
Return only the required JSON object."""


def _contact_name(contact: Contact) -> str:
    display_name = str(getattr(contact, "display_name", "") or "").strip()
    if display_name:
        return display_name
    return " ".join(
        part
        for part in (contact.first_name, contact.middle_name, contact.last_name)
        if part
    ).strip()


def _baseline(
    *,
    lead: Lead,
    contact: Contact,
    communication: CommunicationLog | None,
) -> dict[str, Any]:
    name = _contact_name(contact) or "the prospect"
    purpose = (lead.description or "").strip()
    intake_note = (
        ((communication.summary or communication.body) if communication else "") or ""
    ).strip()
    brief_parts = [
        f"{name} contacted the firm",
        f"about {purpose}" if purpose else None,
        f"for {lead.practice_area}" if lead.practice_area else None,
    ]
    brief = " ".join(part for part in brief_parts if part).strip() + "."
    if intake_note and intake_note.casefold() not in brief.casefold():
        brief = f"{brief} Intake note: {intake_note[:1000]}"

    missing: list[str] = []
    if not purpose:
        missing.append("A concise description of the legal issue and requested help")
    if not lead.practice_area:
        missing.append("Practice area")
    if not contact.email:
        missing.append("Prospect email address")
    if not contact.phone:
        missing.append("Prospect phone number")
    if lead.conflict_check_status == "not_run":
        missing.append("Conflict review result")

    greeting = f"Hello {name}," if name != "the prospect" else "Hello,"
    outreach = (
        f"{greeting}\n\nThank you for contacting our office. We have recorded "
        "the information you shared and forwarded it for attorney review. "
        "We will follow up about next steps or any additional information we need. "
        "This message does not confirm that the firm has accepted representation."
    )
    return {
        "brief": brief,
        "missing_information": missing,
        "outreach_draft": outreach,
        "suggested_next_action": "Assigned attorney reviews the intake and chooses Pursue, Needs information, Decline, or Reassign",
        "needs_attorney_review": True,
    }


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value else None


async def prepare_after_call_handoff(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    prospect: ProspectFollowThrough,
    force: bool = False,
    broker: AIRequestBroker | None = None,
) -> dict[str, Any]:
    lead = await db.scalar(
        select(Lead).where(
            Lead.id == prospect.lead_id,
            Lead.tenant_id == tenant_id,
        )
    )
    if lead is None:
        raise ValueError("After-call preparation requires a tenant-scoped lead")
    contact = await db.scalar(
        select(Contact).where(
            Contact.id == lead.contact_id,
            Contact.tenant_id == tenant_id,
            Contact.is_active.is_(True),
        )
    )
    if contact is None:
        raise ValueError("Lead contact is not available in this tenant")
    communication = None
    if prospect.intake_communication_id:
        communication = await db.scalar(
            select(CommunicationLog).where(
                CommunicationLog.id == prospect.intake_communication_id,
                CommunicationLog.tenant_id == tenant_id,
                CommunicationLog.contact_id == contact.id,
            )
        )

    source_version = ":".join(
        (
            prospect.status,
            _iso(lead.updated_at) or "lead",
            (
                _iso(getattr(communication, "updated_at", None))
                or _iso(communication.created_at)
                or "call"
            )
            if communication
            else "no-call",
        )
    )
    current_metadata = dict(prospect.metadata_json or {})
    cached = current_metadata.get("assistant_preparation")
    if (
        not force
        and isinstance(cached, dict)
        and cached.get("source_version") == source_version
    ):
        return cached

    suggestion = _baseline(lead=lead, contact=contact, communication=communication)
    inference_available = False
    inference_error: str | None = None
    facts = {
        "lead": {
            "status": lead.status,
            "follow_through_status": prospect.status,
            "practice_area": lead.practice_area,
            "purpose": lead.description,
        },
        "prospect": {
            "email_known": bool(contact.email),
            "phone_known": bool(contact.phone),
        },
        "saved_intake_note": (
            (communication.body or communication.summary or "")[:12000]
            if communication
            else ""
        ),
    }
    request = AIRequest(
        tenant_id=tenant_id,
        actor_id=actor_user_id,
        surface="after_call_prepare",
        data_class=AIDataClass.PROSPECT_CONFIDENTIAL,
        messages=[
            {
                "role": "user",
                "content": json.dumps(facts, ensure_ascii=False),
            }
        ],
        system_prompt=SYSTEM_PROMPT,
        schema_name="after_call_preparation",
        schema=HANDOFF_SCHEMA,
        idempotency_key=(
            f"after-call:{prospect.id}:{source_version}:{uuid.uuid4()}"
            if force
            else f"after-call:{prospect.id}:{source_version}"
        ),
        route_tier=RouteTier.STANDARD,
        max_output_tokens=900,
        metadata={"prospect_id": str(prospect.id)},
    )
    try:
        result = await (broker or AIRequestBroker()).execute(db, request)
        suggestion = result.value
        inference_available = True
        inference = {
            "request_id": result.request_id,
            "provider_request_id": result.provider_request_id,
            "route": result.route.gateway_alias,
            "transport": result.transport.value,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
        }
    except AIRequestError as exc:
        inference_error = exc.code
        inference = {"error_code": exc.code}
        logger.info(
            "After-call preparation used deterministic fallback tenant=%s prospect=%s code=%s",
            tenant_id,
            prospect.id,
            exc.code,
        )
    except Exception:
        inference_error = "assistant_unavailable"
        inference = {"error_code": inference_error}
        logger.exception(
            "After-call preparation failed closed tenant=%s prospect=%s",
            tenant_id,
            prospect.id,
        )

    prepared = {
        "suggestion": suggestion,
        "source_version": source_version,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "inference_available": inference_available,
        "inference_error": inference_error,
        "inference": inference,
        "provenance": {
            "brief": "assistant_draft" if inference_available else "deterministic",
            "missing_information": "assistant_draft"
            if inference_available
            else "deterministic",
            "outreach_draft": "assistant_draft"
            if inference_available
            else "deterministic",
            "human_confirmation_required": True,
        },
    }
    current_metadata["assistant_preparation"] = prepared
    prospect.metadata_json = current_metadata
    await db.commit()
    return prepared
