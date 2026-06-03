"""Matter context loading and PII-safe context injection."""

from typing import Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plugin import Matter
from app.services.pii_detection import detect_pii


class MatterContextService:
    """Load and prepare matter context while respecting privacy constraints."""

    PII_SENSITIVE_FIELDS = {
        "counterparty",  # Often contains client/party names
        "outside_counsel",  # Contact information
        "internal_owners",  # Names and contact info
        "initial_posture",  # May contain sensitive strategic info
    }

    async def get_matter_context(
        self,
        db: AsyncSession,
        matter_id: str,
    ) -> Tuple[Optional[dict], bool, list]:
        """
        Load matter and return its context data.
        Returns: (matter_data, has_pii, pii_findings)
        """
        result = await db.execute(select(Matter).where(Matter.id == matter_id))
        matter = result.scalar_one_or_none()

        if not matter:
            return None, False, []

        # Build matter context dict
        matter_data = {
            "matter_name": matter.matter_name,
            "matter_type": matter.matter_type,
            "role": matter.role,
            "jurisdiction": matter.jurisdiction,
            "status": matter.status,
            "stage": matter.stage,
            "risk_level": matter.risk_level,
            "materiality": matter.materiality,
            "exposure_range": matter.exposure_range,
            "conflicts_status": matter.conflicts_status,
            "legal_hold_issued": matter.legal_hold_issued,
            "is_closed": matter.is_closed,
            "outcome": matter.outcome,
        }

        # Check for PII in sensitive fields
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

    def scrub_matter_context(
        self,
        matter_data: dict,
        privacy_mode: bool = False,
    ) -> dict:
        """
        Remove or mask sensitive fields from matter context if privacy_mode.
        """
        if not privacy_mode:
            return matter_data

        scrubbed = matter_data.copy()

        # In privacy mode, strip sensitive fields entirely
        for field in self.PII_SENSITIVE_FIELDS:
            if field in scrubbed:
                scrubbed[field] = "[REDACTED]"

        return scrubbed

    def format_matter_context(
        self,
        matter_data: dict,
        scrubbed: bool = False,
    ) -> str:
        """Format matter data into readable context string for LLM."""
        lines = []

        if scrubbed:
            lines.append("Matter Context (Privacy Mode - Sensitive fields redacted):")
        else:
            lines.append("Matter Context:")

        for key, value in matter_data.items():
            if value and value != "[REDACTED]":
                # Format key as title case
                display_key = key.replace("_", " ").title()
                lines.append(f"  {display_key}: {value}")

        return "\n".join(lines)

    async def get_safe_matter_context(
        self,
        db: AsyncSession,
        matter_id: str,
        privacy_mode: bool = False,
    ) -> Tuple[str, bool, list]:
        """
        Load matter context and prepare it safely.
        Returns: (formatted_context, has_pii, pii_findings)
        """
        matter_data, has_pii, pii_findings = await self.get_matter_context(
            db, matter_id
        )

        if not matter_data:
            return "", False, []

        scrubbed_data = self.scrub_matter_context(matter_data, privacy_mode)
        context_str = self.format_matter_context(scrubbed_data, scrubbed=privacy_mode)

        return context_str, has_pii, pii_findings
