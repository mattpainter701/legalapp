"""Plugin skill executor — routes requests to appropriate prompt + calls LLM."""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.plugin import PracticeProfile
from app.services.llm import LLMService
from app.services.llm_routing import fallback_route, resolve_llm_route
from app.services.plugins.prompts import (
    WORK_PRODUCT_HEADER,
    UNIVERSAL_GUARDRAILS,
    ALL_DEFAULT_PROMPTS,
    COLD_START_INTERVIEW_PROMPT,
    PLUGIN_SPECIFIC_QUESTIONS,
)
from app.services.plugins.prompt_resolver import PromptResolver
from app.utils.guardrails import apply_guardrails

# Full mapping of (plugin, skill) -> prompt template
# Used for resolver-based lookup; kept for backwards compatibility and introspection.
SKILL_PROMPT_MAP: dict[str, dict[str, str]] = {}
for (plugin, skill), prompt in ALL_DEFAULT_PROMPTS.items():
    SKILL_PROMPT_MAP.setdefault(plugin, {})[skill] = prompt


class PluginExecutor:
    def __init__(self, llm_service: LLMService, resolver: PromptResolver | None = None):
        self.llm = llm_service
        self.resolver = resolver

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
        self,
        plugin: str,
        skill: str,
        profile: str,
        context: dict,
        prompt_template: str | None = None,
    ) -> str:
        """Build the full system prompt for a skill execution.

        Args:
            prompt_template: Pre-resolved prompt string (from PromptResolver).
                            If None, uses the old SKILL_PROMPT_MAP lookup or fallback.
        """
        if skill == "cold-start-interview":
            current_step = context.get("setup_step", 1)
            plugin_questions = PLUGIN_SPECIFIC_QUESTIONS.get(plugin, "")
            prompt = COLD_START_INTERVIEW_PROMPT.format(
                plugin_name=plugin,
                current_step=current_step,
                plugin_specific_questions=plugin_questions,
            )
            return prompt

        # Fall back to SKILL_PROMPT_MAP if no resolver was used
        if prompt_template is None:
            plugin_prompts = SKILL_PROMPT_MAP.get(plugin, {})
            prompt_template = plugin_prompts.get(skill)

        if prompt_template is None:
            # Generic fallback with guardrails
            return (
                f"{WORK_PRODUCT_HEADER}\n\n"
                f"You are a legal assistant. {UNIVERSAL_GUARDRAILS}\n\n"
                f"PRACTICE PROFILE:\n"
                f"{profile or 'No practice profile configured.'}\n\n"
                f"Answer the user's legal question carefully, citing sources with "
                f"appropriate tags ([settled], [verify], [model knowledge]).\n"
                f"Every output requires attorney review. This is not legal advice."
            )

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

        try:
            return prompt_template.format(**format_kwargs)
        except KeyError as e:
            # Surface template variable errors clearly instead of a 500
            return (
                f"{WORK_PRODUCT_HEADER}\n\n"
                f"ERROR: Prompt template contains unrecognized template variable: {e}\n\n"
                f"Please check the prompt override for '{plugin}:{skill}' and ensure all "
                f"template variables match the supported set: "
                f"{', '.join(sorted(format_kwargs.keys()))}."
            )

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
                "model_used": fallback_route(False).model,
            }

        # Resolve prompt: tenant override -> code default -> generic fallback
        resolved_prompt = None
        if self.resolver and skill != "cold-start-interview":
            resolved_prompt = await self.resolver.get_prompt(
                db, tenant_id, plugin, skill
            )

        system_prompt = self.build_system_prompt(
            plugin, skill, profile, context, prompt_template=resolved_prompt
        )

        route = await resolve_llm_route(
            db,
            tenant_id,
            use_premium=use_premium,
        )

        response_text, tokens_in, tokens_out = await self.llm.complete(
            messages=[{"role": "user", "content": input_text}],
            tenant_name=context.get("tenant_name", "Legal"),
            context=system_prompt,
            use_premium=use_premium,
            provider=route.provider,
            model=route.model,
        )

        cleaned_response, needs_retry, _ = apply_guardrails(response_text)

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
            "model_used": route.model,
        }
