import logging
import uuid
from typing import AsyncGenerator, List, Tuple

from openai import APIConnectionError, APIError, AsyncOpenAI

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


def _clean_llm_error(exc: Exception) -> str:
    """Extract a clean error message from an LLM gateway exception."""
    msg = str(exc)
    if "<!DOCTYPE" in msg or "<html" in msg.lower():
        msg = msg.split("<!DOCTYPE")[0].split("<html")[0].strip()
        if not msg:
            msg = "LLM gateway returned an unexpected response"
    return msg[:300]


def _llm_error_msg(exc: Exception) -> str:
    return f"LiteLLM Gateway API error: {_clean_llm_error(exc)}"


SYSTEM_PROMPT_TEMPLATE = """You are a senior paralegal and legal analyst working for {tenant_name}. You support attorneys with research, drafting, and analysis. You are precise, discreet, and bound by professional ethics.

CAPABILITIES:
- You draw on the firm's document library (provided as CONTEXT below), uploaded attachments, and your own legal reasoning.
- You synthesise information from all available sources but clearly distinguish what comes from the firm's materials vs. your own knowledge.
- You leverage user history and preferences (provided in USER CONTEXT below) to tailor your responses.

RULES:
- **Accuracy first.** If you are uncertain about a point, say so rather than guessing. Never fabricate case names, citations, or statutes.
- **Cite when you can.** When the CONTEXT contains supporting authority, cite it with case name and legal citation. When you reason from general principles without a specific source, flag it.
- **Confidence tags.** Tag substantive claims: [settled] for black-letter law, [verify] for points an attorney should confirm, [model knowledge] for general legal reasoning not tied to a provided source.
- **No speculation.** Do not predict what a court will do. Outline the legal framework and let the attorney assess.
- **Scope.** You answer legal questions, analyse documents, draft correspondence, and summarise authority - but you do not provide final legal advice. Always remind the attorney to review.
- **Privacy.** You never share information about {tenant_name} or its clients with anyone outside this conversation. All data stays within the firm.
- **Identity.** You are a legal assistant, not an AI. Do not mention what model or system powers you. If asked, say you are a legal research tool built for {tenant_name}.
- **Greeting.** Address the user by their name ({user_name}) when you know it. Never use generic titles like "counsel", "counsellor", or "attorney" unless the user has introduced themselves that way. If you don't know their name, use a neutral greeting without titles.
- End every substantive response with: "\\n\\n---\\n*Prepared for {tenant_name}. Attorney review recommended before reliance.*"

USER CONTEXT (history of interactions, preferences, and patterns):
{memory_context}

FIRM CONTEXT (firm documents and relevant authority - may be empty):
{context}
"""


def _build_system_message(system_prompt: str) -> dict:
    """Build the system message dict, adding cache-control hints for long prompts.

    For providers that support prompt caching (Anthropic, Gemini via LiteLLM),
    the cache_control hint signals that this content should be cached. LiteLLM
    drops the key for providers that don't support it (drop_params: true in
    litellm_config.yaml), so this is a no-op for unsupported providers.
    """
    msg: dict = {"role": "system", "content": system_prompt}
    if len(system_prompt) > 500:
        msg["cache_control"] = {"type": "ephemeral"}
    return msg


class LLMService:
    """LiteLLM gateway client.

    The backend intentionally does not call model providers directly. Provider
    selection, health checks, retries, and fallback chains live in LiteLLM.
    """

    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.LITELLM_API_KEY or "not-needed",
            base_url=settings.LITELLM_BASE_URL,
        )

    def _default_model(self, use_premium: bool) -> str:
        return (
            settings.LITELLM_PREMIUM_MODEL
            if use_premium
            else settings.LITELLM_STANDARD_MODEL
        )

    def _build_system_prompt(
        self,
        *,
        tenant_name: str,
        context: str,
        memory_context: str | None,
        user_name: str,
    ) -> str:
        return SYSTEM_PROMPT_TEMPLATE.format(
            tenant_name=tenant_name,
            memory_context=memory_context or "No user memory available.",
            context=context,
            user_name=user_name or "the attorney",
        )

    async def complete(
        self,
        messages: List[dict],
        tenant_name: str,
        context: str,
        memory_context: str = "",
        use_premium: bool = False,
        provider: str = "litellm",
        model: str | None = None,
        user_name: str = "",
        response_format: dict | None = None,
    ) -> Tuple[str, int, int]:
        """Generate a completion through LiteLLM.

        ``provider`` is retained only for old call-site compatibility and is
        ignored; ``model`` must be a LiteLLM model alias.
        ``response_format`` accepts e.g. ``{"type": "json_object"}`` for
        structured output — LiteLLM drops it silently for models that don't
        support it (drop_params: true in gateway config).
        """
        system_prompt = self._build_system_prompt(
            tenant_name=tenant_name,
            context=context,
            memory_context=memory_context,
            user_name=user_name,
        )
        gateway_model = model or self._default_model(use_premium)
        request_id = str(uuid.uuid4())
        logger.debug("LLM complete request_id=%s model=%s", request_id, gateway_model)
        all_messages = [_build_system_message(system_prompt)] + messages

        create_kwargs: dict = dict(
            model=gateway_model,
            messages=all_messages,
            temperature=0.1,
            max_tokens=4096,
            extra_headers={"x-request-id": request_id},
        )
        if response_format:
            create_kwargs["response_format"] = response_format

        try:
            response = await self.client.chat.completions.create(**create_kwargs)
            response_text = response.choices[0].message.content or ""
            tokens_in = response.usage.prompt_tokens if response.usage else 0
            tokens_out = response.usage.completion_tokens if response.usage else 0
            return response_text, tokens_in, tokens_out
        except (APIError, APIConnectionError) as e:
            logger.error("LiteLLM Gateway API error: %s", _clean_llm_error(e))
            raise RuntimeError(_llm_error_msg(e)) from e

    async def stream_complete(
        self,
        messages: List[dict],
        tenant_name: str,
        context: str,
        use_premium: bool = False,
        provider: str = "litellm",
        memory_context: str | None = None,
        model: str | None = None,
        user_name: str = "",
    ) -> AsyncGenerator[str, None]:
        """Stream a completion through LiteLLM."""
        system_prompt = self._build_system_prompt(
            tenant_name=tenant_name,
            context=context,
            memory_context=memory_context,
            user_name=user_name,
        )
        gateway_model = model or self._default_model(use_premium)
        request_id = str(uuid.uuid4())
        logger.debug(
            "LLM stream_complete request_id=%s model=%s", request_id, gateway_model
        )
        all_messages = [_build_system_message(system_prompt)] + messages

        try:
            stream = await self.client.chat.completions.create(
                model=gateway_model,
                messages=all_messages,
                temperature=0.1,
                max_tokens=4096,
                stream=True,
                extra_headers={"x-request-id": request_id},
            )
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except (APIError, APIConnectionError) as e:
            logger.error("LiteLLM Gateway streaming error: %s", _clean_llm_error(e))
            raise RuntimeError(_llm_error_msg(e)) from e
