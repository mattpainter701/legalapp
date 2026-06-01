from typing import List, Tuple

import anthropic
from openai import AsyncOpenAI

from app.config import get_settings

settings = get_settings()

SYSTEM_PROMPT_TEMPLATE = """You are a legal research assistant for {tenant_name}. You help attorneys research case law and draft legal documents.

RULES:
- When the CONTEXT below contains relevant legal sources, cite the exact case name and legal citation.
- Tag claims with confidence: [settled] for well-established law, [verify] for check-before-relying, [model knowledge] for general reasoning without a specific source.
- If the CONTEXT is empty or contains no relevant sources, you may answer from general knowledge but mark with [model knowledge].
- Do NOT predict case outcomes or provide specific legal advice. Always recommend consulting a qualified attorney.
- Use a formal, authoritative tone.
- End every response with: "\\n\\n*This is not legal advice. Please consult a qualified attorney.*"

CONTEXT (may be empty):
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
        provider: str = "default",
    ) -> Tuple[str, int, int]:
        """
        Generate a completion.
        Returns (response_text, tokens_in, tokens_out).

        Provider routing:
          - "gemini" → Google Gemini
          - "azure"  → Azure OpenAI (GPT-4o)
          - premium/Claude → Anthropic
          - default → DeepSeek
        """
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            tenant_name=tenant_name,
            context=context,
        )

        if provider == "gemini":
            return await self._complete_gemini(messages, system_prompt)
        if provider == "azure":
            return await self._complete_azure(messages, system_prompt)

        if use_premium:
            model = settings.PREMIUM_LLM.lower()
            if model.startswith("claude") or model.startswith("anthropic"):
                return await self._complete_anthropic(messages, system_prompt)
            return await self._complete_deepseek(
                messages, system_prompt, model=settings.PREMIUM_LLM
            )
        else:
            return await self._complete_deepseek(messages, system_prompt)

    async def _complete_deepseek(
        self,
        messages: List[dict],
        system_prompt: str,
        model: str = None,
    ) -> Tuple[str, int, int]:
        """Call DeepSeek via OpenAI-compatible endpoint."""
        all_messages = [{"role": "system", "content": system_prompt}] + messages

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

    async def _complete_anthropic(
        self,
        messages: List[dict],
        system_prompt: str,
    ) -> Tuple[str, int, int]:
        """Call Anthropic Claude for premium responses."""
        # Convert messages to Anthropic format — system is passed separately
        anthropic_messages = []
        for msg in messages:
            anthropic_messages.append({"role": msg["role"], "content": msg["content"]})

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
