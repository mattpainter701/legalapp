"""LLM-driven retrieval planner that converts user questions into structured cloud search plans."""

import json
import logging
from typing import TypedDict

from app.services.llm import LLMService

logger = logging.getLogger(__name__)

# Provider-to-source mapping
PROVIDER_SOURCES = {
    "google": frozenset({"gmail", "drive"}),
    "microsoft": frozenset({"outlook", "onedrive", "sharepoint"}),
}

PLANNER_SYSTEM_PROMPT = """You are a legal search planner. Your job is to convert a user's question into a structured cloud search plan.

TENANT: {tenant_name}
ACTIVE PROVIDERS: {providers}
MATTER CONTEXT (if any): {matter_context}

USER QUESTION: {user_question}

Decide:
1. Does this question require searching the firm's cloud documents/emails? If it's a general legal question, return NO_SEARCH.
2. Which sources to search (gmail, drive, outlook, onedrive, sharepoint)
3. Keywords and entities to search for
4. Date range (if the question mentions time periods like "last quarter", "this year")

Output ONLY this JSON (no markdown, no surrounding text):
{{
  "should_search": true,
  "sources": ["gmail", "drive"],
  "keywords": ["renewal", "SOW", "Acme"],
  "date_after": "2026-01-01",
  "max_hits": 10,
  "people": ["acme.com", "john@firm.com"]
}}

Or if cloud search is not needed:
{{
  "should_search": false
}}"""


class RetrievalPlanDict(TypedDict, total=False):
    """Structured search plan produced by the planner."""

    should_search: bool
    sources: list[str]
    keywords: list[str]
    date_after: str | None
    max_hits: int
    people: list[str]


class RetrievalPlanner:
    """Converts user questions into structured cloud search plans using an LLM.

    The planner determines whether cloud search is needed, which sources to
    query, what keywords to use, and any temporal/person filters to apply.
    """

    def __init__(self, llm_service: LLMService):
        self.llm = llm_service

    async def plan(
        self,
        user_question: str,
        tenant_name: str = "Legal",
        matter_context: str | None = None,
        active_providers: list[str] | None = None,
    ) -> RetrievalPlanDict | None:
        """Generate a search plan from a user question.

        Args:
            user_question: The user's natural-language question.
            tenant_name: Firm/tenant name for context.
            matter_context: Optional matter-specific context (e.g. case name).
            active_providers: Enabled cloud providers — ``["google"]``,
                ``["microsoft"]``, ``["google", "microsoft"]``, or ``None``
                for all.

        Returns:
            A ``RetrievalPlanDict`` with search parameters, or ``None`` if
            no cloud search is needed or parsing fails.
        """
        if active_providers is None:
            active_providers = ["google", "microsoft"]

        allowed_sources: set[str] = set()
        for provider in active_providers:
            allowed_sources |= PROVIDER_SOURCES.get(provider, set())

        providers_str = ", ".join(active_providers)

        system_prompt = PLANNER_SYSTEM_PROMPT.format(
            tenant_name=tenant_name,
            providers=providers_str,
            matter_context=matter_context or "None",
            user_question=user_question,
        )

        response_text, _, _ = await self.llm.complete(
            messages=[{"role": "user", "content": user_question}],
            tenant_name=tenant_name,
            context=system_prompt,
        )

        plan = self._parse_response(response_text)
        if plan is None or not plan.get("should_search"):
            return None

        # Validate and constrain sources to active providers
        raw_sources = plan.get("sources", [])
        plan["sources"] = [s for s in raw_sources if s in allowed_sources]
        plan.setdefault("max_hits", 10)

        if not plan["sources"]:
            logger.info(
                "Planner returned sources outside active providers; "
                "dropping plan for: %s",
                user_question[:80],
            )
            return None

        return plan

    def _parse_response(self, raw: str) -> RetrievalPlanDict | None:
        """Extract the JSON payload from an LLM response.

        Handles markdown code fences, leading/trailing whitespace, and
        common formatting variations.
        """
        text = raw.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            # Remove opening fence (possibly with language tag)
            first_newline = text.find("\n")
            if first_newline != -1:
                text = text[first_newline:].strip()
            # Remove closing fence
            if text.endswith("```"):
                text = text[:-3].strip()

        # Try to extract a JSON object
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start == -1 or brace_end == -1:
            logger.warning("No JSON object found in planner response")
            return None

        json_str = text[brace_start : brace_end + 1]

        try:
            plan = json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse planner JSON: %s", exc)
            return None

        if not isinstance(plan, dict):
            logger.warning("Planner response is not a dict")
            return None

        return plan
