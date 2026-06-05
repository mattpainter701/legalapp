import logging
from typing import AsyncGenerator, List, Tuple

import anthropic
from openai import APIError, APIConnectionError, AsyncOpenAI

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


def _clean_llm_error(exc: Exception) -> str:
    """Extract a clean error message from an LLM provider exception, stripping raw HTML."""
    msg = str(exc)
    if "<!DOCTYPE" in msg or "<html" in msg.lower():
        msg = msg.split("<!DOCTYPE")[0].split("<html")[0].strip()
        if not msg:
            msg = "LLM service returned an unexpected response (possible proxy/gateway error)"
    return msg[:300]


def _llm_error_msg(provider: str, exc: Exception) -> str:
    """Build a clean error message for the chat pipeline."""
    clean = _clean_llm_error(exc)
    return f"{provider} API error: {clean}"


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
- **Scope.** You answer legal questions, analyse documents, draft correspondence, and summarise authority — but you do not provide final legal advice. Always remind the attorney to review.
- **Privacy.** You never share information about {tenant_name} or its clients with anyone outside this conversation. All data stays within the firm.
- **Identity.** You are a legal assistant, not an AI. Do not mention what model or system powers you. If asked, say you are a legal research tool built for {tenant_name}.
- **Greeting.** Address the user by their name ({user_name}) when you know it. Never use generic titles like "counsel", "counsellor", or "attorney" unless the user has introduced themselves that way. If you don't know their name, use a neutral greeting without titles.
- End every substantive response with: "\\n\\n---\\n*Prepared for {tenant_name}. Attorney review recommended before reliance.*"

USER CONTEXT (history of interactions, preferences, and patterns):
{memory_context}

FIRM CONTEXT (firm documents and relevant authority — may be empty):
{context}
"""

GEMINI_SYSTEM_INSTRUCTION = """You are a legal research assistant. You help attorneys research case law and draft legal documents.

RULES:
- Answer ONLY using the provided legal sources.
- ALWAYS cite the exact case name and legal citation for every substantive statement.
- If the provided sources do not support an answer, respond with: "I could not find relevant authority in the provided materials."
- Do NOT predict case outcomes or provide legal advice.
- Use a formal, authoritative tone.
- End every response with: "\\n\\n*This is not legal advice. Please consult a qualified attorney.*"
"""


class LLMService:
    def __init__(self):
        self.deepseek_client = AsyncOpenAI(
            api_key=settings.DEEPSEEK_API_KEY or settings.OPENCODE_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
        )
        self.anthropic_client = anthropic.AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY,
        )
        self.azure_client = None
        if settings.AZURE_OPENAI_ENDPOINT and settings.AZURE_OPENAI_KEY:
            self.azure_client = AsyncOpenAI(
                api_key=settings.AZURE_OPENAI_KEY,
                base_url=f"{settings.AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/deployments/{settings.AZURE_OPENAI_DEPLOYMENT}",
                default_query={"api-version": "2024-08-01-preview"},
            )
        # OpenRouter — OpenAI-compatible, free model access
        self.openrouter_client = None
        if settings.OPENROUTER_API_KEY:
            self.openrouter_client = AsyncOpenAI(
                api_key=settings.OPENROUTER_API_KEY,
                base_url=settings.OPENROUTER_BASE_URL,
            )
        # OpenCode Zen — free-tier OpenAI-compatible endpoint
        opencode_key = settings.OPENCODE_KEY or settings.DEEPSEEK_API_KEY
        self.opencode_client = (
            AsyncOpenAI(
                api_key=opencode_key,
                base_url=settings.OPENCODE_ZEN_BASE_URL,
            )
            if opencode_key
            else None
        )
        self.litellm_client = None
        if settings.LITELLM_ENABLED or settings.LITELLM_API_KEY:
            self.litellm_client = AsyncOpenAI(
                api_key=settings.LITELLM_API_KEY or "not-needed",
                base_url=settings.LITELLM_BASE_URL,
            )

    def _build_messages(
        self,
        system: str,
        conversation_history: List[dict],
        new_message: str,
    ) -> List[dict]:
        """Build the message list for the LLM, injecting system as first message."""
        messages = []
        for msg in conversation_history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": new_message})
        return messages

    async def complete(
        self,
        messages: List[dict],
        tenant_name: str,
        context: str,
        memory_context: str = "",
        use_premium: bool = False,
        provider: str = "default",
        model: str | None = None,
        user_name: str = "",
    ) -> Tuple[str, int, int]:
        """
        Generate a completion.
        Returns (response_text, tokens_in, tokens_out).

        Provider routing:
          - "deepseek"  → DeepSeek
          - "opencode"  → OpenCode Zen (free tier)
          - "openrouter" → OpenRouter (free models)
          - "litellm"   → LiteLLM gateway
          - "gemini"    → Google Gemini
          - "azure"     → Azure OpenAI (GPT-4o)
          - "anthropic" / premium → Anthropic Claude
          - "default"   → DeepSeek (backward compat)
        """
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            tenant_name=tenant_name,
            memory_context=memory_context or "No user memory available.",
            context=context,
            user_name=user_name or "the attorney",
        )

        # Explicit provider routing (operator-assigned or user-selected)
        if provider == "gemini":
            return await self._complete_gemini(messages, system_prompt)
        if provider == "azure":
            return await self._complete_azure(messages, system_prompt)
        if provider == "openrouter":
            resolved_model = (
                model or settings.OPENROUTER_FREE_MODELS.split(",")[0].strip()
            )
            return await self._complete_openrouter(
                messages, system_prompt, resolved_model
            )
        if provider == "litellm":
            resolved_model = model or (
                settings.LITELLM_PREMIUM_MODEL
                if use_premium
                else settings.LITELLM_STANDARD_MODEL
            )
            return await self._complete_litellm(
                messages, system_prompt, resolved_model
            )
        if provider == "opencode":
            resolved_model = model or settings.PRIMARY_LLM
            return await self._complete_opencode(
                messages, system_prompt, resolved_model
            )
        if provider == "deepseek":
            resolved_model = model or settings.PRIMARY_LLM
            return await self._complete_deepseek(
                messages, system_prompt, resolved_model
            )
        if provider == "anthropic":
            resolved_model = model or settings.PREMIUM_LLM
            return await self._complete_anthropic(
                messages, system_prompt, resolved_model
            )

        # Legacy / default routing
        if use_premium:
            resolved_model = model or settings.PREMIUM_LLM
            if resolved_model.lower().startswith(("claude", "anthropic")):
                return await self._complete_anthropic(
                    messages, system_prompt, resolved_model
                )
            return await self._complete_deepseek(
                messages, system_prompt, resolved_model
            )
        else:
            return await self._complete_deepseek(
                messages, system_prompt, model or settings.PRIMARY_LLM
            )

    async def stream_complete(
        self,
        messages: List[dict],
        tenant_name: str,
        context: str,
        use_premium: bool = False,
        provider: str = "default",
        memory_context: str | None = None,
        model: str | None = None,
        user_name: str = "",
    ) -> AsyncGenerator[str, None]:
        """
        Generate a streaming completion, yielding tokens as they arrive.
        Yields chunks of text as strings.

        Provider routing:
          - "deepseek"  → DeepSeek
          - "opencode"  → OpenCode Zen (free tier)
          - "openrouter" → OpenRouter (free models)
          - "litellm"   → LiteLLM gateway
          - "gemini"    → Google Gemini (fallback to non-streaming)
          - "azure"     → Azure OpenAI (GPT-4o)
          - "anthropic" / premium → Anthropic Claude
          - "default"   → DeepSeek (backward compat)
        """
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            tenant_name=tenant_name,
            context=context,
            memory_context=memory_context or "No user memory available.",
            user_name=user_name or "the attorney",
        )

        if provider == "gemini":
            # Gemini doesn't have reliable streaming yet, fall back to non-streaming
            response_text, _, _ = await self._complete_gemini(messages, system_prompt)
            yield response_text
            return
        if provider == "azure":
            async for chunk in self._stream_azure(messages, system_prompt):
                yield chunk
            return
        if provider == "openrouter":
            resolved_model = (
                model or settings.OPENROUTER_FREE_MODELS.split(",")[0].strip()
            )
            async for chunk in self._stream_openrouter(
                messages, system_prompt, resolved_model
            ):
                yield chunk
            return
        if provider == "litellm":
            resolved_model = model or (
                settings.LITELLM_PREMIUM_MODEL
                if use_premium
                else settings.LITELLM_STANDARD_MODEL
            )
            async for chunk in self._stream_litellm(
                messages, system_prompt, resolved_model
            ):
                yield chunk
            return
        if provider == "opencode":
            resolved_model = model or settings.PRIMARY_LLM
            async for chunk in self._stream_opencode(
                messages, system_prompt, resolved_model
            ):
                yield chunk
            return
        if provider == "deepseek":
            resolved_model = model or settings.PRIMARY_LLM
            async for chunk in self._stream_deepseek(
                messages, system_prompt, resolved_model
            ):
                yield chunk
            return
        if provider == "anthropic":
            resolved_model = model or settings.PREMIUM_LLM
            async for chunk in self._stream_anthropic(
                messages, system_prompt, resolved_model
            ):
                yield chunk
            return

        # Legacy / default routing
        if use_premium:
            resolved_model = model or settings.PREMIUM_LLM
            if resolved_model.lower().startswith(("claude", "anthropic")):
                async for chunk in self._stream_anthropic(
                    messages, system_prompt, resolved_model
                ):
                    yield chunk
            else:
                async for chunk in self._stream_deepseek(
                    messages, system_prompt, resolved_model
                ):
                    yield chunk
        else:
            async for chunk in self._stream_deepseek(
                messages, system_prompt, model or settings.PRIMARY_LLM
            ):
                yield chunk

    async def _complete_deepseek(
        self,
        messages: List[dict],
        system_prompt: str,
        model: str = None,
    ) -> Tuple[str, int, int]:
        """Call DeepSeek via OpenAI-compatible endpoint."""
        all_messages = [{"role": "system", "content": system_prompt}] + messages

        try:
            response = await self.deepseek_client.chat.completions.create(
                model=model or settings.PRIMARY_LLM,
                messages=all_messages,
                temperature=0.1,
                max_tokens=4096,
            )

            response_text = response.choices[0].message.content or ""
            tokens_in = response.usage.prompt_tokens if response.usage else 0
            tokens_out = response.usage.completion_tokens if response.usage else 0

            return response_text, tokens_in, tokens_out
        except (APIError, APIConnectionError) as e:
            logger.error(f"DeepSeek API error: {_clean_llm_error(e)}")
            raise RuntimeError(_llm_error_msg("DeepSeek", e)) from e

    async def _complete_opencode(
        self,
        messages: List[dict],
        system_prompt: str,
        model: str,
    ) -> Tuple[str, int, int]:
        """Call OpenCode Zen via OpenAI-compatible endpoint (free tier)."""
        if not self.opencode_client:
            raise ValueError("OpenCode Zen not configured — set OPENCODE_KEY")
        all_messages = [{"role": "system", "content": system_prompt}] + messages

        try:
            response = await self.opencode_client.chat.completions.create(
                model=model or settings.PRIMARY_LLM,
                messages=all_messages,
                temperature=0.1,
                max_tokens=4096,
            )

            response_text = response.choices[0].message.content or ""
            tokens_in = response.usage.prompt_tokens if response.usage else 0
            tokens_out = response.usage.completion_tokens if response.usage else 0

            return response_text, tokens_in, tokens_out
        except (APIError, APIConnectionError) as e:
            logger.error(f"OpenCode Zen API error: {_clean_llm_error(e)}")
            raise RuntimeError(_llm_error_msg("OpenCode Zen", e)) from e

    async def _complete_gemini(
        self,
        messages: List[dict],
        system_prompt: str,
    ) -> Tuple[str, int, int]:
        """Call Google Gemini via the generateContent API."""
        import httpx

        api_key = settings.GEMINI_API_KEY
        if not api_key:
            raise ValueError("GEMINI_API_KEY not configured")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"

        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            content_text = msg.get("content", "")
            if isinstance(content_text, str):
                contents.append(
                    {
                        "role": role,
                        "parts": [{"text": f"[{msg['role']}] {content_text}"}],
                    }
                )

        body = {
            "contents": contents,
            "systemInstruction": {
                "parts": [{"text": system_prompt}],
            },
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 4096,
            },
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=body)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Gemini API error {resp.status_code}: {resp.text[:300]}"
                )

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return "", 0, 0

            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            response_text = "".join(p.get("text", "") for p in parts)

            usage = data.get("usageMetadata", {})
            tokens_in = usage.get("promptTokenCount", 0)
            tokens_out = usage.get("candidatesTokenCount", 0)

            return response_text, tokens_in, tokens_out

    async def _complete_azure(
        self,
        messages: List[dict],
        system_prompt: str,
    ) -> Tuple[str, int, int]:
        """Call Azure OpenAI (GPT-4o) for enterprise-grade responses."""
        if not self.azure_client:
            raise ValueError("Azure OpenAI not configured")

        all_messages = [{"role": "system", "content": system_prompt}] + messages

        try:
            response = await self.azure_client.chat.completions.create(
                model=settings.AZURE_OPENAI_DEPLOYMENT,
                messages=all_messages,
                temperature=0.1,
                max_tokens=4096,
            )

            response_text = response.choices[0].message.content or ""
            tokens_in = response.usage.prompt_tokens if response.usage else 0
            tokens_out = response.usage.completion_tokens if response.usage else 0

            return response_text, tokens_in, tokens_out
        except (APIError, APIConnectionError) as e:
            logger.error(f"Azure OpenAI API error: {_clean_llm_error(e)}")
            raise RuntimeError(_llm_error_msg("Azure OpenAI", e)) from e

    async def _complete_openrouter(
        self,
        messages: List[dict],
        system_prompt: str,
        model: str,
    ) -> Tuple[str, int, int]:
        """Call OpenRouter — OpenAI-compatible API for free/cheap models."""
        if not self.openrouter_client:
            raise ValueError("OpenRouter not configured — set OPENROUTER_API_KEY")
        all_messages = [{"role": "system", "content": system_prompt}] + messages

        try:
            response = await self.openrouter_client.chat.completions.create(
                model=model,
                messages=all_messages,
                temperature=0.1,
                max_tokens=4096,
            )

            response_text = response.choices[0].message.content or ""
            tokens_in = response.usage.prompt_tokens if response.usage else 0
            tokens_out = response.usage.completion_tokens if response.usage else 0

            return response_text, tokens_in, tokens_out
        except (APIError, APIConnectionError) as e:
            logger.error(f"OpenRouter API error: {_clean_llm_error(e)}")
            raise RuntimeError(_llm_error_msg("OpenRouter", e)) from e

    async def _complete_litellm(
        self,
        messages: List[dict],
        system_prompt: str,
        model: str,
    ) -> Tuple[str, int, int]:
        """Call LiteLLM Gateway via its OpenAI-compatible API."""
        if not self.litellm_client:
            raise ValueError("LiteLLM Gateway not configured — set LITELLM_ENABLED=true")
        all_messages = [{"role": "system", "content": system_prompt}] + messages

        try:
            response = await self.litellm_client.chat.completions.create(
                model=model,
                messages=all_messages,
                temperature=0.1,
                max_tokens=4096,
            )

            response_text = response.choices[0].message.content or ""
            tokens_in = response.usage.prompt_tokens if response.usage else 0
            tokens_out = response.usage.completion_tokens if response.usage else 0

            return response_text, tokens_in, tokens_out
        except (APIError, APIConnectionError) as e:
            logger.error(f"LiteLLM Gateway API error: {_clean_llm_error(e)}")
            raise RuntimeError(_llm_error_msg("LiteLLM Gateway", e)) from e

    async def _complete_anthropic(
        self,
        messages: List[dict],
        system_prompt: str,
        model: str | None = None,
    ) -> Tuple[str, int, int]:
        """Call Anthropic Claude for premium responses."""
        # Convert messages to Anthropic format — system is passed separately
        anthropic_messages = []
        for msg in messages:
            anthropic_messages.append({"role": msg["role"], "content": msg["content"]})

        try:
            response = await self.anthropic_client.messages.create(
                model=model or settings.PREMIUM_LLM,
                system=system_prompt,
                messages=anthropic_messages,
                temperature=0.1,
                max_tokens=4096,
            )

            response_text = response.content[0].text if response.content else ""
            tokens_in = response.usage.input_tokens if response.usage else 0
            tokens_out = response.usage.output_tokens if response.usage else 0

            return response_text, tokens_in, tokens_out
        except anthropic.APIStatusError as e:
            logger.error(f"Anthropic API error: {_clean_llm_error(e)}")
            raise RuntimeError(_llm_error_msg("Anthropic", e)) from e

    async def _stream_deepseek(
        self,
        messages: List[dict],
        system_prompt: str,
        model: str = None,
    ) -> AsyncGenerator[str, None]:
        """Stream DeepSeek via OpenAI-compatible endpoint."""
        all_messages = [{"role": "system", "content": system_prompt}] + messages

        try:
            stream = await self.deepseek_client.chat.completions.create(
                model=model or settings.PRIMARY_LLM,
                messages=all_messages,
                temperature=0.1,
                max_tokens=4096,
                stream=True,
            )

            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except (APIError, APIConnectionError) as e:
            logger.error(f"DeepSeek streaming error: {_clean_llm_error(e)}")
            raise RuntimeError(_llm_error_msg("DeepSeek", e)) from e

    async def _stream_opencode(
        self,
        messages: List[dict],
        system_prompt: str,
        model: str,
    ) -> AsyncGenerator[str, None]:
        """Stream OpenCode Zen via OpenAI-compatible endpoint."""
        if not self.opencode_client:
            raise ValueError("OpenCode Zen not configured — set OPENCODE_KEY")

        all_messages = [{"role": "system", "content": system_prompt}] + messages

        try:
            stream = await self.opencode_client.chat.completions.create(
                model=model or settings.PRIMARY_LLM,
                messages=all_messages,
                temperature=0.1,
                max_tokens=4096,
                stream=True,
            )

            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except (APIError, APIConnectionError) as e:
            logger.error(f"OpenCode Zen streaming error: {_clean_llm_error(e)}")
            raise RuntimeError(_llm_error_msg("OpenCode Zen", e)) from e

    async def _stream_azure(
        self,
        messages: List[dict],
        system_prompt: str,
    ) -> AsyncGenerator[str, None]:
        """Stream Azure OpenAI (GPT-4o)."""
        if not self.azure_client:
            raise ValueError("Azure OpenAI not configured")

        all_messages = [{"role": "system", "content": system_prompt}] + messages

        try:
            stream = await self.azure_client.chat.completions.create(
                model=settings.AZURE_OPENAI_DEPLOYMENT,
                messages=all_messages,
                temperature=0.1,
                max_tokens=4096,
                stream=True,
            )

            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except (APIError, APIConnectionError) as e:
            logger.error(f"Azure OpenAI streaming error: {_clean_llm_error(e)}")
            raise RuntimeError(_llm_error_msg("Azure OpenAI", e)) from e

    async def _stream_openrouter(
        self,
        messages: List[dict],
        system_prompt: str,
        model: str,
    ) -> AsyncGenerator[str, None]:
        """Stream OpenRouter."""
        if not self.openrouter_client:
            raise ValueError("OpenRouter not configured — set OPENROUTER_API_KEY")

        all_messages = [{"role": "system", "content": system_prompt}] + messages

        try:
            stream = await self.openrouter_client.chat.completions.create(
                model=model,
                messages=all_messages,
                temperature=0.1,
                max_tokens=4096,
                stream=True,
            )

            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except (APIError, APIConnectionError) as e:
            logger.error(f"OpenRouter streaming error: {_clean_llm_error(e)}")
            raise RuntimeError(_llm_error_msg("OpenRouter", e)) from e

    async def _stream_litellm(
        self,
        messages: List[dict],
        system_prompt: str,
        model: str,
    ) -> AsyncGenerator[str, None]:
        """Stream from LiteLLM Gateway."""
        if not self.litellm_client:
            raise ValueError("LiteLLM Gateway not configured — set LITELLM_ENABLED=true")

        all_messages = [{"role": "system", "content": system_prompt}] + messages

        try:
            stream = await self.litellm_client.chat.completions.create(
                model=model,
                messages=all_messages,
                temperature=0.1,
                max_tokens=4096,
                stream=True,
            )

            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except (APIError, APIConnectionError) as e:
            logger.error(f"LiteLLM Gateway streaming error: {_clean_llm_error(e)}")
            raise RuntimeError(_llm_error_msg("LiteLLM Gateway", e)) from e

    async def _stream_anthropic(
        self,
        messages: List[dict],
        system_prompt: str,
        model: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream Anthropic Claude."""
        anthropic_messages = []
        for msg in messages:
            anthropic_messages.append({"role": msg["role"], "content": msg["content"]})

        try:
            async with self.anthropic_client.messages.stream(
                model=model or settings.PREMIUM_LLM,
                system=system_prompt,
                messages=anthropic_messages,
                temperature=0.1,
                max_tokens=4096,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except anthropic.APIStatusError as e:
            logger.error(f"Anthropic streaming error: {_clean_llm_error(e)}")
            raise RuntimeError(_llm_error_msg("Anthropic", e)) from e
