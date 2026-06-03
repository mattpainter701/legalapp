"""Plugin skill executor — routes requests to appropriate prompt + calls LLM."""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.plugin import PracticeProfile
from app.services.llm import LLMService
from app.services.plugins.prompts import (
    WORK_PRODUCT_HEADER,
    UNIVERSAL_GUARDRAILS,
    COMMERCIAL_VENDOR_REVIEW_PROMPT,
    COMMERCIAL_NDA_REVIEW_PROMPT,
    COMMERCIAL_SAAS_REVIEW_PROMPT,
    LITIGATION_MATTER_INTAKE_PROMPT,
    LITIGATION_DEMAND_DRAFT_PROMPT,
    LITIGATION_CLAIM_CHART_PROMPT,
    PRIVACY_DPA_REVIEW_PROMPT,
    PRIVACY_DSAR_PROMPT,
    PRIVACY_PIA_PROMPT,
    EMPLOYMENT_TERMINATION_REVIEW_PROMPT,
    EMPLOYMENT_CLASSIFICATION_PROMPT,
    PRODUCT_LAUNCH_REVIEW_PROMPT,
    IP_TRADEMARK_CLEARANCE_PROMPT,
    IP_FTO_ANALYSIS_PROMPT,
    AI_GOV_USE_CASE_TRIAGE_PROMPT,
    REGULATORY_GAP_ANALYSIS_PROMPT,
    COLD_START_INTERVIEW_PROMPT,
    PLUGIN_SPECIFIC_QUESTIONS,
)
from app.utils.guardrails import apply_guardrails

SKILL_PROMPT_MAP = {
    "commercial-legal": {
        "vendor-agreement-review": COMMERCIAL_VENDOR_REVIEW_PROMPT,
        "nda-review": COMMERCIAL_NDA_REVIEW_PROMPT,
        "saas-msa-review": COMMERCIAL_SAAS_REVIEW_PROMPT,
    },
    "litigation-legal": {
        "matter-intake": LITIGATION_MATTER_INTAKE_PROMPT,
        "demand-draft": LITIGATION_DEMAND_DRAFT_PROMPT,
        "claim-chart": LITIGATION_CLAIM_CHART_PROMPT,
    },
    "privacy-legal": {
        "dpa-review": PRIVACY_DPA_REVIEW_PROMPT,
        "dsar-response": PRIVACY_DSAR_PROMPT,
        "pia-generation": PRIVACY_PIA_PROMPT,
    },
    "employment-legal": {
        "termination-review": EMPLOYMENT_TERMINATION_REVIEW_PROMPT,
        "classification-analysis": EMPLOYMENT_CLASSIFICATION_PROMPT,
    },
    "product-legal": {
        "launch-review": PRODUCT_LAUNCH_REVIEW_PROMPT,
    },
    "ip-legal": {
        "trademark-clearance": IP_TRADEMARK_CLEARANCE_PROMPT,
        "fto-analysis": IP_FTO_ANALYSIS_PROMPT,
    },
    "ai-governance-legal": {
        "use-case-triage": AI_GOV_USE_CASE_TRIAGE_PROMPT,
    },
    "regulatory-legal": {
        "reg-gap-analysis": REGULATORY_GAP_ANALYSIS_PROMPT,
    },
}


class PluginExecutor:
    def __init__(self, llm_service: LLMService):
        self.llm = llm_service

    async def get_practice_profile(
        self, db: AsyncSession, tenant_id: str, user_id: str, plugin_name: str
    ) -> str:
        """Retrieve practice profile content for a plugin. Returns empty string if not set up."""
        result = await db.execute(
            select(PracticeProfile).where(
                PracticeProfile.tenant_id == tenant_id,
                PracticeProfile.plugin_name == plugin_name,
            )
        )
        profile = result.scalar_one_or_none()
        if profile is None or not profile.profile_content:
            return ""
        return profile.profile_content

    def check_hard_gates(
        self, plugin: str, skill: str, profile: str, context: dict
    ) -> list[str]:
        """Check for hard gates that should block execution. Returns list of gate messages."""
        gates = []

        if not profile:
            gates.append(
                f"GATE: No practice profile found for {plugin}. "
                f"Run /{plugin}:cold-start-interview to set up your practice profile before using this skill."
            )
        elif "[PLACEHOLDER]" in profile:
            gates.append(
                "GATE: Practice profile contains unfilled [PLACEHOLDER] entries. "
                "Complete your practice profile before running skills."
            )

        if plugin == "litigation-legal" and skill in ("matter-intake",):
            conflicts = context.get("conflicts_status", "not-run")
            if conflicts == "not-run":
                gates.append(
                    "CONFLICTS GATE: Conflicts status is 'not-run'. Cannot proceed with matter intake. "
                    "Options: (1) Run conflicts check and return cleared status, "
                    "(2) Mark as pending with owner + due date, "
                    "(3) Bypass with documented rationale (permanent, visible in all portfolio briefings)."
                )

        return gates

    def build_system_prompt(
        self, plugin: str, skill: str, profile: str, context: dict
    ) -> str:
        """Build the full system prompt for a skill execution."""
        if skill == "cold-start-interview":
            current_step = context.get("setup_step", 1)
            plugin_questions = PLUGIN_SPECIFIC_QUESTIONS.get(plugin, "")
            prompt = COLD_START_INTERVIEW_PROMPT.format(
                plugin_name=plugin,
                current_step=current_step,
                plugin_specific_questions=plugin_questions,
            )
            return prompt

        plugin_prompts = SKILL_PROMPT_MAP.get(plugin, {})
        prompt_template = plugin_prompts.get(skill)

        if not prompt_template:
            # Generic fallback with guardrails
            prompt = f"""{WORK_PRODUCT_HEADER}

You are a legal assistant. {UNIVERSAL_GUARDRAILS}

PRACTICE PROFILE:
{profile or "No practice profile configured."}

Answer the user's legal question carefully, citing sources with appropriate tags ([settled], [verify], [model knowledge]).
Every output requires attorney review. This is not legal advice.
"""
            return prompt

        # Build safe context substitutions — only pass keys that the template uses
        format_kwargs = {
            "work_product_header": WORK_PRODUCT_HEADER,
            "universal_guardrails": UNIVERSAL_GUARDRAILS,
            "practice_profile": profile
            or "No practice profile configured. Proceed with general best practices.",
            "matter_context": context.get("matter_context", ""),
            "dsar_context": context.get("dsar_context", ""),
            "jurisdiction": context.get("jurisdiction", "Jurisdiction not specified"),
            "chart_mode": context.get("chart_mode", "infringement"),
        }

        prompt = prompt_template.format(**format_kwargs)
        return prompt

    async def execute(
        self,
        db: AsyncSession,
        plugin: str,
        skill: str,
        input_text: str,
        tenant_id: str,
        user_id: str,
        context: dict,
        use_premium: bool = False,
    ) -> dict:
        """Execute a plugin skill and return structured response."""
        from app.config import get_settings

        settings = get_settings()

        profile = await self.get_practice_profile(db, tenant_id, user_id, plugin)
        gates = self.check_hard_gates(plugin, skill, profile, context)

        if gates and skill != "cold-start-interview":
            return {
                "skill": skill,
                "plugin": plugin,
                "memo": "\n".join(gates),
                "findings": [],
                "gates_triggered": gates,
                "flags": [],
                "requires_attorney_review": True,
                "tokens_used": 0,
                "model_used": settings.PRIMARY_LLM,
            }

        system_prompt = self.build_system_prompt(plugin, skill, profile, context)

        response_text, tokens_in, tokens_out = await self.llm.complete(
            messages=[{"role": "user", "content": input_text}],
            tenant_name=context.get("tenant_name", "Legal"),
            context=system_prompt,
            use_premium=use_premium,
        )

        cleaned_response, needs_retry = apply_guardrails(response_text)

        model_used = settings.PREMIUM_LLM if use_premium else settings.PRIMARY_LLM

        return {
            "skill": skill,
            "plugin": plugin,
            "memo": cleaned_response,
            "findings": [],
            "gates_triggered": [],
            "flags": [],
            "requires_attorney_review": True,
            "tokens_used": tokens_in + tokens_out,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "model_used": model_used,
        }
