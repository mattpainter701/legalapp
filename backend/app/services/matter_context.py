"""Matter context loading and PII-safe context injection for LLM conversations."""

from typing import Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.billing import TimeEntry
from app.models.communication_log import CommunicationLog
from app.models.matter_assignment import MatterAssignment
from app.models.matter_note import MatterNote
from app.models.plugin import Matter, MatterEvent
from app.models.retainer import Retainer
from app.services.pii_detection import detect_pii


class MatterContextService:
    """Load and prepare matter context while respecting privacy constraints."""

    PII_SENSITIVE_FIELDS = {
        "counterparty",
        "outside_counsel",
        "initial_posture",
    }

    async def get_matter_context(
        self,
        db: AsyncSession,
        matter_id: str,
    ) -> Tuple[Optional[dict], bool, list]:
        """Load matter and return full context data with PII detection."""
        result = await db.execute(
            select(Matter)
            .options(
                selectinload(Matter.assignments).selectinload(MatterAssignment.user),
                selectinload(Matter.client),
            )
            .where(Matter.id == matter_id)
        )
        matter = result.unique().scalar_one_or_none()

        if not matter:
            return None, False, []

        # Core matter data
        matter_data = {
            "matter_name": matter.matter_name,
            "matter_type": matter.matter_type,
            "practice_area": matter.practice_area,
            "role": matter.role,
            "counterparty": matter.counterparty,
            "jurisdiction": matter.jurisdiction,
            "status": matter.status,
            "stage": matter.stage,
            "risk_level": matter.risk_level,
            "materiality": matter.materiality,
            "exposure_range": matter.exposure_range,
            "court": matter.court,
            "judge": matter.judge,
            "case_number": matter.case_number,
            "conflicts_status": matter.conflicts_status,
            "legal_hold_issued": matter.legal_hold_issued,
            "is_closed": matter.is_closed,
            "outcome": matter.outcome,
            "billing_cycle": matter.billing_cycle,
            "billing_method": matter.billing_method,
        }

        # Budget
        budget_actuals = await self._get_budget_summary(db, matter)
        matter_data["budget"] = budget_actuals

        # Retainers
        retainers = await self._get_retainer_summary(db, matter)
        matter_data["retainers"] = retainers

        # Team
        team = []
        for a in matter.assignments:
            team.append(
                {
                    "name": a.user.full_name if a.user else "Unknown",
                    "role": a.role,
                    "is_primary": a.is_primary,
                }
            )
        matter_data["team"] = team

        # Client
        if matter.client:
            matter_data["client"] = {
                "name": getattr(matter.client, "display_name", None),
                "email": matter.client.email,
                "phone": matter.client.phone,
            }

        # Recent notes (internal only, last 20)
        notes_q = await db.execute(
            select(MatterNote)
            .where(
                MatterNote.matter_id == matter.id,
                MatterNote.note_type.in_(["internal", "email"]),
            )
            .order_by(MatterNote.created_at.desc())
            .limit(20)
        )
        notes = notes_q.scalars().all()
        matter_data["recent_notes"] = [
            {
                "title": n.title,
                "content": n.content[:2000],
                "note_type": n.note_type,
                "created_at": str(n.created_at),
            }
            for n in notes
        ]

        # Recent events (last 20)
        events_q = await db.execute(
            select(MatterEvent)
            .where(MatterEvent.matter_id == matter.id)
            .order_by(MatterEvent.created_at.desc())
            .limit(20)
        )
        events = events_q.scalars().all()
        matter_data["recent_events"] = [
            {
                "type": e.event_type,
                "title": e.title,
                "content": e.content[:1000],
                "created_at": str(e.created_at),
            }
            for e in events
        ]

        # Recent communications (last 10)
        comms_q = await db.execute(
            select(CommunicationLog)
            .where(
                CommunicationLog.matter_id == matter.id,
            )
            .order_by(CommunicationLog.occurred_at.desc().nulls_last())
            .limit(10)
        )
        comms = comms_q.scalars().all()
        matter_data["recent_communications"] = [
            {
                "direction": c.direction,
                "channel": c.channel,
                "subject": c.subject,
                "summary": c.summary,
                "occurred_at": str(c.occurred_at) if c.occurred_at else None,
            }
            for c in comms
        ]

        # PII detection
        pii_findings = []
        has_pii = False
        for field in self.PII_SENSITIVE_FIELDS:
            value = getattr(matter, field, None)
            if value:
                value_str = str(value)
                findings = detect_pii(value_str)
                if findings:
                    has_pii = True
                    pii_findings.extend(findings)

        return matter_data, has_pii, pii_findings

    async def _get_budget_summary(self, db: AsyncSession, matter: Matter) -> dict:
        """Get budget vs actuals summary."""
        billed_q = await db.execute(
            select(
                func.coalesce(func.sum(TimeEntry.hours), 0),
                func.coalesce(func.sum(TimeEntry.amount), 0),
            ).where(
                TimeEntry.matter_id == matter.id,
                TimeEntry.is_billable.is_(True),
            )
        )
        hours, billed = billed_q.one()
        hours = float(hours or 0)
        billed = float(billed or 0)

        unbilled_q = await db.execute(
            select(func.coalesce(func.sum(TimeEntry.amount), 0)).where(
                TimeEntry.matter_id == matter.id,
                TimeEntry.is_billable.is_(True),
                TimeEntry.invoice_id.is_(None),
            )
        )
        unbilled = float(unbilled_q.scalar() or 0)

        return {
            "budget_amount": float(matter.budget_amount)
            if matter.budget_amount
            else None,
            "budget_currency": matter.budget_currency,
            "total_hours": hours,
            "total_billed": billed,
            "total_unbilled": unbilled,
            "utilization_pct": (
                round(billed / float(matter.budget_amount) * 100, 1)
                if matter.budget_amount and matter.budget_amount > 0
                else None
            ),
        }

    async def _get_retainer_summary(self, db: AsyncSession, matter: Matter) -> list:
        """Get active retainer balances."""
        ret_q = await db.execute(
            select(Retainer).where(
                Retainer.matter_id == matter.id,
                Retainer.status == "active",
            )
        )
        return [
            {
                "type": r.retainer_type,
                "amount": float(r.amount),
                "current_balance": float(r.current_balance),
            }
            for r in ret_q.scalars().all()
        ]

    def scrub_matter_context(
        self,
        matter_data: dict,
        privacy_mode: bool = False,
    ) -> dict:
        """Remove or mask sensitive fields from matter context if privacy_mode."""
        if not privacy_mode:
            return matter_data

        scrubbed = matter_data.copy()
        for field in self.PII_SENSITIVE_FIELDS:
            if field in scrubbed:
                scrubbed[field] = "[REDACTED]"

        if "client" in scrubbed:
            client = scrubbed["client"] or {}
            scrubbed["client"] = {
                "name": "[REDACTED]" if client else None,
                "email": "[REDACTED]" if client else None,
                "phone": "[REDACTED]" if client else None,
            }

        # Strip note content in privacy mode
        if "recent_notes" in scrubbed:
            scrubbed["recent_notes"] = [
                {
                    "title": n.get("title", ""),
                    "content": "[REDACTED]",
                    "note_type": n.get("note_type"),
                }
                for n in scrubbed["recent_notes"]
            ]

        return scrubbed

    def format_matter_context(
        self,
        matter_data: dict,
        scrubbed: bool = False,
    ) -> str:
        """Format matter data into readable context string for LLM injection."""
        lines = []

        if scrubbed:
            lines.append("Matter Context (Privacy Mode — sensitive fields redacted):")
        else:
            lines.append("Matter Context:")

        # Core fields
        core_fields = [
            "matter_name",
            "matter_type",
            "practice_area",
            "role",
            "counterparty",
            "jurisdiction",
            "status",
            "stage",
            "risk_level",
            "materiality",
            "court",
            "judge",
            "case_number",
            "billing_cycle",
            "billing_method",
        ]
        for key in core_fields:
            value = matter_data.get(key)
            if value and value != "[REDACTED]":
                display_key = key.replace("_", " ").title()
                lines.append(f"  {display_key}: {value}")

        # Budget
        budget = matter_data.get("budget", {})
        if budget:
            lines.append(
                f"  Budget: ${budget.get('budget_amount') or 'N/A'} {budget.get('budget_currency', 'USD')}"
            )
            lines.append(
                f"  Billed: ${budget.get('total_billed', 0):,.2f} ({budget.get('total_hours', 0):.1f}h)"
            )
            if budget.get("utilization_pct"):
                lines.append(f"  Utilization: {budget['utilization_pct']}%")

        # Team
        team = matter_data.get("team", [])
        if team:
            names = [f"{t['name']} ({t['role']})" for t in team]
            lines.append(f"  Team: {', '.join(names)}")

        # Client
        client = matter_data.get("client")
        if client and client.get("name"):
            lines.append(f"  Client: {client['name']}")

        # Recent notes (summarized)
        notes = matter_data.get("recent_notes", [])
        if notes:
            lines.append("  Recent Notes:")
            for n in notes[:5]:
                snippet = (n.get("content") or "")[:300]
                lines.append(
                    f"    - [{n.get('note_type')}] {n.get('title')}: {snippet}"
                )

        # Recent events
        events = matter_data.get("recent_events", [])
        if events:
            lines.append("  Recent Events:")
            for e in events[:10]:
                lines.append(
                    f"    - [{e['type']}] {e['title']}: {e.get('content', '')[:200]}"
                )

        return "\n".join(lines)

    async def get_safe_matter_context(
        self,
        db: AsyncSession,
        matter_id: str,
        privacy_mode: bool = False,
    ) -> Tuple[str, bool, list]:
        """Load matter context and prepare it safely for LLM injection."""
        matter_data, has_pii, pii_findings = await self.get_matter_context(
            db, matter_id
        )

        if not matter_data:
            return "", False, []

        scrubbed_data = self.scrub_matter_context(matter_data, privacy_mode)
        context_str = self.format_matter_context(scrubbed_data, scrubbed=privacy_mode)

        return context_str, has_pii, pii_findings
