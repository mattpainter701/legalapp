from typing import List, Tuple
import anthropic
from openai import AsyncOpenAI

from app.config import get_settings

settings = get_settings()

SYSTEM_PROMPT_TEMPLATE = """You are a legal research assistant for {tenant_name}. You help attorneys research case law and draft legal documents.

RULES:
- Answer ONLY using the provided legal sources in the context below.
- ALWAYS cite the exact case name and legal citation for every substantive statement.
- If the provided sources do not support an answer, respond with: "I could not find relevant authority in the provided materials."
- Do NOT predict case outcomes or provide legal advice.
- Use a formal, authoritative tone.
- End every response with: "\\n\\n*This is not legal advice. Please consult a qualified attorney.*"

CONTEXT (legal sources retrieved from database):
{context}
"""


class LLMService:
    def __init__(self):
        # DeepSeek uses OpenAI-compatible API
        self.deepseek_client = AsyncOpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
        )
        # Anthropic client for premium tier
        self.anthropic_client = anthropic.AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY,
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
        use_premium: bool = False,
    ) -> Tuple[str, int, int]:
        """
        Generate a completion.
        Returns (response_text, tokens_in, tokens_out).
        """
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            tenant_name=tenant_name,
            context=context,
        )

        if use_premium:
            return await self._complete_anthropic(messages, system_prompt)
        else:
            return await self._complete_deepseek(messages, system_prompt)

    async def _complete_deepseek(
        self,
        messages: List[dict],
        system_prompt: str,
    ) -> Tuple[str, int, int]:
        """Call DeepSeek via OpenAI-compatible endpoint."""
        all_messages = [{"role": "system", "content": system_prompt}] + messages

        response = await self.deepseek_client.chat.completions.create(
            model=settings.PRIMARY_LLM,
            messages=all_messages,
            temperature=0.1,
            max_tokens=4096,
        )

        response_text = response.choices[0].message.content or ""
        tokens_in = response.usage.prompt_tokens if response.usage else 0
        tokens_out = response.usage.completion_tokens if response.usage else 0

        return response_text, tokens_in, tokens_out

    async def _complete_anthropic(
        self,
        messages: List[dict],
        system_prompt: str,
    ) -> Tuple[str, int, int]:
        """Call Anthropic Claude for premium responses."""
        # Convert messages to Anthropic format — system is passed separately
        anthropic_messages = []
        for msg in messages:
            anthropic_messages.append(
                {"role": msg["role"], "content": msg["content"]}
            )

        response = await self.anthropic_client.messages.create(
            model=settings.PREMIUM_LLM,
            system=system_prompt,
            messages=anthropic_messages,
            temperature=0.1,
            max_tokens=4096,
        )

        response_text = response.content[0].text if response.content else ""
        tokens_in = response.usage.input_tokens if response.usage else 0
        tokens_out = response.usage.output_tokens if response.usage else 0

        return response_text, tokens_in, tokens_out
