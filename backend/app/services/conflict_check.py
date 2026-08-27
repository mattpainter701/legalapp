"""
Shared conflict-check service.

Extracted from the contacts /conflict-check endpoint so it can be called
from both the contacts router (manual check) and the plugins router
(auto-check on matter create + manual re-run endpoint).
"""

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.matter_party import MatterParty
from app.models.plugin import Matter


async def run_conflict_check(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    names: list[str],
    emails: list[str],
    organization_names: list[str] | None = None,
    exclude_matter_ids: list[uuid.UUID] | None = None,
) -> dict:
    """
    Run a fuzzy conflict check across contacts and matter counterparties.

    Returns a dict with keys:
      clear   : bool — True when no matches found
      matches : list of dicts, each with keys:
                  contact_id, display_name, contact_type, email,
                  match_field, match_value, matter_ids, matter_names
    """
    if organization_names is None:
        organization_names = []
    if exclude_matter_ids is None:
        exclude_matter_ids = []
    matches: list[dict] = []

    def _escape_ilike(text: str) -> str:
        """Escape % and _ wildcards for ILIKE patterns."""
        return text.replace("%", "\\%").replace("_", "\\_")

    # Build (search_term, field_type) pairs
    terms = (
        [(n, "name") for n in names if n]
        + [(e, "email") for e in emails if e]
        + [(o, "organization") for o in organization_names if o]
    )

    # ── Phase 1: contact-based matches ───────────────────────────────────────
    for term, field_type in terms:
        pattern = f"%{_escape_ilike(term)}%"
        stmt = select(Contact).where(
            Contact.tenant_id == tenant_id,
            Contact.is_active.is_(True),
            or_(
                Contact.first_name.ilike(pattern),
                Contact.last_name.ilike(pattern),
                Contact.organization_name.ilike(pattern),
                Contact.email.ilike(pattern),
            ),
        )
        result = await db.execute(stmt)
        found = result.scalars().all()

        for c in found:
            # Matters where this contact is the client
            m_stmt = select(Matter).where(
                Matter.tenant_id == tenant_id,
                Matter.client_contact_id == c.id,
            )
            m_result = await db.execute(m_stmt)
            matters = m_result.scalars().all()

            # Matters where this contact is linked in any party role. The
            # standalone search must not miss opposing counsel, witnesses, or
            # other participants merely because they are not the client.
            party_stmt = (
                select(Matter)
                .join(MatterParty, MatterParty.matter_id == Matter.id)
                .where(
                    Matter.tenant_id == tenant_id,
                    MatterParty.tenant_id == tenant_id,
                    MatterParty.contact_id == c.id,
                )
            )
            party_result = await db.execute(party_stmt)
            party_matters = party_result.scalars().all()

            # Matters where counterparty string matches
            cp_stmt = select(Matter).where(
                Matter.tenant_id == tenant_id,
                Matter.counterparty.ilike(pattern),
            )
            cp_result = await db.execute(cp_stmt)
            cp_matters = cp_result.scalars().all()

            all_matters = {
                m.id: m
                for m in matters + party_matters + cp_matters
                if m.id not in exclude_matter_ids
            }

            # Avoid duplicate contact entries
            already_seen = any(m.get("contact_id") == c.id for m in matches)
            if not already_seen:
                matches.append(
                    {
                        "contact_id": c.id,
                        "display_name": c.display_name,
                        "contact_type": c.contact_type,
                        "email": c.email,
                        "match_field": field_type,
                        "match_value": term,
                        "matter_ids": list(all_matters.keys()),
                        "matter_names": [m.matter_name for m in all_matters.values()],
                    }
                )

    # ── Phase 2: matter counterparty-only matches (no Contact record) ─────────
    for term in list(names) + list(organization_names):
        if not term:
            continue
        pattern = f"%{_escape_ilike(term)}%"
        cp_filters = [
            Matter.tenant_id == tenant_id,
            Matter.counterparty.ilike(pattern),
            Matter.client_contact_id.is_(None),  # not already linked to a contact
        ]
        if exclude_matter_ids:
            cp_filters.append(Matter.id.notin_(exclude_matter_ids))
        cp_stmt = select(Matter).where(*cp_filters)
        cp_result = await db.execute(cp_stmt)
        cp_matters = cp_result.scalars().all()

        for m in cp_matters:
            matches.append(
                {
                    "contact_id": uuid.UUID("00000000-0000-0000-0000-000000000000"),
                    "display_name": m.counterparty,
                    "contact_type": "opposing_party",
                    "email": None,
                    "match_field": "matter_counterparty",
                    "match_value": term,
                    "matter_ids": [m.id],
                    "matter_names": [m.matter_name],
                }
            )

    return {
        "clear": len(matches) == 0,
        "matches": matches,
    }
