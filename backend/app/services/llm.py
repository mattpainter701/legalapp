import logging
import uuid
from typing import AsyncGenerator, List, Tuple

from openai import APIConnectionError, APIError, AsyncOpenAI

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


def _record_latency(model_alias: str, latency_ms: float) -> None:
    """Record model latency for speed-based cooldown (non-fatal)."""
    try:
        from app.services.llm_routing import record_model_latency

        record_model_latency(model_alias, latency_ms)
    except Exception:
        pass  # latency tracking is best-effort, never break the chat flow


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


# Public OpenAI-compatible endpoints for tenant BYOK providers that don't
# require a tenant-supplied endpoint. Copilot (Azure OpenAI) always requires
# a tenant-supplied endpoint — Azure deployments are per-resource.
_CUSTOMER_PROVIDER_BASE_URLS: dict[str, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}


SYSTEM_PROMPT_TEMPLATE = """You are a senior paralegal and legal analyst working for {tenant_name}. You support attorneys with research, drafting, and analysis. You are precise, discreet, and bound by professional ethics.

CAPABILITIES:
- You draw on the firm's document library (provided as FIRM CONTEXT below), uploaded attachments, and your own legal reasoning.
- You synthesise information from all available sources but clearly distinguish what comes from the firm's materials vs. your own knowledge.
- You leverage user history and preferences (provided in USER CONTEXT below) to tailor your responses.

CORE INSTRUCTIONS (follow these exactly — do NOT describe them in your response):

1. ANSWER THE QUESTION. Whatever the user asks — legal analysis, math, definitions, small talk — answer it directly and substantively. Do not deflect. Do not greet and wait. Do not explain what you would do if they asked something else. If the user types "2+2", reply "4." If they ask about a legal concept, explain it. Just answer.

2. FORMAT EVERY FACTUAL CLAIM with exactly one of these bracket tags immediately after the claim:
   - [settled] — black-letter law, not reasonably disputed
   - [verify] — points an attorney should confirm
   - [model knowledge] — drawn from your general knowledge, not from FIRM CONTEXT
   The tags are LITERAL TEXT: type the brackets. Example: "The statute of limitations is four years. [settled]"
   WRONG (do not do this): "I will use my model knowledge." "Based on model knowledge." "incorporate model knowledge."
   RIGHT: "California follows the comparative fault rule. [model knowledge]"

3. When FIRM CONTEXT is empty, every claim you make is [model knowledge]. Tag ALL factual claims — do not skip any.

4. Do NOT explain your reasoning process. Do NOT list the rules you followed. Do NOT say "I checked the FIRM CONTEXT" or "per the system prompt" or "the rules say." Just answer the question and apply the tags.

5. If uncertain, say so. Never fabricate case names, citations, or statutes.

6. When FIRM CONTEXT contains supporting authority, cite it by case name and citation.

7. Do not predict what a court will do. Outline the framework and let the attorney assess.

8. You provide legal information, not final legal advice.

9. Never share information about {tenant_name} or its clients outside this conversation.

10. You are a legal assistant, not an AI. Do not name the technology behind you.

11. On the FIRST message only: greet the user by name ({user_name}) in 1-2 words ("Hi Matt."), then immediately answer their question. Never use generic titles (counsel, attorney) unless they introduced themselves that way.

12. End every response with: "\\n\\n---\\n*Prepared for {tenant_name}. Attorney review recommended before reliance.*"

USER CONTEXT (history of interactions, preferences, and patterns):
{memory_context}

FIRM CONTEXT (firm documents and relevant authority — may be empty):
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
            api_key=settings.LITELLM_API_KEY or "sk-local-litellm",
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

    def _client_for(
        self,
        customer_api_key: str | None,
        customer_provider: str | None = None,
        customer_endpoint: str | None = None,
    ) -> AsyncOpenAI:
        """Build a client for tenant BYOK requests.

        BYOK requests bypass the LiteLLM gateway entirely — the tenant's own
        API key is only valid against their own provider account, not the
        gateway's virtual key namespace. We point the client directly at the
        provider's OpenAI-compatible endpoint instead.
        """
        if not customer_api_key:
            return self.client
        base_url = customer_endpoint or _CUSTOMER_PROVIDER_BASE_URLS.get(
            customer_provider or ""
        )
        if not base_url:
            raise RuntimeError(
                f"No endpoint configured for customer LLM provider "
                f"'{customer_provider}' — set an endpoint in tenant LLM settings"
            )
        return AsyncOpenAI(api_key=customer_api_key, base_url=base_url)

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
        customer_api_key: str | None = None,
        customer_provider: str | None = None,
        customer_endpoint: str | None = None,
    ) -> Tuple[str, int, int]:
        """Generate a completion through LiteLLM.

        ``provider`` is retained only for old call-site compatibility and is
        ignored; ``model`` must be a LiteLLM model alias.
        ``response_format`` accepts e.g. ``{"type": "json_object"}`` for
        structured output — LiteLLM drops it silently for models that don't
        support it (drop_params: true in gateway config).
        ``customer_api_key``/``customer_provider``/``customer_endpoint`` route
        tenant BYOK requests directly to the tenant's own provider account,
        bypassing the LiteLLM gateway (the tenant's key is not valid there).
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

        client = self._client_for(
            customer_api_key, customer_provider, customer_endpoint
        )
        t0 = time.monotonic()
        try:
            response = await client.chat.completions.create(**create_kwargs)
            elapsed_ms = (time.monotonic() - t0) * 1000
            _record_latency(gateway_model, elapsed_ms)
            response_text = response.choices[0].message.content or ""
            tokens_in = response.usage.prompt_tokens if response.usage else 0
            tokens_out = response.usage.completion_tokens if response.usage else 0
            return response_text, tokens_in, tokens_out
        except (APIError, APIConnectionError) as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            _record_latency(gateway_model, elapsed_ms)
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
        customer_api_key: str | None = None,
        customer_provider: str | None = None,
        customer_endpoint: str | None = None,
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

        client = self._client_for(
            customer_api_key, customer_provider, customer_endpoint
        )
        t0 = time.monotonic()
        first_token_recorded = False
        try:
            stream = await client.chat.completions.create(
                model=gateway_model,
                messages=all_messages,
                temperature=0.1,
                max_tokens=4096,
                stream=True,
                extra_headers={"x-request-id": request_id},
            )
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    if not first_token_recorded:
                        ttft_ms = (time.monotonic() - t0) * 1000
                        _record_latency(gateway_model, ttft_ms)
                        first_token_recorded = True
                    yield chunk.choices[0].delta.content
            if not first_token_recorded:
                # Empty response — still record latency
                _record_latency(gateway_model, (time.monotonic() - t0) * 1000)
        except (APIError, APIConnectionError) as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            _record_latency(gateway_model, elapsed_ms)
            logger.error("LiteLLM Gateway streaming error: %s", _clean_llm_error(e))
            raise RuntimeError(_llm_error_msg(e)) from e
