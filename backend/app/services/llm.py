import logging
import time
import uuid
from typing import AsyncGenerator, List, Tuple

from openai import APIConnectionError, APIError, AsyncOpenAI

from app.config import get_settings
from app.services.byok_security import normalize_customer_llm_endpoint
from app.services.gateway_privacy import gateway_metadata as sanitized_gateway_metadata

settings = get_settings()
logger = logging.getLogger(__name__)

STANDARD_GATEWAY_FALLBACKS = (
    "clarity-standard-zen-nemotron",
    "clarity-standard-deepseek-flash-free",
)


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
SYSTEM_PROMPT_TEMPLATE = """You are a senior paralegal and legal analyst working for {tenant_name}. You support attorneys with research, drafting, and analysis. You are precise, discreet, and bound by professional ethics.

CAPABILITIES:
- You draw on source materials below, uploaded attachments, public legal authority, firm documents, and your own legal reasoning.
- You synthesise information from all available sources but clearly distinguish cited/retrieved materials from your own knowledge.
- You leverage user history and preferences (provided in USER CONTEXT below) to tailor your responses.

CORE INSTRUCTIONS (follow these exactly — do NOT describe them in your response):

1. ANSWER THE QUESTION. Whatever the user asks — legal analysis, math, definitions, small talk — answer it directly and substantively. Do not deflect. Do not greet and wait. Do not explain what you would do if they asked something else. If the user types "2+2", reply "4." If they ask about a legal concept, explain it. Just answer.

2. FORMAT EVERY FACTUAL CLAIM with exactly one of these bracket tags immediately after the claim:
   - [settled] — supported by an exact retrieved source as described below
   - [verify] — points an attorney should confirm
   - [model knowledge] — drawn from your general knowledge, not from the source materials
   The tags are LITERAL TEXT: type the brackets. Example: "The statute of limitations may be four years. [verify]"
   WRONG (do not do this): "I will use my model knowledge." "Based on model knowledge." "incorporate model knowledge."
   RIGHT: "California follows the comparative fault rule. [model knowledge]"

3. When SOURCE MATERIALS is empty, lead with this exact concise note once: "**Source note:** This response uses general legal knowledge, not retrieved authority. Verify jurisdiction-specific law and citations before relying on it." Do not repeat [model knowledge] after every factual claim. When SOURCE MATERIALS is present, use [model knowledge] only for individual claims that are not drawn from those materials.

4. Do NOT explain your reasoning process. Do NOT list the rules you followed. Do NOT say "I checked the source materials" or "per the system prompt" or "the rules say." Never write the phrase "based on the provided source materials" or any internal source bucket label. Just answer the question and apply the tags.

5. If uncertain, say so. Never fabricate case names, citations, or statutes.

6. SOURCE VERIFICATION: Use [settled] only when the same claim includes (a) the exact
   [source: <source_id>] tag printed in SOURCE MATERIALS and (b) a verbatim quote of
   at least 20 characters that appears in that source's excerpt. Cite legal authority
   by case name and citation as well. If either the exact source tag or matching quote
   is absent, use [verify]. Never invent or alter a source id.

7. SOURCE ATTRIBUTION: Prefer retrieved sources over general knowledge. Every claim
    drawn from SOURCE MATERIALS must include the exact [source: <source_id>] marker
    printed with that source. If the source has a URL, make its case name, citation,
    statute, rule, or source title a Markdown hyperlink to that URL. Use this format:
    "[Case name, citation](URL) [source: exact-id] [verify]". Do not use
    [model knowledge] merely because a sourced claim does not meet the stricter
    [settled] standard; use [verify] and keep its source marker.

8. Do not predict what a court will do. Outline the framework and let the attorney assess.

9. You provide legal information, not final legal advice.

10. Never share information about {tenant_name} or its clients outside this conversation.

11. You are an AI-assisted legal research tool. Do not claim human status and do not
    invent a provider or model identity. Preserve substantive AI/vendor terminology
    when it is relevant to the user's legal work.

12. On the FIRST message only: greet the user by name ({user_name}) in 1-2 words ("Hi Matt."), then immediately answer their question. Never use generic titles (counsel, attorney) unless they introduced themselves that way.

13. Append "\\n\\n---\\n*Prepared for {tenant_name}. Attorney review recommended before reliance.*" only when the response contains legal analysis, legal drafting, jurisdiction-specific legal information, case/statute discussion, or advice-like legal guidance. Do not append that footer to ordinary non-legal answers, math, greetings, product help, status updates, or factual/admin responses unrelated to legal work.

USER CONTEXT (history of interactions, preferences, and patterns):
{memory_context}

SOURCE MATERIALS (retrieved firm documents, matter context, uploaded attachments, cloud files, and legal authority — may be empty):
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

    def _gateway_candidates(
        self,
        gateway_model: str,
        *,
        use_premium: bool,
        customer_api_key: str | None,
    ) -> list[str]:
        if (
            customer_api_key
            or use_premium
            or gateway_model != settings.LITELLM_STANDARD_MODEL
        ):
            return [gateway_model]
        candidates = [gateway_model, *STANDARD_GATEWAY_FALLBACKS]
        try:
            from app.services.llm_routing import is_model_in_cooldown

            available = [
                candidate
                for candidate in candidates
                if not is_model_in_cooldown(candidate)
            ]
            # A cooldown is a speed preference, not an availability verdict.
            # If every route is cooling down, retain the configured chain.
            return available or candidates
        except Exception:
            return candidates

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
        # Revalidate here so legacy or tampered database values cannot bypass
        # the persistence-time SSRF boundary.
        base_url = normalize_customer_llm_endpoint(
            customer_provider or "", customer_endpoint
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
        gateway_metadata: dict | None = None,
        system_prompt_override: str | None = None,
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
        system_prompt = system_prompt_override or self._build_system_prompt(
            tenant_name=tenant_name,
            context=context,
            memory_context=memory_context,
            user_name=user_name,
        )
        gateway_model = model or self._default_model(use_premium)
        all_messages = [_build_system_message(system_prompt)] + messages

        client = self._client_for(
            customer_api_key, customer_provider, customer_endpoint
        )
        candidates = self._gateway_candidates(
            gateway_model,
            use_premium=use_premium,
            customer_api_key=customer_api_key,
        )
        last_error: APIError | APIConnectionError | None = None
        for candidate in candidates:
            request_id = str(uuid.uuid4())
            logger.debug("LLM complete request_id=%s model=%s", request_id, candidate)
            create_kwargs: dict = dict(
                model=candidate,
                messages=all_messages,
                temperature=0.1,
                max_tokens=4096,
                extra_headers={"x-request-id": request_id},
            )
            metadata = sanitized_gateway_metadata(**(gateway_metadata or {}))
            if metadata and not customer_api_key:
                create_kwargs["extra_body"] = {"metadata": metadata}
            if response_format:
                create_kwargs["response_format"] = response_format

            t0 = time.monotonic()
            try:
                response = await client.chat.completions.create(**create_kwargs)
                elapsed_ms = (time.monotonic() - t0) * 1000
                _record_latency(candidate, elapsed_ms)
                response_text = response.choices[0].message.content or ""
                tokens_in = response.usage.prompt_tokens if response.usage else 0
                tokens_out = response.usage.completion_tokens if response.usage else 0
                return response_text, tokens_in, tokens_out
            except (APIError, APIConnectionError) as e:
                elapsed_ms = (time.monotonic() - t0) * 1000
                _record_latency(candidate, elapsed_ms)
                last_error = e
                if candidate != candidates[-1]:
                    logger.warning(
                        "LiteLLM model %s failed before completion, trying fallback: %s",
                        candidate,
                        _clean_llm_error(e),
                    )
                    continue
                logger.error("LiteLLM Gateway API error: %s", _clean_llm_error(e))
                raise RuntimeError(_llm_error_msg(e)) from e
        if last_error:
            raise RuntimeError(_llm_error_msg(last_error)) from last_error
        raise RuntimeError("LiteLLM Gateway API error: no model candidates configured")

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
        gateway_metadata: dict | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a completion through LiteLLM."""
        system_prompt = self._build_system_prompt(
            tenant_name=tenant_name,
            context=context,
            memory_context=memory_context,
            user_name=user_name,
        )
        gateway_model = model or self._default_model(use_premium)
        all_messages = [_build_system_message(system_prompt)] + messages

        client = self._client_for(
            customer_api_key, customer_provider, customer_endpoint
        )
        candidates = self._gateway_candidates(
            gateway_model,
            use_premium=use_premium,
            customer_api_key=customer_api_key,
        )
        for candidate in candidates:
            request_id = str(uuid.uuid4())
            logger.debug(
                "LLM stream_complete request_id=%s model=%s", request_id, candidate
            )
            t0 = time.monotonic()
            first_token_recorded = False
            try:
                create_kwargs: dict = dict(
                    model=candidate,
                    messages=all_messages,
                    temperature=0.1,
                    max_tokens=4096,
                    stream=True,
                    extra_headers={"x-request-id": request_id},
                )
                metadata = sanitized_gateway_metadata(**(gateway_metadata or {}))
                if metadata and not customer_api_key:
                    create_kwargs["extra_body"] = {"metadata": metadata}
                stream = await client.chat.completions.create(**create_kwargs)
                async for chunk in stream:
                    if chunk.choices[0].delta.content:
                        if not first_token_recorded:
                            ttft_ms = (time.monotonic() - t0) * 1000
                            _record_latency(candidate, ttft_ms)
                            first_token_recorded = True
                        yield chunk.choices[0].delta.content
                if not first_token_recorded:
                    # Empty response — still record latency
                    _record_latency(candidate, (time.monotonic() - t0) * 1000)
                return
            except (APIError, APIConnectionError) as e:
                elapsed_ms = (time.monotonic() - t0) * 1000
                _record_latency(candidate, elapsed_ms)
                if first_token_recorded or candidate == candidates[-1]:
                    logger.error(
                        "LiteLLM Gateway streaming error: %s", _clean_llm_error(e)
                    )
                    raise RuntimeError(_llm_error_msg(e)) from e
                logger.warning(
                    "LiteLLM model %s failed before first token, trying fallback: %s",
                    candidate,
                    _clean_llm_error(e),
                )
